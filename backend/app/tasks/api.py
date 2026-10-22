import uuid
from datetime import UTC, datetime, timedelta

import structlog.stdlib
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agentic import store
from app.agentic.errors import AnswersDoNotMatch
from app.agentic.loop import run_tool_loop
from app.agentic.models import ProgramStatus, StageStatus
from app.agentic.state import ClarificationAnswer, ProgramState, apply_answers
from app.agentic.tools import INTAKE_TOOLS, ToolContext, call_executor
from app.cache import get_deletion_key, get_generation_key, get_redis_client
from app.models.auth import User
from app.models.chat import (
    ChatMessage,
    ChatSession,
    GenerationResult,
    GenerationResultType,
    UserRole,
    write_result,
)
from app.naming import first_user_message, resolve_chat_name
from app.providers.containers import provider
from app.providers.models import Message, ResponseFormat
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
async def run_chat_turn(session: uuid.UUID, answers: list[dict[str, str]] | None = None) -> None:
    """One turn of a chat: the model's answer, and whatever the tools changed.

    The turn is a tool loop, not a single completion: the model may start a
    programme, approve a rendered draft, or answer the methodist in prose. The
    answers the methodist supplied for the open questions are applied before the
    loop, so the model sees a programme that is already under way.
    """
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
                log.error("run_chat_turn_already_running", session=session)
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

        state = await store.load(session)
        if answers:
            try:
                state = await _start_with_answers(state, answers, session)
            except AnswersDoNotMatch as error:
                # Another turn applied the same answers first. The run has moved
                # on, and this turn continues without them — it must not report
                # an error over a turn that succeeded.
                log.info("program_answers_already_applied", session=str(session), reason=str(error))

        ctx = ToolContext(session_id=session, state=state)
        offered = INTAKE_TOOLS

        async def generate(history: list[Message]):
            return await provider.generate(
                history,
                ResponseFormat(type=ResponseFormat.TYPE_TEXT),
                x_client_id=user.uuid,
                x_session_id=session,
                tools=offered,
            )

        async def checkpoint() -> None:
            """Save what a tool changed, so a turn that dies afterwards keeps it.

            Without this a model that started a programme and then failed to
            answer would lose the programme: the tool's effect would only have
            been in memory.
            """
            if ctx.dirty:
                await _commit_tool_state(ctx, state, session)
                ctx.dirty = False

        try:
            reply = await run_tool_loop(
                messages,
                generate=generate,
                call_tool=call_executor(ctx, offered),
                checkpoint=checkpoint,
            )
        except Exception as e:
            log.error("run_chat_turn_error", session=session, error=e)
            async with tsq_db() as db:
                await _add_error_result(db, session, "Internal server error occurred", user.uuid)
            return

        if not reply.strip():
            log.error("empty_response", session=session)
            async with tsq_db() as db:
                await _add_error_result(db, session, "Provider did not respond properly", user.uuid)
            return

        await _commit_tool_state(ctx, state, session)

        # If the session got deleted during worker execution, don't add response there.
        # Saving result is fine though, it will expire anyway
        async with get_redis_client() as redis_client:
            if await redis_client.delete(get_deletion_key(session)):
                log.warning("execution_cancelled", session=session)
                return
        await process_response.kiq(user.uuid, session, reply)
    except Exception as e:
        log.error("generation_unknown_exception", exc=e)
        async with tsq_db() as db:
            await _add_error_result(db, session, "Internal server error occurred", user.uuid)
        async with get_redis_client() as redis:
            await redis.delete(key)


async def _start_with_answers(
    state: ProgramState | None, answers: list[dict[str, str]], session: uuid.UUID
) -> ProgramState:
    """Apply the methodist's answers and let the programme start.

    The answers are checked against the questions the run is waiting for, so a
    client cannot answer a question nobody asked.
    """
    if state is None:
        raise AnswersDoNotMatch("в этом чате нет программы")
    answered = apply_answers(state, [ClarificationAnswer.model_validate(answer) for answer in answers])
    saved = await store.save(answered, expected_run_id=answered.run_id)
    log.info("program_answers_applied", session=str(session), run_id=str(saved.run_id))
    await program_stage.kiq(session, saved.current_stage.value, saved.run_id)
    return saved


async def _commit_tool_state(ctx: ToolContext, previous: ProgramState | None, session: uuid.UUID) -> None:
    """Save what the tools changed, and put the next stage on the queue.

    Three things start work: a programme the model has just begun, the
    approval of a skeleton — the second one is what releases the content — and
    a change the methodist asked for, which reruns one stage.
    """
    if not ctx.dirty or ctx.state is None:
        return
    started = previous is None or ctx.state.run_id != previous.run_id
    saved = await store.save(ctx.state) if started else await store.save(ctx.state, expected_run_id=ctx.state.run_id)
    log.info("program_state_committed", session=str(session), run_id=str(saved.run_id), status=saved.status.value)
    if started and saved.status is ProgramStatus.GENERATING:
        await program_stage.kiq(session, saved.current_stage.value, saved.run_id)
    elif not started and saved.revision is not None:
        # The methodist asked for a change in one part: that part is redone.
        await program_stage.kiq(session, saved.revision.stage.value, saved.run_id)
    elif not started and saved.status is ProgramStatus.GENERATING:
        # The methodist approved the skeleton: the content may be written now.
        # The attempt is spent here, so the stage has the same budget as every
        # other one, and a stage that is already complete is not run again.
        stage = saved.current_stage
        if saved.stage_status(stage) is StageStatus.COMPLETED:
            return
        queued = await store.save(saved.enqueued(stage), expected_run_id=saved.run_id)
        await program_stage.kiq(session, queued.current_stage.value, queued.run_id)


@broker.task(queue_name="default")
async def process_response(user_uuid: uuid.UUID, session_id: uuid.UUID, response: str):
    """Publish the turn's answer: the result row, the chat message and the name."""
    redis_generation_key = get_generation_key(user_uuid)
    try:
        async with tsq_db() as db:
            update_name = None
            session = await db.get(ChatSession, session_id)
            if session is not None:
                # A programme takes the name of its title when it is rendered.
                # Until then the chat takes the name of the first thing the
                # methodist said, and the finalise replaces it with the title.
                first = await first_user_message(db, session_id)
                update_name = resolve_chat_name(session.name, first_user_message=first)
                if update_name is not None:
                    session.name = update_name

            await write_result(db, session_id, response, update_name=update_name)
            db.add(
                ChatMessage(
                    chat_session_id=session_id,
                    role=UserRole.ASSISTANT,
                    content=response,
                    display_text=response,
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
