"""Background stages of the programme generation cycle.

The intake chat hands one programme to this module, and the module walks the
document one stage at a time: each TaskIQ task asks the provider for a single
block, persists it, and enqueues the next stage. That shape is what makes the
process survivable: a task holds a Redis lock while it runs, PostgreSQL keeps
every committed block, and :func:`recover_stalled_programs` re-enqueues a stage
whose worker disappeared.

One task owns one stage, and the enqueue happens after the commit. There is no
``while not ready`` loop anywhere: the chain is the state machine, and the state
in ``chat_sessions.program_state`` is the record of where it stopped.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import structlog.stdlib
from pydantic import ValidationError
from sqlalchemy import delete, select

from app.agentic.loop import StageNotCompleted, run_tool_loop
from app.agentic.models import ProgramStatus, Stage, StageStatus
from app.agentic.prompts import build_stage_messages
from app.agentic.render import render_draft
from app.agentic.state import (
    MAX_STAGE_ATTEMPTS,
    STALLED_AFTER_SECONDS,
    ProgramState,
    mark_finalized,
    mark_run_failed,
    mark_stage_failed,
    mark_stage_started,
)
from app.agentic.store import ProgramStateStore
from app.agentic.tools import STAGE_TOOLS, ToolContext, execute_tool, snapshot_state
from app.cache import (
    PROGRAM_STAGE_LOCK_TTL,
    get_program_stage_lock_key,
    get_redis_client,
)
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
from app.providers.models import GenerationResponse, Message, ResponseFormat
from app.tasks.conf.broker import broker, tsq_db

log = structlog.stdlib.get_logger(__name__)


def _chat_name(title: str) -> str:
    """Fit a programme title into a chat name."""
    if len(title) <= MAX_CHAT_NAME_LENGTH:
        return title
    return title[: MAX_CHAT_NAME_LENGTH - 3].rstrip() + "..."


async def _acquire_lock(session_id: uuid.UUID, stage: str, run_id: str) -> str | None:
    """Take the stage lock, returning the token that owns it.

    The value is unique per acquisition, not just per run: if the lock expires
    while a slow worker is still alive, the replacement's token differs and the
    old worker cannot release the replacement's lock when it finally finishes.
    """
    token = f"{run_id}:{uuid.uuid4().hex}"
    async with get_redis_client() as redis:
        acquired = await redis.set(
            get_program_stage_lock_key(session_id, stage),
            token,
            ex=PROGRAM_STAGE_LOCK_TTL,
            nx=True,
        )
    return token if acquired else None


async def _release_lock(session_id: uuid.UUID, stage: str, token: str) -> None:
    key = get_program_stage_lock_key(session_id, stage)
    async with get_redis_client() as redis:
        if await redis.get(key) == token:
            await redis.delete(key)


async def _provider_generate(session_id: uuid.UUID, messages: list[Message]) -> GenerationResponse | None:
    """One stage turn: text format, stage tools, ids that identify the session."""
    return await provider.generate(
        messages,
        ResponseFormat(type=ResponseFormat.TYPE_TEXT),
        x_client_id=session_id,
        x_session_id=session_id,
        tools=STAGE_TOOLS,
    )


async def _advance(session_id: uuid.UUID, state: ProgramState, run_id: str) -> None:
    """Enqueue the next stage, or finalisation, after the state is committed."""
    if state.failed:
        return
    following = state.next_stage()
    if following is None:
        if state.status is ProgramStatus.PENDING_APPROVAL:
            await finalize_program_draft.kiq(session_id, run_id)
        return
    if state.status is ProgramStatus.GENERATING:
        await program_stage.kiq(session_id, following, run_id)


async def _post_failure_message(session_id: uuid.UUID, stage: Stage) -> None:
    """Tell the user that the pipeline gave up on a stage."""
    content = (
        f"Не удалось сгенерировать программу на этапе «{stage.value}» после {MAX_STAGE_ATTEMPTS} попыток. "
        "Попробуйте начать заново или обратитесь к администратору."
    )
    async with tsq_db() as db:
        session = await db.get(ChatSession, session_id)
        if session is None:
            return
        await db.execute(delete(GenerationResult).where(GenerationResult.chat_session_id == session_id))
        db.add(GenerationResult(chat_session_id=session_id, type=GenerationResultType.ERROR, content=content))
        db.add(
            ChatMessage(
                chat_session_id=session_id,
                role=UserRole.ASSISTANT,
                content=content,
                display_text=content,
            )
        )
        await db.commit()


async def _record_failure(session_id: uuid.UUID, stage: Stage, run_id: str, error: Exception) -> None:
    store = ProgramStateStore(tsq_db)
    state = await store.load(session_id)
    if state is None or state.meta.run_id != run_id or state.stage_status(stage) is StageStatus.COMPLETED:
        return
    state = mark_stage_failed(state, stage, str(error))
    saved = await store.save(state, expected_run_id=run_id)
    if saved is None:
        return
    state = saved
    log.error(
        "program_stage_failed",
        chat_session_id=str(session_id),
        stage=stage.value,
        attempts=state.attempts.get(stage.value, 0),
        terminal=state.failed,
        exc=error,
    )
    if state.failed:
        await _post_failure_message(session_id, stage)


@broker.task(queue_name="network")
async def program_stage(session_id: uuid.UUID, stage: Stage, run_id: str):
    """Generate one block of the programme and hand control to the next stage.

    The lock is the first line of defence against two workers running the same
    stage; the state's ``run_id`` and the stage status are the second. A duplicate
    delivery therefore either returns immediately or finds the stage already
    complete and only re-enqueues what follows it.
    """
    lock = await _acquire_lock(session_id, stage.value, run_id)
    if lock is None:
        log.info("program_stage_locked", chat_session_id=str(session_id), stage=stage.value)
        return
    try:
        store = ProgramStateStore(tsq_db)
        state = await store.load(session_id)
        if state is None or state.meta.run_id != run_id:
            log.info("program_stage_stale_run", chat_session_id=str(session_id), stage=stage.value)
            return
        if state.stage_status(stage) is StageStatus.COMPLETED:
            await _advance(session_id, state, run_id)
            return
        if state.current_stage is not stage or state.failed:
            log.info(
                "program_stage_not_current",
                chat_session_id=str(session_id),
                stage=stage.value,
                current=state.current_stage.value if state.current_stage else None,
            )
            return

        started = await store.save(mark_stage_started(state, stage), expected_run_id=run_id)
        if started is None:
            return
        state = started

        context = ToolContext(
            session_id=session_id,
            state=state,
            stage=stage,
            known_source_ids=set(state.known_source_ids),
        )

        async def checkpoint() -> None:
            if not context.dirty:
                return
            snapshot = snapshot_state(context)
            if snapshot is None:
                return
            saved = await store.save(snapshot, expected_run_id=run_id)
            if saved is not None:
                context.state = saved
                context.dirty = False

        await run_tool_loop(
            build_stage_messages(state),
            generate=lambda messages: _provider_generate(session_id, messages),
            call_tool=lambda call: execute_tool(context, call.function),
            checkpoint=checkpoint,
        )

        if context.state is None or context.state.stage_status(stage) is not StageStatus.COMPLETED:
            raise StageNotCompleted(f"stage {stage.value} finished without a saved block")
        await checkpoint()
        log.info(
            "program_stage_completed",
            chat_session_id=str(session_id),
            stage=stage.value,
            current=context.state.current_stage.value if context.state.current_stage else None,
        )
        await _advance(session_id, context.state, run_id)
    except Exception as error:
        await _record_failure(session_id, stage, run_id, error)
    finally:
        await _release_lock(session_id, stage.value, lock)


@broker.task(queue_name="network")
async def finalize_program_draft(session_id: uuid.UUID, run_id: str):
    """Render the completed draft into the chat and wait for the methodist.

    Everything the user will see is written in one transaction together with the
    ``finalized`` flag on the state, so a worker that dies before the commit
    leaves no half-written chat behind and the recovery cron can run the task
    again.
    """
    lock = await _acquire_lock(session_id, "finalize", run_id)
    if lock is None:
        return
    try:
        store = ProgramStateStore(tsq_db)
        state = await store.load(session_id)
        if state is None or state.meta.run_id != run_id or state.finalized or state.failed:
            return
        if state.status is not ProgramStatus.PENDING_APPROVAL:
            return
        try:
            rendered = render_draft(state.to_draft())
        except (ValueError, ValidationError) as error:
            log.error("program_draft_invalid", chat_session_id=str(session_id), exc=error)
            failed = await store.save(mark_run_failed(state, str(error)), expected_run_id=run_id)
            if failed is not None:
                await _post_failure_message(session_id, Stage.SUMMARY)
            return

        final_state = mark_finalized(state)
        new_name: str | None = None
        async with tsq_db() as db:
            row = await db.scalar(select(ChatSession).where(ChatSession.session_id == session_id).with_for_update())
            if row is None:
                return
            if row.program_state is not None:
                try:
                    current = ProgramState.model_validate(row.program_state)
                except ValidationError:
                    current = None
                if current is None or current.meta.run_id != run_id or current.finalized or current.failed:
                    return
            if row.name == DEFAULT_CHAT_NAME and final_state.title:
                new_name = _chat_name(final_state.title)
                row.name = new_name
            row.program_state = final_state.model_dump(mode="json")
            await db.execute(delete(GenerationResult).where(GenerationResult.chat_session_id == session_id))
            db.add(
                GenerationResult(
                    chat_session_id=session_id,
                    type=GenerationResultType.SUCCESS,
                    content=rendered,
                    update_name=new_name,
                )
            )
            db.add(
                ChatMessage(
                    chat_session_id=session_id,
                    role=UserRole.ASSISTANT,
                    content=rendered,
                    display_text=rendered,
                )
            )
            await db.commit()
        await store.mirror(final_state)
        log.info("program_draft_finalized", chat_session_id=str(session_id), renamed_to=new_name)
    finally:
        await _release_lock(session_id, "finalize", lock)


@broker.task(queue_name="default", schedule=[{"interval": timedelta(minutes=5)}])
async def recover_stalled_programs():
    """Re-enqueue programme stages whose worker died without finishing.

    The scan is deliberately conservative: a state that is failed, approved or
    still fresh is left alone, and a stage whose Redis lock is alive is being
    worked on by someone else. Only a stale ``generating`` state with attempts
    left, or a ``pending_approval`` state whose finalisation never ran, is handed
    to a new worker, which is exactly the case a dead worker leaves behind.
    """
    now = datetime.now(UTC)
    async with tsq_db() as db:
        rows = list((await db.scalars(select(ChatSession).where(ChatSession.program_state.isnot(None)))).all())

    for row in rows:
        if row.program_state is None:
            continue
        try:
            state = ProgramState.model_validate(row.program_state)
        except ValidationError as error:
            log.error("program_state_invalid", chat_session_id=str(row.session_id), exc=error)
            continue
        if state.failed:
            continue
        if state.status is ProgramStatus.PENDING_APPROVAL:
            # The summary stage reaches pending approval before finalisation has
            # rendered anything, so a pending state that was never finalised is a
            # dead finalisation worker, not a user who has not answered yet.
            if state.finalized:
                continue
            if (now - state.meta.updated_at).total_seconds() < STALLED_AFTER_SECONDS:
                continue
            async with get_redis_client() as redis:
                if await redis.exists(get_program_stage_lock_key(row.session_id, "finalize")):
                    continue
            log.warning("program_finalisation_recovered", chat_session_id=str(row.session_id))
            await finalize_program_draft.kiq(row.session_id, state.meta.run_id)
            continue
        if state.status is not ProgramStatus.GENERATING:
            continue
        stage = state.current_stage
        if stage is None:
            if state.next_stage() is None:
                await finalize_program_draft.kiq(row.session_id, state.meta.run_id)
            continue
        if state.stage_status(stage) is StageStatus.COMPLETED:
            await _advance(row.session_id, state, state.meta.run_id)
            continue
        if state.attempts.get(stage.value, 0) >= MAX_STAGE_ATTEMPTS:
            continue
        if (now - state.meta.updated_at).total_seconds() < STALLED_AFTER_SECONDS:
            continue
        async with get_redis_client() as redis:
            if await redis.exists(get_program_stage_lock_key(row.session_id, stage.value)):
                continue
        log.warning(
            "program_stage_recovered",
            chat_session_id=str(row.session_id),
            stage=stage.value,
            attempts=state.attempts.get(stage.value, 0),
        )
        await program_stage.kiq(row.session_id, stage, state.meta.run_id)
