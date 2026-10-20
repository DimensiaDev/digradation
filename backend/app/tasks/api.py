import json
import uuid
from datetime import UTC, datetime, timedelta

import structlog.stdlib
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import get_deletion_key, get_generation_key, get_redis_client
from app.harness import HARNESS_STRUCTURED_SCHEMA
from app.models.auth import User
from app.models.chat import (
    MAX_CHAT_NAME_LENGTH,
    ChatMessage,
    ChatSession,
    GenerationResult,
    GenerationResultType,
    UserRole,
)
from app.providers.containers import provider
from app.providers.models import (
    HarnessStructuredOutput,
    Message,
    ResponseFormat,
)
from app.tasks.conf.broker import broker, tsq_db

log = structlog.stdlib.get_logger(__name__)


async def _add_error_result(db: AsyncSession, session: uuid.UUID, error_msg: str, user_uuid: uuid.UUID):
    db.add(GenerationResult(chat_session_id=session, type=GenerationResultType.ERROR, content=error_msg))
    await db.commit()
    async with get_redis_client() as redis:
        await redis.delete(get_generation_key(user_uuid))


def _history_up_to_last_user_message(rows: list[ChatMessage]) -> list[ChatMessage]:
    """Cut the stored history right after the last user message.

    A generation request has to end with something the user said, otherwise the
    model has nothing to answer. When a turn is retried the assistant's own
    previous answer is still the last row of the table, and passing it back makes
    the provider continue that message instead of replying to the user.
    """
    for index in range(len(rows) - 1, -1, -1):
        if rows[index].role == UserRole.USER:
            return rows[: index + 1]
    return []


@broker.task(queue_name="default", schedule=[{"interval": timedelta(hours=1)}])
async def cleanup_old_results():
    log.info("old_results_cleanup_start")
    async with tsq_db() as db:
        res = await db.execute(
            delete(GenerationResult).where(GenerationResult.timestamp < datetime.now(tz=UTC) - timedelta(hours=1))
        )
        await db.commit()
        log.info("old_results_cleanup_ok", count=res.rowcount)


@broker.task(queue_name="network")
async def generate_chat_message(session: uuid.UUID):
    async with tsq_db() as db:
        await db.execute(delete(GenerationResult).where(GenerationResult.chat_session_id == session))
        await db.commit()

        user = await db.scalar(select(User).join(User.chat_sessions).where(ChatSession.session_id == session).limit(1))
        if user is None:
            log.error("no_user_for_session", session=session)
            await _add_error_result(db, session, "Invalid message session", uuid.uuid4())
            return
        key = get_generation_key(user.uuid)
        async with get_redis_client() as redis_client:
            acquired = await redis_client.set(key, "1", ex=300, nx=True)
            if not acquired:
                log.error("generate_chat_message_already_running", session=session)
                return
    try:
        async with tsq_db() as db:
            messages_data = await db.scalars(
                select(ChatMessage).where(ChatMessage.chat_session_id == session).order_by(ChatMessage.id)
            )
            rows = list(messages_data)
            messages = [Message.from_chat_message(msg) for msg in _history_up_to_last_user_message(rows)]
            if not messages:
                log.error("no_messages_for_session", session=session)
                await _add_error_result(db, session, "Invalid message session: no messages to send", user.uuid)
                return

            log.debug("text_message_generation", last_message=messages[-1])

        try:
            response = await provider.generate(
                messages,
                ResponseFormat(
                    type=ResponseFormat.TYPE_JSON_SCHEMA,
                    schema=HARNESS_STRUCTURED_SCHEMA,
                    strict=True,
                ),
                x_client_id=user.uuid,
                x_session_id=session,
            )
            log.debug("text_generation_response", response=response)
            if response is None:
                raise ValueError("null_response")
        except Exception as e:
            log.error("generate_chat_message_error", session=session, error=e)
            await _add_error_result(db, session, "Internal server error occurred", user.uuid)
            return

        async with tsq_db() as db:
            if not response:
                result = GenerationResult(
                    chat_session_id=session,
                    type=GenerationResultType.ERROR,
                    content="Internal server error occurred, please try again later or contact support.",
                )
                db.add(result)
                await db.commit()
                return
            if not response.messages or not response.messages[0].content:
                log.error("empty_response", session=session)
                await _add_error_result(db, session, "Provider did not respond properly", user.uuid)
                return

            # If the session got deleted during worker execution, don't add response there.
            # Saving result is fine though, it will expire anyway
            async with get_redis_client() as redis_client:
                if await redis_client.delete(get_deletion_key(session)):
                    log.warning("execution_cancelled", session=session)
                    return
            text = response.messages[0].content[0].text
        await process_response.kiq(user.uuid, session, text)
    except Exception as e:
        log.error("generation_unknown_exception", exc=e)
        async with tsq_db() as db:
            await _add_error_result(db, session, "Internal server error occurred", user.uuid)
        async with get_redis_client() as redis_client:
            await redis_client.delete(key)


@broker.task(queue_name="default")
async def process_response(user_uuid: uuid.UUID, session_id: uuid.UUID, response: str):
    redis_generation_key = get_generation_key(user_uuid)
    try:
        data = json.loads(response)
        output = HarnessStructuredOutput.model_validate(data)

        async with tsq_db() as db:
            update_name = None
            if output.chat_name:
                session = await db.get(ChatSession, session_id)
                if session is not None and session.name == "Новый чат":
                    chat_name = output.chat_name
                    if len(chat_name) > MAX_CHAT_NAME_LENGTH:
                        chat_name = chat_name[: MAX_CHAT_NAME_LENGTH - 3].rstrip() + "..."
                    session.name = chat_name
                    update_name = chat_name

            result = GenerationResult(
                chat_session_id=session_id,
                type=GenerationResultType.SUCCESS,
                content=output.message,
                update_name=update_name,
            )
            db.add(result)
            db.add(
                ChatMessage(
                    chat_session_id=session_id,
                    role=UserRole.ASSISTANT,
                    content=response,
                    display_text=output.message,
                )
            )
            await db.commit()
    except Exception as e:
        log.error("processing_unknown_exception", exc=e)
        async with tsq_db() as db:
            await _add_error_result(db, session_id, "Internal server error occurred", user_uuid)
    finally:
        async with get_redis_client() as redis:
            await redis.delete(redis_generation_key)
