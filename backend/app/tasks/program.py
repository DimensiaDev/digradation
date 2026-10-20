"""The sequential programme pipeline: one task per stage.

A stage does one thing. It asks the model for one block, commits the block when
the model saves it, marks the stage complete and enqueues what follows. Stages are
separate tasks, not a loop inside one task, and the state in the database is the
only thing that passes between them — no worker memory, no transcript, nothing
else. That is what lets a worker die at any point without losing the blocks it
already committed: the next task reads them and carries on.

Failure has three outcomes, and the difference between them matters. A provider
or tool failure spends one of the stage's attempts and enqueues the same stage
again. Spending the last attempt ends the run and tells the methodist, because a
retry would fail the same way. A worker that vanished without failing anything is
noticed by :func:`recover_stalled_programs`, which enqueues the stage the run
stopped on — otherwise nothing would ever resume it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import structlog.stdlib
from sqlalchemy import select

from app.agentic import store
from app.agentic.loop import (
    AnswerTruncated,
    ProviderDidNotRespond,
    ToolCallFailed,
    ToolLoopExceeded,
    run_tool_loop,
)
from app.agentic.models import ProgramStatus, Stage, StageStatus
from app.agentic.prompts import stage_messages
from app.agentic.render import render_draft
from app.agentic.state import MAX_STAGE_ATTEMPTS, IncompleteDraft, ProgramState
from app.agentic.tools import ToolContext, call_executor, stage_tools
from app.cache import PROGRAM_STAGE_LOCK_TTL, get_program_stage_lock_key, get_redis_client
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
from app.providers.models import GenerationResponse, Message, ResponseFormat
from app.tasks.conf.broker import broker, tsq_db

log = structlog.stdlib.get_logger(__name__)

#: How long a run may go untouched before the recovery cron treats its stage as
#: abandoned. Longer than a stage should take for every provider the project
#: supports, and shorter than a methodist waits before writing again.
STALLED_AFTER = timedelta(minutes=10)

#: Releases a stage lock only if it still holds the token of the worker that took
#: it. A plain ``DEL`` after a ``GET`` would remove a lock that expired and was
#: taken by another worker in between, which is exactly the overlap the lock
#: exists to prevent.
_UNLOCK = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""


@broker.task(queue_name="network")
async def program_stage(session_id: uuid.UUID, stage: str, run_id: uuid.UUID) -> None:
    """Run one stage of the programme a chat is building.

    ``run_id`` is in the signature, not read from the row, so a stage task that
    arrives after the methodist started a new run refuses itself instead of
    writing a block into the new document.
    """
    stage_value = Stage(stage)
    stage_log = log.bind(chat_session_id=str(session_id), stage=stage, run_id=str(run_id))
    state = await store.load(session_id)
    if state is None or state.run_id != run_id:
        stage_log.info("program_stage_stale", reason="another run or no programme")
        return
    if state.failed or state.terminal:
        stage_log.info("program_stage_closed", reason="the run is over")
        return
    if state.stage_status(stage_value) is StageStatus.COMPLETED:
        # The worker died between committing the stage and enqueueing its
        # successor. Hand over instead of paying for the same block twice.
        await _advance(state)
        return
    if state.status is not ProgramStatus.GENERATING:
        stage_log.info("program_stage_not_generating", status=state.status.value)
        return

    token = await _lock(session_id, stage_value)
    if token is None:
        stage_log.info("program_stage_locked", reason="another worker holds the stage")
        return
    try:
        await _run_stage(session_id, state, stage_value, stage_log)
    finally:
        await _unlock(session_id, stage_value, token)


async def _run_stage(
    session_id: uuid.UUID,
    state: ProgramState,
    stage: Stage,
    stage_log: structlog.stdlib.BoundLogger,
) -> None:
    """One model turn with one job: fill the stage's block and commit it."""
    user_uuid = await _user_uuid(session_id)
    if user_uuid is None:
        stage_log.error("program_stage_no_user")
        return
    started = await store.save(state.started(stage), expected_run_id=state.run_id)
    offered = stage_tools(stage)
    ctx = ToolContext(
        session_id=session_id,
        state=started,
        known_source_ids={source.id for source in started.sources},
    )

    async def generate(messages: list[Message]) -> GenerationResponse | None:
        return await provider.generate(
            messages,
            ResponseFormat(type=ResponseFormat.TYPE_TEXT),
            x_client_id=user_uuid,
            x_session_id=session_id,
            tools=offered,
        )

    async def checkpoint() -> None:
        if ctx.dirty:
            ctx.state = await store.save(ctx.state, expected_run_id=state.run_id)
            ctx.dirty = False

    try:
        await run_tool_loop(
            stage_messages(started),
            generate=generate,
            call_tool=call_executor(ctx, offered),
            checkpoint=checkpoint,
        )
    except (ProviderDidNotRespond, ToolCallFailed, AnswerTruncated, ToolLoopExceeded) as error:
        await _stage_failed(ctx.state or started, stage, _reason(error), stage_log)
        return
    except store.StaleState:
        stage_log.warning("program_stage_superseded", reason="the row moved on")
        return
    except store.MissingChat:
        stage_log.info("program_stage_chat_deleted")
        return

    if not ctx.saved or ctx.state is None:
        await _stage_failed(ctx.state or started, stage, "модель не сохранила блок этапа", stage_log)
        return
    saved = await store.save(ctx.state.completed(stage), expected_run_id=state.run_id)
    stage_log.info("program_stage_completed", version=saved.version)
    await _advance(saved)


async def _stage_failed(
    state: ProgramState,
    stage: Stage,
    reason: str,
    stage_log: structlog.stdlib.BoundLogger,
) -> None:
    """Spend an attempt, then either retry the stage or end the run."""
    failed = state.failed_stage(stage, reason)
    if failed.terminal:
        saved = await store.save(failed, expected_run_id=state.run_id)
        stage_log.error("program_stage_gave_up", error=reason)
        await _notify(saved.session_id, _failure_message(reason))
        return
    retry = failed.enqueued(stage)
    saved = await store.save(retry, expected_run_id=state.run_id)
    stage_log.warning("program_stage_retry", attempt=saved.attempts.get(stage.value), error=reason)
    await program_stage.kiq(saved.session_id, stage.value, saved.run_id)


def _reason(error: Exception) -> str:
    """The failure as the methodist is told about it."""
    match error:
        case ProviderDidNotRespond():
            return "провайдер не ответил"
        case AnswerTruncated():
            return "ответ модели обрезан по длине"
        case ToolLoopExceeded():
            return "модель не смогла завершить блок"
        case ToolCallFailed():
            return "модель вернула некорректный вызов инструмента"
    return str(error)


def _failure_message(reason: str) -> str:
    return (
        f"Не удалось собрать программу: {reason}. Черновик не готов. "
        "Напишите, что изменить, и попросите начать заново — программа будет собрана с нуля."
    )


@broker.task(queue_name="default")
async def finalize_program_draft(session_id: uuid.UUID, run_id: uuid.UUID) -> None:
    """Assemble the committed blocks, render the draft and ask for confirmation.

    Finalising is idempotent by the state it commits: the row lock and the
    ``finalized`` flag inside it make a duplicate task return without rendering a
    second message. A draft that cannot be assembled ends the run — the same
    blocks would be refused again, so spending stage attempts on it would only
    delay the answer the methodist is owed.
    """
    finalize_log = log.bind(chat_session_id=str(session_id), run_id=str(run_id))
    state = await store.load(session_id)
    if state is None or state.run_id != run_id or state.failed or state.terminal or state.finalized:
        return
    if state.status is not ProgramStatus.PENDING_APPROVAL:
        finalize_log.info("program_finalize_not_ready", status=state.status.value)
        return
    try:
        draft = state.to_draft()
    except IncompleteDraft as error:
        failed = state.failed_run(f"черновик неполон: {'; '.join(error.problems)}")
        await store.save(failed, expected_run_id=run_id)
        finalize_log.error("program_draft_incomplete", problems=error.problems)
        await _notify(session_id, f"Не удалось собрать программу: {'; '.join(error.problems)}.")
        return

    message = render_draft(draft)
    name = _chat_name(draft.title)
    finalized = state.mark_finalized()
    try:
        async with store.locked(session_id) as (db, row):
            current = store.parse(row.program_state)
            if current is None or current.run_id != run_id or current.finalized:
                return
            row.program_state = store.dump(finalized)
            row.name = name
            db.add(
                GenerationResult(
                    chat_session_id=session_id,
                    type=GenerationResultType.SUCCESS,
                    content=message,
                    update_name=name,
                )
            )
            db.add(
                ChatMessage(
                    chat_session_id=session_id,
                    role=UserRole.ASSISTANT,
                    content=message,
                    display_text=message,
                )
            )
    except store.MissingChat:
        finalize_log.info("program_finalize_chat_deleted")
        return
    await store.mirror(finalized)
    finalize_log.info("program_draft_ready", title=draft.title)


@broker.task(queue_name="default", schedule=[{"interval": timedelta(minutes=5)}])
async def recover_stalled_programs() -> None:
    """Restart the stage of every run that stopped moving.

    ``program_stage`` is enqueued before it starts, so a worker that dies between
    the enqueue and the commit leaves a run marked as generating with nothing
    running. This task is what notices. It skips a stage whose Redis lock is still
    held, so a slow stage is never doubled, and it spends an attempt on the
    re-enqueue: a stage that kills its worker must end the run rather than cycle
    through the queue forever.
    """
    async with tsq_db() as db:
        rows = list(await db.scalars(select(ChatSession).where(ChatSession.program_state.is_not(None))))
    now = datetime.now(UTC)
    for row in rows:
        state = store.parse(row.program_state)
        if state is None or state.failed or state.terminal or state.finalized:
            continue
        if now - state.updated_at < STALLED_AFTER:
            continue
        await _recover(state)


async def _recover(state: ProgramState) -> None:
    """Re-enqueue what a stalled run is waiting for."""
    recover_log = log.bind(
        chat_session_id=str(state.session_id),
        run_id=str(state.run_id),
        stage=state.current_stage.value,
    )
    if state.status is ProgramStatus.PENDING_APPROVAL:
        recover_log.warning("program_recovery_finalize", reason="the draft was never rendered")
        await finalize_program_draft.kiq(state.session_id, state.run_id)
        return
    if state.status is not ProgramStatus.GENERATING:
        return
    stage = state.current_stage
    if await _locked(state.session_id, stage):
        recover_log.info("program_recovery_worker_alive")
        return
    spent = state.attempts.get(stage.value, 0)
    if spent >= MAX_STAGE_ATTEMPTS:
        failed = state.failed_run(f"этап не завершился за {spent} попытки")
        saved = await store.save(failed, expected_run_id=state.run_id)
        recover_log.error("program_recovery_gave_up", attempts=spent)
        await _notify(saved.session_id, _failure_message("этап не завершился"))
        return
    saved = await store.save(state.enqueued(stage), expected_run_id=state.run_id)
    recover_log.warning("program_recovery_requeue", attempt=saved.attempts.get(stage.value))
    await program_stage.kiq(saved.session_id, stage.value, saved.run_id)


async def _advance(state: ProgramState) -> None:
    """Enqueue what follows the stage that just completed.

    Both enqueues of the pipeline happen here — the next stage and the finalize
    task — and both read the state the database has, so replaying a stage task
    does not start a second copy of anything. A successor that is already running
    or done is left alone.
    """
    if state.failed or state.terminal:
        return
    if state.status is ProgramStatus.PENDING_APPROVAL:
        await finalize_program_draft.kiq(state.session_id, state.run_id)
        return
    next_stage = state.current_stage
    if state.stage_status(next_stage) in (StageStatus.IN_PROGRESS, StageStatus.COMPLETED):
        return
    saved = await store.save(state.enqueued(next_stage), expected_run_id=state.run_id)
    await program_stage.kiq(saved.session_id, next_stage.value, saved.run_id)


async def _user_uuid(session_id: uuid.UUID) -> uuid.UUID | None:
    """The user the provider call is attributed to."""
    async with tsq_db() as db:
        user = await db.scalar(
            select(User).join(User.chat_sessions).where(ChatSession.session_id == session_id).limit(1)
        )
    return user.uuid if user is not None else None


async def _notify(session_id: uuid.UUID, message: str) -> None:
    """Tell the methodist, in the chat, that the pipeline stopped.

    The result row is one per chat — ``generate_chat_message`` deletes it when the
    next message arrives — so the write replaces the intake's result rather than
    adding a second row under the same primary key.
    """
    try:
        async with tsq_db() as db:
            if await db.get(ChatSession, session_id) is None:
                return
            result = await db.get(GenerationResult, session_id)
            if result is None:
                db.add(
                    GenerationResult(
                        chat_session_id=session_id,
                        type=GenerationResultType.SUCCESS,
                        content=message,
                    )
                )
            else:
                result.type = GenerationResultType.SUCCESS
                result.content = message
                result.update_name = None
                result.timestamp = datetime.now(UTC)
            db.add(
                ChatMessage(
                    chat_session_id=session_id,
                    role=UserRole.ASSISTANT,
                    content=message,
                    display_text=message,
                )
            )
            await db.commit()
    except Exception as error:
        log.error("program_notify_failed", chat_session_id=str(session_id), error=str(error))


def _chat_name(title: str) -> str:
    """The chat name to give the programme, cut to the column's length."""
    if len(title) <= MAX_CHAT_NAME_LENGTH:
        return title
    return title[: MAX_CHAT_NAME_LENGTH - 3].rstrip() + "..."


async def _lock(session_id: uuid.UUID, stage: Stage) -> str | None:
    """Take the stage lock, or ``None`` when another worker holds it."""
    token = uuid.uuid4().hex
    async with get_redis_client() as redis:
        acquired = await redis.set(
            get_program_stage_lock_key(session_id, stage),
            token,
            ex=PROGRAM_STAGE_LOCK_TTL,
            nx=True,
        )
    return token if acquired else None


async def _unlock(session_id: uuid.UUID, stage: Stage, token: str) -> None:
    """Release the stage lock if this worker still holds it."""
    try:
        async with get_redis_client() as redis:
            await redis.eval(_UNLOCK, 1, get_program_stage_lock_key(session_id, stage), token)
    except Exception as error:
        log.warning("program_stage_unlock_failed", chat_session_id=str(session_id), error=str(error))


async def _locked(session_id: uuid.UUID, stage: Stage) -> bool:
    """Whether a worker holds the stage lock right now.

    A Redis outage must not turn recovery into a stampede, so an unreadable lock
    counts as held: the next cron tick will ask again.
    """
    try:
        async with get_redis_client() as redis:
            return bool(await redis.exists(get_program_stage_lock_key(session_id, stage)))
    except Exception as error:
        log.warning("program_stage_lock_unreadable", chat_session_id=str(session_id), error=str(error))
        return True
