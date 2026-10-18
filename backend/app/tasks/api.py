import uuid
from datetime import UTC, datetime, timedelta

import structlog.stdlib
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agentic.loop import run_tool_loop
from app.agentic.tools import AGENT_TOOL, ToolContext, execute_tool
from app.cache import get_deletion_key, get_generation_key, get_redis_client
from app.models.auth import User
from app.models.chat import (
    ChatMessage,
    ChatSession,
    GenerationResult,
    GenerationResultType,
    UserRole,
)
from app.providers.containers import provider
from app.providers.models import FunctionCall, Message, ToolConfig
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

        # The draft lives in the database, not in the transcript, so the handlers
        # open their own short-lived sessions through `tsq_db`.
        tool_context = ToolContext(session_id=session, open_db=tsq_db)

        async def generate(history: list[Message]):
            return await provider.generate(
                history,
                x_client_id=user.uuid,
                x_session_id=session,
                tools=[AGENT_TOOL],
                tool_config=ToolConfig(mode="auto"),
            )

        async def call_tool(call: FunctionCall):
            return await execute_tool(tool_context, call)

        try:
            response = await run_tool_loop(messages, generate=generate, call_tool=call_tool)
            log.debug("text_generation_response", response=response)
        except Exception as e:
            log.error("generate_chat_message_error", session=session, error=e)
            await _add_error_result(db, session, "Internal server error occurred", user.uuid)
            return

        if not response:
            log.error("empty_response", session=session)
            await _add_error_result(db, session, "Provider did not respond properly", user.uuid)
            return

        # If the session got deleted during worker execution, don't add response there.
        # Saving result is fine though, it will expire anyway
        async with get_redis_client() as redis_client:
            if await redis_client.delete(get_deletion_key(session)):
                log.warning("execution_cancelled", session=session)
                return
        await process_response.kiq(user.uuid, session, response, tool_context.renamed_to)
    except Exception as e:
        log.error("generation_unknown_exception", exc=e)
        async with tsq_db() as db:
            await _add_error_result(db, session, "Internal server error occurred", user.uuid)
        async with get_redis_client() as redis_client:
            await redis_client.delete(key)


@broker.task(queue_name="default")
async def process_response(user_uuid: uuid.UUID, session_id: uuid.UUID, response: str, update_name: str | None = None):
    """Store the assistant's answer and release the generation lock.

    The reply is plain text: the agent answers through the functions of
    ``app.agentic.tools``, and the function-calling rounds of one reply are not
    part of the transcript. ``update_name`` arrives already truncated from the
    ``save_program_draft`` handler, which is the only place that names a chat.
    """
    redis_generation_key = get_generation_key(user_uuid)
    try:
        async with tsq_db() as db:
            db.add(
                GenerationResult(
                    chat_session_id=session_id,
                    type=GenerationResultType.SUCCESS,
                    content=response,
                    update_name=update_name,
                )
            )
            db.add(
                ChatMessage(
                    chat_session_id=session_id,
                    role=UserRole.ASSISTANT,
                    content=response,
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
