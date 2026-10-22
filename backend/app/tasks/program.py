"""The sequential programme pipeline: one task per stage.

A stage asks the model for one block, commits it, marks the stage complete and
enqueues what follows. The state in the database is the only thing that passes
between stages, so a worker may die at any point without losing a committed
block.

A failed stage spends one attempt and is retried; spending the last one ends the
run and tells the methodist. A worker that vanished without failing anything is
found by :func:`recover_stalled_programs`, which enqueues the stage the run
stopped on.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import structlog.stdlib
from sqlalchemy import select

from app.agentic import store
from app.agentic.errors import (
    IncompleteDraft,
    MissingChat,
    ProgramError,
    StageNotCompleted,
    StageSuperseded,
    StaleState,
)
from app.agentic.loop import run_tool_loop
from app.agentic.models import ProgramStatus, Stage, StageStatus
from app.agentic.prompts import stage_messages
from app.agentic.render import render_draft
from app.agentic.state import CONTENT_ORDER, MAX_STAGE_ATTEMPTS, ProgramState
from app.agentic.tools import ToolContext, ToolResult, call_executor, stage_tools
from app.cache import PROGRAM_STAGE_LOCK_TTL, get_program_stage_lock_key, get_redis_client
from app.database import db_session
from app.models.auth import User
from app.models.chat import (
    ChatMessage,
    ChatSession,
    UserRole,
    write_result,
)
from app.naming import first_user_message, resolve_chat_name
from app.providers.containers import provider
from app.providers.models import GenerationResponse, Message, ResponseFormat, ToolCall
from app.tasks.conf.broker import broker
from app.tasks.exports import export_program

log = structlog.stdlib.get_logger(__name__)

#: How long a run may go untouched before recovery treats its stage as abandoned.
#: A live stage lock always wins, so a killed worker's run resumes when
#: ``PROGRAM_STAGE_LOCK_TTL`` expires, not here; this threshold catches a run
#: whose message was lost while no worker held the stage at all.
STALLED_AFTER = timedelta(minutes=10)

#: What the parts of the content are called in the chat.
CONTENT_NAMES: dict[Stage, str] = {
    Stage.ASSESSMENT: "оценочные средства",
    Stage.MATERIALS: "материалы",
}

#: Releases a stage lock only if it still holds the token of the worker that took
#: it, so an expired-and-retaken lock is not deleted by the previous owner.
_UNLOCK = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""

#: Extends a lock only while it still holds the token of the worker that took it.
_REFRESH = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
end
return 0
"""


@broker.task(queue_name="network")
async def program_stage(session_id: uuid.UUID, stage: str, run_id: uuid.UUID) -> None:
    """Run one stage of the programme a chat is building.

    ``run_id`` is in the signature so a task that arrives after the methodist
    started a new run refuses itself instead of writing into the new document.
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
        # successor: hand over instead of paying for the same block twice.
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
        await _run_stage(session_id, state, stage_value, token, stage_log)
    finally:
        await _unlock(session_id, stage_value, token)


async def _run_stage(
    session_id: uuid.UUID,
    state: ProgramState,
    stage: Stage,
    token: str,
    stage_log: structlog.stdlib.BoundLogger,
) -> None:
    """One model turn with one job: fill the stage's block and commit it."""
    user_uuid = await _user_uuid(session_id)
    if user_uuid is None:
        stage_log.error("program_stage_no_user")
        return
    offered = stage_tools(stage)
    ctx = ToolContext(session_id=session_id, state=state)

    async def keep_lock() -> None:
        if not await _refresh_lock(session_id, stage, token):
            raise StageSuperseded()

    async def generate(messages: list[Message]) -> GenerationResponse | None:
        await keep_lock()
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

    executor = call_executor(ctx, offered)

    async def call_tool(tool_call: ToolCall) -> ToolResult:
        """Run one call, remembering what a refusal asked the model to fix."""
        result = await executor(tool_call)
        if not result.ok and result.details:
            ctx.last_problems = list(result.details)
        return result

    try:
        ctx.state = await store.save(state.started(stage), expected_run_id=state.run_id)
        await run_tool_loop(
            stage_messages(ctx.state, files=await _request_files(session_id)),
            generate=generate,
            call_tool=call_tool,
            checkpoint=checkpoint,
            complete=lambda: ctx.saved,
        )
        if not ctx.saved or ctx.state is None:
            raise StageNotCompleted()
        revision = ctx.state.revision
        finished = ctx.state.completed(stage)
        if revision is not None and revision.stage is stage:
            # A revision reruns one stage and leaves the rest of the programme
            # alone, so the run does not walk the pipeline again.
            finished = finished.revision_completed(stage)
        saved = await store.save(finished, expected_run_id=state.run_id)
    except (MissingChat, StageSuperseded, StaleState) as error:
        stage_log.warning("program_stage_superseded", reason=str(error))
        return
    except ProgramError as error:
        await _stage_failed(ctx.state or state, stage, error, stage_log, ctx.last_problems)
        return

    stage_log.info("program_stage_completed", version=saved.version)
    if revision is not None and revision.stage in CONTENT_ORDER:
        # The content was redone: the methodist is told, because the chat still
        # shows the draft they approved before the change.
        await _notify(
            session_id,
            "Готово: " + CONTENT_NAMES[revision.stage] + " переработаны по вашей правке. Документ и слайды обновлены.",
        )
    await _advance(saved)


async def _stage_failed(
    state: ProgramState,
    stage: Stage,
    error: ProgramError,
    stage_log: structlog.stdlib.BoundLogger,
    problems: list[str] | None = None,
) -> None:
    """Spend an attempt, then either retry the stage or end the run.

    What the last refused call asked the model to fix says more than "the block
    was not saved", and the retry reads it in its brief instead of starting
    from the same plan that was already refused. Any other failure keeps its
    own reason: a stale refusal must not hide a provider error.
    """
    reason = "; ".join(problems) if problems and isinstance(error, StageNotCompleted) else str(error)
    failed = state.failed_stage(stage, reason)
    try:
        saved = (
            await store.save(failed, expected_run_id=state.run_id)
            if failed.terminal
            else await _enqueue_stage(failed, stage)
        )
    except (MissingChat, StaleState) as superseded:
        stage_log.info("program_stage_superseded", reason=str(superseded))
        return
    if failed.terminal:
        stage_log.error("program_stage_gave_up", error=reason)
        await _notify(saved.session_id, _failure_message(reason))
        return
    stage_log.warning("program_stage_retry", attempt=saved.attempts.get(stage), error=reason)


def _failure_message(reason: str) -> str:
    return (
        f"Не удалось собрать программу: {reason}. Черновик не готов. "
        "Напишите, что изменить, и попросите начать заново — программа будет собрана с нуля."
    )


@broker.task(queue_name="default")
async def finalize_program_draft(session_id: uuid.UUID, run_id: uuid.UUID) -> None:
    """Render the committed blocks into the chat and ask for confirmation.

    Idempotent by the ``finalized`` flag it commits under the row lock, so a
    duplicate task does not render a second message. A draft that cannot be
    assembled ends the run: the same blocks would be refused again.
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
    finalized = state
    try:
        async with store.locked(session_id) as (db, row):
            current = store.parse(row.program_state)
            if current is None or current.run_id != run_id or current.finalized or current.failed:
                return
            # Built from the row, not from the copy read before the lock: another
            # writer may have advanced the version, and finalizing must not roll
            # it back even when both writes say the same thing.
            finalized = current.mark_finalized()
            row.program_state = store.dump(finalized)
            # The programme's title is the best name the chat can have, so it
            # takes over from the name the first message gave it.
            update_name = resolve_chat_name(
                row.name,
                programme_title=draft.title,
                first_user_message=await first_user_message(db, session_id),
            )
            if update_name is not None:
                row.name = update_name
            await write_result(db, session_id, message, update_name=update_name)
            db.add(
                ChatMessage(
                    chat_session_id=session_id,
                    role=UserRole.ASSISTANT,
                    content=message,
                    display_text=message,
                )
            )
    except MissingChat:
        finalize_log.info("program_finalize_chat_deleted")
        return
    await store.mirror(finalized)
    finalize_log.info("program_draft_ready", title=draft.title)


@broker.task(queue_name="default", schedule=[{"interval": timedelta(minutes=5)}])
async def recover_stalled_programs() -> None:
    """Restart the stage of every run that stopped moving.

    Skips a stage whose Redis lock is still held, so a slow stage is never
    doubled, and spends an attempt on the re-enqueue so a stage that kills its
    worker ends the run instead of cycling through the queue forever.
    """
    async with db_session() as db:
        rows = list(await db.scalars(select(ChatSession).where(ChatSession.program_state.is_not(None))))
    now = datetime.now(UTC)
    for row in rows:
        state = store.parse(row.program_state)
        if state is None or state.failed or state.terminal:
            continue
        if state.finalized and state.status is not ProgramStatus.GENERATING:
            # The draft is on the table and waits for the methodist. A run that
            # is still being worked on is not in that state even though its
            # skeleton was rendered, and it has to be recovered.
            continue
        if now - state.updated_at < STALLED_AFTER:
            continue
        try:
            await _recover(state)
        except StaleState:
            # The row moved between the scan and the save: another sweep sees it.
            log.debug("program_recovery_stale", chat_session_id=str(state.session_id))


async def _recover(state: ProgramState) -> None:
    """Re-enqueue what a stalled run is waiting for."""
    recover_log = log.bind(
        chat_session_id=str(state.session_id),
        run_id=str(state.run_id),
        stage=state.current_stage.value,
    )
    if state.status is ProgramStatus.PENDING_APPROVAL:
        recover_log.warning("program_recovery_finalize", reason="the draft was never rendered")
        # Age the row first: finalizing writes no state of its own, so without this
        # a finalize that always raises would be re-enqueued every five minutes.
        await store.save(state.touched(), expected_run_id=state.run_id)
        await finalize_program_draft.kiq(state.session_id, state.run_id)
        return
    if state.status is not ProgramStatus.GENERATING:
        return
    stage = state.current_stage
    if await _locked(state.session_id, stage):
        recover_log.info("program_recovery_worker_alive")
        return
    spent = state.attempts.get(stage, 0)
    if spent >= MAX_STAGE_ATTEMPTS:
        failed = state.failed_run(f"этап не завершился после {spent} попыток")
        saved = await store.save(failed, expected_run_id=state.run_id)
        recover_log.error("program_recovery_gave_up", attempts=spent)
        await _notify(saved.session_id, _failure_message("этап не завершился"))
        return
    saved = await _enqueue_stage(state, stage)
    recover_log.warning("program_recovery_requeue", attempt=saved.attempts.get(stage))


async def _enqueue_stage(state: ProgramState, stage: Stage) -> ProgramState:
    """Spend an attempt on the stage and put it on the queue."""
    saved = await store.save(state.enqueued(stage), expected_run_id=state.run_id)
    await program_stage.kiq(saved.session_id, stage.value, saved.run_id)
    return saved


async def release_content(state: ProgramState) -> ProgramState:
    """Put the first content stage that is still missing on the queue.

    The approval releases the content, and the release is a stage like any
    other: it spends one of that stage's three attempts, so a stage that is
    already complete is not run again and a stage that fails has a budget.

    A programme whose content is already whole has nothing to release. Its
    document and its slides are written again, because the block that was
    revised is in them.
    """
    stage = next(
        (item for item in CONTENT_ORDER if state.stage_status(item) is not StageStatus.COMPLETED),
        None,
    )
    if stage is None:
        await export_program.kiq(state.session_id, state.run_id)
        return state
    return await _enqueue_stage(state, stage)


async def rerun_stage(state: ProgramState, stage: Stage) -> ProgramState:
    """Put a stage on the queue for a change the methodist asked for.

    The attempt is spent here, so the revised stage runs against the same
    budget as every other one: the revision gave the stage a fresh budget of
    three, and the run it starts is the first of them.
    """
    return await _enqueue_stage(state, stage)


async def _request_files(session_id: uuid.UUID) -> list[str]:
    """The files the methodist attached to the request.

    The provider's file ids are on the messages of the chat, so a stage can
    read the template or the approved programme the request came with. A chat
    without attachments has none, and a message without files carries nothing.
    """
    async with db_session() as db:
        rows = await db.scalars(
            select(ChatMessage.files_str).where(
                ChatMessage.chat_session_id == session_id,
                ChatMessage.files_str.is_not(None),
            )
        )
        found: list[str] = []
        for row in rows:
            for file_id in (row or "").split(","):
                name = file_id.strip()
                if name and name not in found:
                    found.append(name)
    return found


async def _advance(state: ProgramState) -> None:
    """Enqueue what follows the stage that just completed.

    Both enqueues of the pipeline happen here — the next stage and the finalize
    task — and both read the state the database has, so replaying a stage task
    does not start a second copy of anything.
    """
    if state.failed:
        return
    if state.status is ProgramStatus.PENDING_APPROVAL:
        await finalize_program_draft.kiq(state.session_id, state.run_id)
        return
    if state.status is ProgramStatus.APPROVED:
        # The programme is complete: the document and the slides are written,
        # and the client fetches them by their attachment ids.
        await export_program.kiq(state.session_id, state.run_id)
        return
    if state.terminal:
        return
    next_stage = state.current_stage
    if next_stage in CONTENT_ORDER:
        # The content waits: the methodist has to approve the skeleton — the
        # goal, the competences and the hours — before anything is written
        # from it. The approval is what puts the first content stage on the
        # queue; a revision of a content stage is the other way in.
        if state.status is not ProgramStatus.GENERATING:
            return
        pending = next(
            (stage for stage in CONTENT_ORDER if state.stage_status(stage) is not StageStatus.COMPLETED),
            None,
        )
        if pending is None:
            return
        next_stage = pending
    if state.stage_status(next_stage) in (StageStatus.IN_PROGRESS, StageStatus.COMPLETED):
        return
    if state.attempts.get(next_stage):
        # The successor was enqueued already: enqueueing again would spend one of
        # its three attempts on a duplicate the queue never lost.
        return
    await _enqueue_stage(state, next_stage)


async def _user_uuid(session_id: uuid.UUID) -> uuid.UUID | None:
    """The user the provider call is attributed to."""
    async with db_session() as db:
        user = await db.scalar(
            select(User).join(User.chat_sessions).where(ChatSession.session_id == session_id).limit(1)
        )
    return user.uuid if user is not None else None


async def _notify(session_id: uuid.UUID, message: str) -> None:
    """Tell the methodist, in the chat, that the pipeline stopped."""
    try:
        async with db_session() as db:
            row = await db.get(ChatSession, session_id)
            if row is None:
                return
            # A programme that never got a title still deserves a name, and the
            # first thing the methodist said is the one left.
            update_name = resolve_chat_name(row.name, first_user_message=await first_user_message(db, session_id))
            if update_name is not None:
                row.name = update_name
            await write_result(db, session_id, message, update_name=update_name)
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


async def _refresh_lock(session_id: uuid.UUID, stage: Stage, token: str) -> bool:
    """Extend the stage lock; ``False`` when another worker owns the stage."""
    try:
        async with get_redis_client() as redis:
            refreshed = await redis.eval(
                _REFRESH,
                1,
                get_program_stage_lock_key(session_id, stage),
                token,
                PROGRAM_STAGE_LOCK_TTL,
            )
    except Exception as error:
        # A Redis outage must not abort a stage that may still own the lock.
        log.warning("program_stage_refresh_failed", chat_session_id=str(session_id), error=str(error))
        return True
    return bool(refreshed)


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
    counts as held and the next cron tick asks again.
    """
    try:
        async with get_redis_client() as redis:
            return bool(await redis.exists(get_program_stage_lock_key(session_id, stage)))
    except Exception as error:
        log.warning("program_stage_lock_unreadable", chat_session_id=str(session_id), error=str(error))
        return True
