import json
import uuid
from datetime import UTC, datetime, timedelta

import structlog.stdlib
from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agentic.loop import run_tool_loop
from app.agentic.models import ProgramStatus, Stage
from app.agentic.state import ProgramState, is_approval_message
from app.agentic.store import ProgramStateStore
from app.agentic.tools import INTAKE_TOOLS, ToolContext, execute_tool, snapshot_state
from app.cache import get_deletion_key, get_generation_key, get_redis_client
from app.harness import HARNESS_STRUCTURED_SCHEMA
from app.models.auth import User
from app.models.chat import (
    DEFAULT_CHAT_NAME,
    MAX_CHAT_NAME_LENGTH,
    ChatMessage,
    ChatSession,
    GenerationResult,
    GenerationResultType,
    UserRole,
)
from app.providers.containers import provider
from app.providers.models import (
    FunctionCall,
    GenerationResponse,
    HarnessStructuredOutput,
    Message,
    ResponseFormat,
)
from app.tasks.conf.broker import broker, tsq_db
from app.tasks.program import program_stage

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


def _load_program_state(raw: dict | None) -> ProgramState | None:
    """Parse the stored envelope, treating unusable data as "no programme"."""
    if raw is None:
        return None
    try:
        return ProgramState.model_validate(raw)
    except ValidationError as error:
        log.error("program_state_invalid", exc=error)
        return None


async def _reply(user_uuid: uuid.UUID, session_id: uuid.UUID, message: str) -> None:
    """Store an assistant message through the ordinary result pipeline."""
    payload = json.dumps({"chat_name": None, "message": message}, ensure_ascii=False)
    await process_response.kiq(user_uuid, session_id, payload)


async def _handle_program_turn(
    user: User,
    session_id: uuid.UUID,
    state: ProgramState,
    last_user_text: str,
) -> bool:
    """Answer a message aimed at a programme that is already in the pipeline.

    Returns ``True`` when the message was handled here. An approved programme is
    not handled: the chat continues as an ordinary conversation, which is what
    the next stage (#5) will use to discuss the full document.
    """
    if state.failed:
        # A run that is terminally broken is not handled here, so the model can
        # start a new one from the methodist's message.
        return False
    if state.status is ProgramStatus.GENERATING:
        await _reply(
            user.uuid,
            session_id,
            "Генерация программы ещё идёт. Черновик появится в чате, как только все блоки будут готовы.",
        )
        return True
    if state.status is ProgramStatus.PENDING_APPROVAL:
        if not state.finalized:
            await _reply(
                user.uuid,
                session_id,
                "Черновик ещё готовится: как только он будет записан в чат, его можно будет подтвердить.",
            )
            return True
        if not is_approval_message(last_user_text):
            await _reply(
                user.uuid,
                session_id,
                "Черновик программы ждёт подтверждения. Проверьте его и напишите «подтверждаю», чтобы утвердить.",
            )
            return True
        context = ToolContext(session_id=session_id, state=state)
        result = await execute_tool(context, FunctionCall(name="approve_program_draft", arguments="{}"))
        if not result.ok:
            await _reply(user.uuid, session_id, result.error or "Не удалось утвердить черновик.")
            return True
        snapshot = snapshot_state(context)
        if snapshot is not None:
            saved = await ProgramStateStore(tsq_db).save(snapshot, expected_run_id=state.meta.run_id)
            if saved is None:
                await _reply(user.uuid, session_id, "Не удалось сохранить утверждённый черновик. Попробуйте ещё раз.")
                return True
        await _reply(
            user.uuid,
            session_id,
            "Черновик утверждён. Он сохранён и будет основой следующего этапа полной генерации.",
        )
        return True
    return False


def _intake_response_format() -> ResponseFormat:
    return ResponseFormat(
        type=ResponseFormat.TYPE_JSON_SCHEMA,
        schema=HARNESS_STRUCTURED_SCHEMA,
        strict=True,
    )


async def _run_intake(
    message_history: list[Message],
    user_uuid: uuid.UUID,
    session_id: uuid.UUID,
    first_response: GenerationResponse | None,
    initial_state: ProgramState | None,
) -> tuple[str, ToolContext]:
    """Finish an intake reply, executing the tools the model asked for.

    The provider call that produced ``first_response`` already happened in the
    caller, because it carries the error branches the worker tests pin down. From
    here the loop continues with the same format and the intake tools until the
    model answers with JSON text.
    """
    context = ToolContext(session_id=session_id, state=initial_state)
    store = ProgramStateStore(tsq_db)

    async def checkpoint() -> None:
        if not context.dirty:
            return
        snapshot = snapshot_state(context)
        if snapshot is None:
            return
        saved = await store.save(snapshot)
        if saved is not None:
            context.state = saved
            context.dirty = False

    async def generate(messages: list[Message]):
        return await provider.generate(
            messages,
            _intake_response_format(),
            x_client_id=user_uuid,
            x_session_id=session_id,
            tools=INTAKE_TOOLS,
        )

    text = await run_tool_loop(
        message_history,
        generate=generate,
        call_tool=lambda call: execute_tool(context, call.function),
        checkpoint=checkpoint,
        first_response=first_response,
    )
    if context.started and context.state is not None:
        snapshot = snapshot_state(context)
        if snapshot is not None:
            saved = await store.save(snapshot)
            if saved is not None:
                context.state = saved
                await program_stage.kiq(session_id, Stage.COMPETENCES, saved.meta.run_id)
    return text, context


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
        session_row = await db.get(ChatSession, session)
        raw_program_state = session_row.program_state if session_row is not None else None
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
            last_user_text = messages[-1].content[0].text if messages[-1].content else ""
            program_state = _load_program_state(raw_program_state)

        if program_state is not None and await _handle_program_turn(user, session, program_state, last_user_text):
            return

        try:
            response = await provider.generate(
                messages,
                _intake_response_format(),
                x_client_id=user.uuid,
                x_session_id=session,
                tools=INTAKE_TOOLS,
            )
            log.debug("text_generation_response", response=response)
            if response is None:
                raise ValueError("null_response")
        except Exception as e:
            log.error("generate_chat_message_error", session=session, error=e)
            async with tsq_db() as db:
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
            if not response.messages or (not response.tool_calls and not response.messages[0].content):
                log.error("empty_response", session=session)
                await _add_error_result(db, session, "Provider did not respond properly", user.uuid)
                return
            text = response.messages[0].content[0].text if response.messages and response.messages[0].content else ""

        if response.tool_calls:
            text, _context = await _run_intake(messages, user.uuid, session, response, program_state)

        # If the session got deleted during worker execution, don't add response there.
        # Saving result is fine though, it will expire anyway
        async with get_redis_client() as redis_client:
            if await redis_client.delete(get_deletion_key(session)):
                log.warning("execution_cancelled", session=session)
                return
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
                if session is not None and session.name == DEFAULT_CHAT_NAME:
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
