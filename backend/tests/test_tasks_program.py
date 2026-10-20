"""Worker tests for the sequential programme pipeline in ``app.tasks.program``.

Each stage is driven through the real TaskIQ body, against the test PostgreSQL
and Redis containers. The provider is scripted: one turn asks for the block, the
next confirms it, which is exactly the shape a real function-calling model has.
The enqueue helpers are replaced with spies because RabbitMQ is not part of the
worker contract under test — the state transition and the argument the next task
would receive are.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.agentic.models import ProgramStatus, Stage, StageStatus
from app.agentic.state import (
    MAX_STAGE_ATTEMPTS,
    STALLED_AFTER_SECONDS,
    CompetencesBlock,
    HoursBlock,
    ProgramState,
    SourcesBlock,
    SummaryBlock,
    apply_block,
    mark_finalized,
    mark_stage_started,
    new_state,
)
from app.cache import get_program_stage_lock_key, get_program_state_key, get_redis_client
from app.models.chat import ChatMessage, ChatSession, GenerationResult, GenerationResultType
from app.providers.models import (
    Content,
    FunctionCall,
    GenerationResponse,
    InputTokenDetails,
    Message,
    ToolCall,
    Usage,
)
from app.tasks import program as program_module
from app.tasks.program import (
    finalize_program_draft as finalize_task,
)
from app.tasks.program import (
    program_stage as stage_task,
)
from app.tasks.program import (
    recover_stalled_programs as recover_task,
)
from tests.helpers import run_task

REQUESTED_HOURS = 2
SUMMARY_TITLE = "Тестовая программа"


# --------------------------------------------------------------------------------------
# scripts and seed helpers
# --------------------------------------------------------------------------------------


def response(
    text: str = "готово",
    *,
    calls: list[ToolCall] | None = None,
    finish_reason: str = "stop",
) -> GenerationResponse:
    return GenerationResponse(
        messages=[Message(role="assistant", content=[Content(text=text)])],
        model="test",
        thread_id="thread-1",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        finish_reason=finish_reason,
        usage=Usage(
            input_tokens=1,
            input_tokens_details=InputTokenDetails(prompt_tokens=1, cached_tokens=0),
            output_tokens=1,
            total_tokens=2,
        ),
        tool_calls=calls or [],
    )


def competences_block() -> CompetencesBlock:
    return CompetencesBlock.model_validate(
        {
            "competencies": [
                {
                    "id": "comp-01",
                    "statement": "Способен анализировать данные",
                    "knowledge": [{"id": "ro-k-01", "statement": "Знает методы"}],
                }
            ]
        }
    )


def hours_block(hours: float = REQUESTED_HOURS) -> HoursBlock:
    return HoursBlock.model_validate(
        {
            "curriculum": {
                "modules": [
                    {
                        "id": "m-01",
                        "title": "Модуль 1",
                        "topics": [
                            {
                                "id": "t-01.1",
                                "title": "Тема 1",
                                "hours": {"lecture": hours},
                            }
                        ],
                    }
                ],
                "schedule": {"days": 1, "hours_per_day": hours},
            }
        }
    )


def summary_block() -> SummaryBlock:
    return SummaryBlock.model_validate(
        {
            "kind": "general_development",
            "title": SUMMARY_TITLE,
            "summary": {
                "goal": "Научить работать с данными",
                "target_audience": "Взрослые слушатели",
                "form": "очная",
                "issued_document": "сертификат",
            },
        }
    )


def save_call(stage: Stage, payload: dict[str, Any]) -> ToolCall:
    return ToolCall(
        id=f"call-{stage.value}",
        function=FunctionCall(
            name="save_program_draft",
            arguments=json.dumps({"stage": stage.value, "payload": payload}, ensure_ascii=False),
        ),
    )


class ScriptedProvider:
    """A provider whose turns are queued in advance."""

    def __init__(self, *turns: GenerationResponse | Exception):
        self._turns = list(turns)
        self.calls = 0

    async def generate(self, messages, response_format, *, x_client_id, x_session_id, tools=None):
        del messages, response_format, x_client_id, x_session_id, tools
        self.calls += 1
        turn = self._turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return turn


async def seed_state(rows, state: ProgramState) -> None:
    async with rows.session() as db:
        row = await db.get(ChatSession, state.meta.session_id)
        assert row is not None
        row.program_state = state.model_dump(mode="json")
        await db.commit()


async def load_state(rows, session_id: uuid.UUID) -> ProgramState | None:
    async with rows.session() as db:
        row = await db.get(ChatSession, session_id)
        if row is None or row.program_state is None:
            return None
        return ProgramState.model_validate(row.program_state)


async def make_session(rows, *, username: str | None = None) -> ChatSession:
    user = await rows.user(username=username or f"worker-{uuid.uuid4().hex[:8]}")
    return await rows.chat_session(user)


def started_state(session_id: uuid.UUID, *, run_id: str = "run-1") -> ProgramState:
    return new_state(
        session_id,
        requested_hours=REQUESTED_HOURS,
        requirements="Тема: анализ данных",
        run_id=run_id,
    )


def ready_state(session_id: uuid.UUID, *, run_id: str = "run-1") -> ProgramState:
    state = started_state(session_id, run_id=run_id)
    state = apply_block(state, Stage.COMPETENCES, competences_block())
    state = apply_block(state, Stage.HOURS, hours_block())
    return apply_block(state, Stage.SOURCES, SourcesBlock(sources=[]))


def complete_state(session_id: uuid.UUID, *, run_id: str = "run-1") -> ProgramState:
    return apply_block(ready_state(session_id, run_id=run_id), Stage.SUMMARY, summary_block())


def stale(state: ProgramState, seconds: int = STALLED_AFTER_SECONDS + 1) -> ProgramState:
    updated = state.meta.updated_at - timedelta(seconds=seconds)
    return state.model_copy(update={"meta": state.meta.model_copy(update={"updated_at": updated})})


@pytest.fixture()
def enqueues(monkeypatch):
    """Replace the two task handles with spies; the bodies stay available to ``run_task``."""
    stage = AsyncMock()
    finalize = AsyncMock()
    monkeypatch.setattr(program_module, "program_stage", SimpleNamespace(kiq=stage))
    monkeypatch.setattr(program_module, "finalize_program_draft", SimpleNamespace(kiq=finalize))
    return SimpleNamespace(stage=stage, finalize=finalize)


# --------------------------------------------------------------------------------------
# program_stage: one stage, one block, then the next task
# --------------------------------------------------------------------------------------


async def test_a_stage_saves_its_block_and_enqueues_the_next_stage(task_db, rows, redis_keys, enqueues, monkeypatch):
    session = await make_session(rows)
    state = started_state(session.session_id)
    await seed_state(rows, state)
    provider = ScriptedProvider(
        response(calls=[save_call(Stage.COMPETENCES, competences_block().model_dump(mode="json"))]),
        response("Блок сохранён."),
    )
    monkeypatch.setattr(program_module, "provider", provider)

    await run_task(stage_task, session.session_id, Stage.COMPETENCES, "run-1")

    saved = await load_state(rows, session.session_id)
    assert saved is not None
    assert saved.stage_status(Stage.COMPETENCES) is StageStatus.COMPLETED
    assert saved.current_stage is Stage.HOURS
    assert saved.attempts["competences"] == 1
    assert provider.calls == 2
    enqueues.stage.assert_awaited_once_with(session.session_id, Stage.HOURS, "run-1")
    async with get_redis_client() as redis:
        assert await redis.exists(get_program_stage_lock_key(session.session_id, "competences")) == 0


async def test_a_stage_lock_stops_a_second_worker(task_db, rows, redis_keys, enqueues, monkeypatch):
    session = await make_session(rows)
    state = started_state(session.session_id)
    await seed_state(rows, state)
    lock_key = redis_keys.track(get_program_stage_lock_key(session.session_id, "competences"))
    await redis_keys.put(lock_key, "run-1", ex=900)
    provider = ScriptedProvider(response(calls=[save_call(Stage.COMPETENCES, competences_block().model_dump())]))
    monkeypatch.setattr(program_module, "provider", provider)

    await run_task(stage_task, session.session_id, Stage.COMPETENCES, "run-1")

    assert provider.calls == 0
    enqueues.stage.assert_not_awaited()
    saved = await load_state(rows, session.session_id)
    assert saved is not None
    assert saved.attempts == {}


async def test_a_stale_run_is_ignored(task_db, rows, redis_keys, enqueues, monkeypatch):
    session = await make_session(rows)
    state = started_state(session.session_id, run_id="current")
    await seed_state(rows, state)
    provider = ScriptedProvider(response(calls=[save_call(Stage.COMPETENCES, competences_block().model_dump())]))
    monkeypatch.setattr(program_module, "provider", provider)

    await run_task(stage_task, session.session_id, Stage.COMPETENCES, "old")

    assert provider.calls == 0
    enqueues.stage.assert_not_awaited()
    saved = await load_state(rows, session.session_id)
    assert saved is not None
    assert saved.meta.run_id == "current"


async def test_a_completed_stage_only_enqueues_what_follows_it(task_db, rows, redis_keys, enqueues, monkeypatch):
    session = await make_session(rows)
    state = apply_block(started_state(session.session_id), Stage.COMPETENCES, competences_block())
    state = state.model_copy(update={"current_stage": Stage.COMPETENCES})
    await seed_state(rows, state)
    provider = ScriptedProvider()
    monkeypatch.setattr(program_module, "provider", provider)

    await run_task(stage_task, session.session_id, Stage.COMPETENCES, "run-1")

    assert provider.calls == 0
    enqueues.stage.assert_awaited_once_with(session.session_id, Stage.HOURS, "run-1")


async def test_a_stage_other_than_the_current_one_does_nothing(task_db, rows, redis_keys, enqueues, monkeypatch):
    session = await make_session(rows)
    state = started_state(session.session_id)
    await seed_state(rows, state)
    provider = ScriptedProvider()
    monkeypatch.setattr(program_module, "provider", provider)

    await run_task(stage_task, session.session_id, Stage.HOURS, "run-1")

    assert provider.calls == 0
    enqueues.stage.assert_not_awaited()


async def test_the_last_stage_enqueues_finalisation(task_db, rows, redis_keys, enqueues, monkeypatch):
    session = await make_session(rows)
    state = ready_state(session.session_id)
    await seed_state(rows, state)
    provider = ScriptedProvider(
        response(calls=[save_call(Stage.SUMMARY, summary_block().model_dump(mode="json"))]),
        response("Блок сохранён."),
    )
    monkeypatch.setattr(program_module, "provider", provider)

    await run_task(stage_task, session.session_id, Stage.SUMMARY, "run-1")

    saved = await load_state(rows, session.session_id)
    assert saved is not None
    assert saved.status is ProgramStatus.PENDING_APPROVAL
    assert saved.current_stage is None
    enqueues.stage.assert_not_awaited()
    enqueues.finalize.assert_awaited_once_with(session.session_id, "run-1")


async def test_a_failed_stage_is_recorded_without_losing_the_earlier_blocks(
    task_db, rows, redis_keys, enqueues, monkeypatch
):
    session = await make_session(rows)
    state = ready_state(session.session_id)
    await seed_state(rows, state)
    provider = ScriptedProvider(RuntimeError("provider is down"))
    monkeypatch.setattr(program_module, "provider", provider)

    await run_task(stage_task, session.session_id, Stage.SUMMARY, "run-1")

    saved = await load_state(rows, session.session_id)
    assert saved is not None
    assert saved.attempts["summary"] == 1
    assert saved.last_error == "provider is down"
    assert saved.failed is False
    assert saved.stage_status(Stage.SUMMARY) is StageStatus.IN_PROGRESS
    assert len(saved.competencies) == 1
    assert saved.curriculum is not None
    enqueues.stage.assert_not_awaited()
    enqueues.finalize.assert_not_awaited()


async def test_the_third_failure_ends_the_run_and_tells_the_user(task_db, rows, redis_keys, enqueues, monkeypatch):
    session = await make_session(rows)
    state = ready_state(session.session_id)
    await seed_state(rows, state)
    provider = ScriptedProvider(
        RuntimeError("provider is down"),
        RuntimeError("provider is still down"),
        RuntimeError("provider is down again"),
    )
    monkeypatch.setattr(program_module, "provider", provider)

    for _ in range(MAX_STAGE_ATTEMPTS):
        await run_task(stage_task, session.session_id, Stage.SUMMARY, "run-1")

    saved = await load_state(rows, session.session_id)
    assert saved is not None
    assert saved.failed is True
    assert saved.attempts["summary"] == MAX_STAGE_ATTEMPTS
    assert saved.stage_status(Stage.SUMMARY) is StageStatus.FAILED
    async with rows.session() as db:
        result = await db.scalar(select(GenerationResult).where(GenerationResult.chat_session_id == session.session_id))
        message = await db.scalar(select(ChatMessage).where(ChatMessage.chat_session_id == session.session_id))
    assert result is not None
    assert result.type is GenerationResultType.ERROR
    assert "после 3 попыток" in result.content
    assert message is not None
    assert message.content == result.content


async def test_a_stage_that_answers_without_saving_is_a_failure(task_db, rows, redis_keys, enqueues, monkeypatch):
    session = await make_session(rows)
    state = started_state(session.session_id)
    await seed_state(rows, state)
    provider = ScriptedProvider(response("Я ничего не сохранил."))
    monkeypatch.setattr(program_module, "provider", provider)

    await run_task(stage_task, session.session_id, Stage.COMPETENCES, "run-1")

    saved = await load_state(rows, session.session_id)
    assert saved is not None
    assert saved.attempts["competences"] == 1
    assert "finished without a saved block" in (saved.last_error or "")
    enqueues.stage.assert_not_awaited()


# --------------------------------------------------------------------------------------
# finalize_program_draft: render once, in one transaction
# --------------------------------------------------------------------------------------


async def test_finalisation_renders_the_draft_renames_the_chat_and_freezes_the_state(
    task_db, rows, redis_keys, enqueues
):
    session = await make_session(rows)
    state = complete_state(session.session_id)
    await seed_state(rows, state)
    key = redis_keys.track(get_program_state_key(session.session_id))

    await run_task(finalize_task, session.session_id, "run-1")

    async with rows.session() as db:
        chat = await db.get(ChatSession, session.session_id)
        result = await db.scalar(select(GenerationResult).where(GenerationResult.chat_session_id == session.session_id))
        message = await db.scalar(select(ChatMessage).where(ChatMessage.chat_session_id == session.session_id))
    assert chat is not None
    assert chat.name == SUMMARY_TITLE
    assert chat.program_state is not None
    assert ProgramState.model_validate(chat.program_state).finalized is True
    assert result is not None
    assert result.type is GenerationResultType.SUCCESS
    assert "Черновик программы готов к подтверждению." in result.content
    assert result.update_name == SUMMARY_TITLE
    assert message is not None
    assert message.display_text == result.content
    assert await redis_keys.get(key) is not None
    enqueues.finalize.assert_not_awaited()


async def test_finalisation_keeps_a_chat_name_the_user_chose(task_db, rows, redis_keys, enqueues):
    session = await make_session(rows)
    state = complete_state(session.session_id)
    await seed_state(rows, state)
    async with rows.session() as db:
        chat = await db.get(ChatSession, session.session_id)
        chat.name = "Мой проект"
        await db.commit()

    await run_task(finalize_task, session.session_id, "run-1")

    async with rows.session() as db:
        chat = await db.get(ChatSession, session.session_id)
        result = await db.scalar(select(GenerationResult).where(GenerationResult.chat_session_id == session.session_id))
    assert chat is not None
    assert chat.name == "Мой проект"
    assert result is not None
    assert result.update_name is None


async def test_a_finalised_draft_is_not_written_twice(task_db, rows, redis_keys, enqueues):
    session = await make_session(rows)
    state = complete_state(session.session_id)
    await seed_state(rows, state)
    await run_task(finalize_task, session.session_id, "run-1")
    await run_task(finalize_task, session.session_id, "run-1")

    async with rows.session() as db:
        after = (await db.scalars(select(GenerationResult))).all()
    assert len(after) == 1


async def test_a_finalisation_that_loaded_before_commit_does_not_write_twice(
    task_db, rows, redis_keys, enqueues, monkeypatch
):
    session = await make_session(rows)
    stale_state = complete_state(session.session_id)
    await seed_state(rows, stale_state)
    await run_task(finalize_task, session.session_id, "run-1")

    async def stale_load(self, session_id):
        del self, session_id
        return stale_state

    monkeypatch.setattr(program_module.ProgramStateStore, "load", stale_load)
    await run_task(finalize_task, session.session_id, "run-1")

    async with rows.session() as db:
        results = (await db.scalars(select(GenerationResult))).all()
        messages = (await db.scalars(select(ChatMessage))).all()
    assert len(results) == 1
    assert len(messages) == 1


async def test_a_stale_finalisation_writes_nothing(task_db, rows, redis_keys, enqueues):
    session = await make_session(rows)
    state = complete_state(session.session_id, run_id="current")
    await seed_state(rows, state)

    await run_task(finalize_task, session.session_id, "old")

    async with rows.session() as db:
        chat = await db.get(ChatSession, session.session_id)
        result = await db.scalar(select(GenerationResult).where(GenerationResult.chat_session_id == session.session_id))
    assert chat is not None
    assert ProgramState.model_validate(chat.program_state).finalized is False
    assert result is None


async def test_an_invalid_draft_is_reported_instead_of_finalised(task_db, rows, redis_keys, enqueues):
    session = await make_session(rows)
    state = complete_state(session.session_id).model_copy(update={"curriculum": hours_block(10).curriculum})
    await seed_state(rows, state)

    await run_task(finalize_task, session.session_id, "run-1")
    # A broken set of committed blocks is deterministic, so a retry has to be a
    # no-op rather than a second error message.
    await run_task(finalize_task, session.session_id, "run-1")

    async with rows.session() as db:
        chat = await db.get(ChatSession, session.session_id)
        results = (await db.scalars(select(GenerationResult))).all()
        messages = (await db.scalars(select(ChatMessage))).all()
    assert chat is not None
    saved = ProgramState.model_validate(chat.program_state)
    assert saved.finalized is False
    assert saved.failed is True
    assert len(results) == 1
    assert results[0].type is GenerationResultType.ERROR
    assert "Не удалось сгенерировать программу" in results[0].content
    assert len(messages) == 1


async def test_finalisation_ignores_a_state_that_is_not_awaiting_approval(task_db, rows, redis_keys, enqueues):
    session = await make_session(rows)
    state = ready_state(session.session_id)
    await seed_state(rows, state)

    await run_task(finalize_task, session.session_id, "run-1")

    async with rows.session() as db:
        chat = await db.get(ChatSession, session.session_id)
        result = await db.scalar(select(GenerationResult).where(GenerationResult.chat_session_id == session.session_id))
    assert chat is not None
    assert ProgramState.model_validate(chat.program_state).finalized is False
    assert result is None


# --------------------------------------------------------------------------------------
# recovery: a dead worker's stage is handed to a fresh one
# --------------------------------------------------------------------------------------


async def test_recovery_reenqueues_a_stalled_stage(task_db, rows, redis_keys, enqueues):
    session = await make_session(rows)
    state = stale(mark_stage_started(started_state(session.session_id), Stage.COMPETENCES))
    await seed_state(rows, state)

    await run_task(recover_task)

    enqueues.stage.assert_awaited_once_with(session.session_id, Stage.COMPETENCES, "run-1")
    enqueues.finalize.assert_not_awaited()


async def test_recovery_leaves_a_fresh_stage_alone(task_db, rows, redis_keys, enqueues):
    session = await make_session(rows)
    state = mark_stage_started(started_state(session.session_id), Stage.COMPETENCES)
    await seed_state(rows, state)

    await run_task(recover_task)

    enqueues.stage.assert_not_awaited()


async def test_recovery_does_not_touch_a_failed_run(task_db, rows, redis_keys, enqueues):
    session = await make_session(rows)
    state = started_state(session.session_id).model_copy(update={"failed": True})
    state = stale(state)
    await seed_state(rows, state)

    await run_task(recover_task)

    enqueues.stage.assert_not_awaited()


async def test_recovery_does_not_touch_a_run_waiting_for_approval(task_db, rows, redis_keys, enqueues):
    session = await make_session(rows)
    await seed_state(rows, stale(mark_finalized(complete_state(session.session_id))))

    await run_task(recover_task)

    enqueues.stage.assert_not_awaited()
    enqueues.finalize.assert_not_awaited()


async def test_recovery_reenqueues_a_finalisation_that_never_ran(task_db, rows, redis_keys, enqueues):
    session = await make_session(rows)
    await seed_state(rows, stale(complete_state(session.session_id)))

    await run_task(recover_task)

    enqueues.stage.assert_not_awaited()
    enqueues.finalize.assert_awaited_once_with(session.session_id, "run-1")


async def test_recovery_leaves_a_fresh_finalisation_alone(task_db, rows, redis_keys, enqueues):
    session = await make_session(rows)
    await seed_state(rows, complete_state(session.session_id))

    await run_task(recover_task)

    enqueues.finalize.assert_not_awaited()


async def test_recovery_finalises_a_complete_generating_state(task_db, rows, redis_keys, enqueues):
    session = await make_session(rows)
    state = complete_state(session.session_id).model_copy(
        update={"status": ProgramStatus.GENERATING, "current_stage": None}
    )
    await seed_state(rows, stale(state))

    await run_task(recover_task)

    enqueues.finalize.assert_awaited_once_with(session.session_id, "run-1")


async def test_recovery_enqueues_the_successor_of_a_completed_stage(task_db, rows, redis_keys, enqueues):
    session = await make_session(rows)
    state = apply_block(started_state(session.session_id), Stage.COMPETENCES, competences_block())
    state = state.model_copy(update={"current_stage": Stage.COMPETENCES})
    await seed_state(rows, stale(state))

    await run_task(recover_task)

    enqueues.stage.assert_awaited_once_with(session.session_id, Stage.HOURS, "run-1")


async def test_recovery_skips_a_stage_that_has_spent_its_attempts(task_db, rows, redis_keys, enqueues):
    session = await make_session(rows)
    state = mark_stage_started(started_state(session.session_id), Stage.COMPETENCES)
    state = state.model_copy(update={"attempts": {"competences": MAX_STAGE_ATTEMPTS}})
    await seed_state(rows, stale(state))

    await run_task(recover_task)

    enqueues.stage.assert_not_awaited()


async def test_recovery_skips_a_stage_whose_lock_is_alive(task_db, rows, redis_keys, enqueues):
    session = await make_session(rows)
    state = stale(mark_stage_started(started_state(session.session_id), Stage.COMPETENCES))
    await seed_state(rows, state)
    key = redis_keys.track(get_program_stage_lock_key(session.session_id, "competences"))
    await redis_keys.put(key, "run-1", ex=900)

    await run_task(recover_task)

    enqueues.stage.assert_not_awaited()


async def test_recovery_ignores_a_payload_that_does_not_validate(task_db, rows, redis_keys, enqueues):
    session = await make_session(rows)
    async with rows.session() as db:
        chat = await db.get(ChatSession, session.session_id)
        chat.program_state = {"not": "a state"}
        await db.commit()

    await run_task(recover_task)

    enqueues.stage.assert_not_awaited()
    enqueues.finalize.assert_not_awaited()


# --------------------------------------------------------------------------------------
# worker-death and retry edges
# --------------------------------------------------------------------------------------


def test_a_long_programme_title_is_truncated_for_the_chat_name():
    name = program_module._chat_name("Д" * 200)

    assert name == "Д" * 125 + "..."
    assert len(name) == 128


async def test_advance_does_nothing_for_a_failed_state(enqueues):
    state = started_state(uuid.uuid4()).model_copy(update={"failed": True})

    await program_module._advance(uuid.uuid4(), state, "run-1")

    enqueues.stage.assert_not_awaited()
    enqueues.finalize.assert_not_awaited()


async def test_a_failure_for_a_superseded_run_is_ignored(task_db, rows, redis_keys):
    session = await make_session(rows)
    state = started_state(session.session_id, run_id="current")
    await seed_state(rows, state)

    await program_module._record_failure(session.session_id, Stage.COMPETENCES, "old", RuntimeError("поздно"))

    saved = await load_state(rows, session.session_id)
    assert saved is not None
    assert saved.attempts == {}
    assert saved.last_error is None


async def test_a_finished_worker_does_not_release_a_replacement_lock(task_db, rows, redis_keys, enqueues, monkeypatch):
    session = await make_session(rows)
    state = started_state(session.session_id, run_id="run-1")
    await seed_state(rows, state)
    key = redis_keys.track(get_program_stage_lock_key(session.session_id, "competences"))

    class LockStealingProvider(ScriptedProvider):
        def __init__(self, *turns):
            super().__init__(*turns)
            self.steals = True

        async def generate(self, messages, response_format, *, x_client_id, x_session_id, tools=None):
            del messages, response_format, x_client_id, x_session_id, tools
            if self.steals:
                self.steals = False
                async with get_redis_client() as redis:
                    await redis.set(key, "run-1:replacement", ex=900)
            return await super().generate([], None, x_client_id=None, x_session_id=None)

    provider = LockStealingProvider(
        response(calls=[save_call(Stage.COMPETENCES, competences_block().model_dump(mode="json"))]),
        response("Блок сохранён."),
    )
    monkeypatch.setattr(program_module, "provider", provider)

    await run_task(stage_task, session.session_id, Stage.COMPETENCES, "run-1")

    async with get_redis_client() as redis:
        assert await redis.get(key) == "run-1:replacement"


async def test_the_finalisation_lock_stops_a_second_worker(task_db, rows, redis_keys, enqueues):
    session = await make_session(rows)
    await seed_state(rows, complete_state(session.session_id))
    key = redis_keys.track(get_program_stage_lock_key(session.session_id, "finalize"))
    await redis_keys.put(key, "run-1", ex=900)

    await run_task(finalize_task, session.session_id, "run-1")

    async with rows.session() as db:
        chat = await db.get(ChatSession, session.session_id)
        result = await db.scalar(select(GenerationResult).where(GenerationResult.chat_session_id == session.session_id))
    assert chat is not None
    assert ProgramState.model_validate(chat.program_state).finalized is False
    assert result is None


async def test_a_failure_message_for_a_deleted_session_is_a_no_op(task_db, rows, redis_keys):
    await program_module._post_failure_message(uuid.uuid4(), Stage.COMPETENCES)
