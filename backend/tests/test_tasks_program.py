"""Tests for the sequential programme pipeline in ``app.tasks.program``.

Every test drives the real task body through :func:`tests.helpers.run_task` with
the real database, Redis and state store. The provider is scripted and the two
enqueues are spies: what a stage does to the run, and what it hands to the next
task, is exactly what these tests are about.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.agentic import store
from app.agentic.models import ProgramStatus, Stage, StageStatus
from app.agentic.state import (
    STAGE_ORDER,
    CompetencesBlock,
    HoursBlock,
    ProgramState,
    SourcesBlock,
    SummaryBlock,
    new_state,
)
from app.auth import hash_password
from app.cache import get_program_stage_lock_key
from app.models.auth import User
from app.models.chat import (
    MAX_CHAT_NAME_LENGTH,
    ChatMessage,
    ChatSession,
    GenerationResult,
    GenerationResultType,
    UserRole,
)
from app.providers.models import (
    Content,
    FunctionCall,
    GenerationResponse,
    InputTokenDetails,
    Message,
    ToolCall,
    Usage,
)
from app.tasks import program
from tests.helpers import run_task

EXAMPLE_PATH = Path(__file__).resolve().parents[1] / "examples" / "document_state_example.json"

USAGE = Usage(
    input_tokens=1,
    input_tokens_details=InputTokenDetails(prompt_tokens=1, cached_tokens=0),
    output_tokens=1,
    total_tokens=2,
)

COMPETENCES_BLOCK: dict[str, Any] = {
    "kind": "general_development",
    "title": "Анализ данных",
    "competencies": [
        {
            "id": "comp-01",
            "statement": "Способен анализировать данные",
            "knowledge": [{"id": "ro-k-01", "statement": "Знает основы статистики"}],
        }
    ],
}


def answer(
    text: str = "",
    *,
    calls: tuple[ToolCall, ...] = (),
    finish_reason: str = "stop",
) -> GenerationResponse:
    return GenerationResponse(
        messages=[Message(role="assistant", content=[Content(text=text)] if text else [])],
        model="scripted",
        created_at=datetime.now(UTC),
        finish_reason=finish_reason,
        usage=USAGE,
        tool_calls=list(calls),
    )


def save_turn(payload: dict[str, Any], *, call_id: str = "call-1") -> GenerationResponse:
    """A turn that saves a block instead of answering."""
    call = ToolCall(
        id=call_id,
        function=FunctionCall(name="save_program_draft", arguments=json.dumps(payload, ensure_ascii=False)),
    )
    return answer(finish_reason="function_call", calls=(call,))


class FakeProvider:
    """A provider that answers from a script and records the request."""

    def __init__(self) -> None:
        self.answers: list[GenerationResponse | None] = []
        self.calls: list[dict[str, Any]] = []
        #: Awaited before every answer, so a test can change the world under a
        #: running stage: bump the row's version, delete the chat, and so on.
        self.on_generate: Any = None

    async def generate(
        self,
        message_history: list[Message],
        response_format: Any,
        x_client_id: uuid.UUID,
        x_session_id: uuid.UUID,
        *,
        tools: list[Any] | None = None,
    ) -> GenerationResponse | None:
        self.calls.append(
            {
                "messages": message_history,
                "format": response_format.type,
                "tools": [tool.function.name for tool in tools or []],
                "client": x_client_id,
                "session": x_session_id,
            }
        )
        if self.on_generate is not None:
            await self.on_generate()
        return self.answers.pop(0) if self.answers else None


def complete_state(session_id: uuid.UUID) -> ProgramState:
    """A run that committed every block of the example document."""
    raw = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
    state = new_state(session_id, requested_hours=66, requirements="Курс по анализу данных")
    for stage in STAGE_ORDER:
        state = state.started(stage)
        if stage is Stage.COMPETENCES:
            block = CompetencesBlock(
                kind=raw["meta"]["kind"],
                title=raw["meta"]["title"],
                competencies=raw["competencies"],
            )
            state = state.with_competences(block, [])
        elif stage is Stage.HOURS:
            state = state.with_curriculum(HoursBlock(curriculum=raw["curriculum"]))
        elif stage is Stage.SOURCES:
            state = state.with_sources(SourcesBlock(sources=raw["sources"]))
        else:
            state = state.with_summary(SummaryBlock(summary=raw["summary"]), [])
        state = state.completed(stage)
    return state


def aged(state: ProgramState, delta: timedelta = timedelta(hours=1)) -> ProgramState:
    """The same state, untouched for ``delta``."""
    return state.model_copy(update={"updated_at": datetime.now(UTC) - delta})


@pytest_asyncio.fixture()
async def chat(engine: AsyncEngine) -> AsyncGenerator[uuid.UUID]:
    """A committed chat, because the tasks open their own connections."""
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with maker() as db:
        user = User(username=f"program-{uuid.uuid4().hex[:8]}", password_hash=hash_password("password1234"))
        db.add(user)
        await db.commit()
        await db.refresh(user)
        row = ChatSession(session_id=uuid.uuid4(), user_id=user.id)
        db.add(row)
        await db.commit()
        session_id, user_id = row.session_id, user.id
    try:
        yield session_id
    finally:
        async with maker() as db:
            await db.execute(delete(ChatSession).where(ChatSession.session_id == session_id))
            await db.execute(delete(User).where(User.id == user_id))
            await db.commit()


@pytest.fixture()
def provider(monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    fake = FakeProvider()
    monkeypatch.setattr(program, "provider", fake)
    return fake


@pytest.fixture()
def enqueued(monkeypatch: pytest.MonkeyPatch) -> tuple[AsyncMock, AsyncMock]:
    """Capture the two enqueues instead of publishing to RabbitMQ."""
    stages = AsyncMock(name="program_stage.kiq")
    finalizes = AsyncMock(name="finalize_program_draft.kiq")
    monkeypatch.setattr(program.program_stage, "kiq", stages)
    monkeypatch.setattr(program.finalize_program_draft, "kiq", finalizes)
    return stages, finalizes


async def read_result(engine: AsyncEngine, session_id: uuid.UUID) -> GenerationResult | None:
    async with async_sessionmaker(bind=engine, expire_on_commit=False)() as db:
        return await db.get(GenerationResult, session_id)


async def read_messages(engine: AsyncEngine, session_id: uuid.UUID) -> list[ChatMessage]:
    async with async_sessionmaker(bind=engine, expire_on_commit=False)() as db:
        rows = await db.scalars(
            select(ChatMessage).where(ChatMessage.chat_session_id == session_id).order_by(ChatMessage.id)
        )
        return list(rows)


async def read_session(engine: AsyncEngine, session_id: uuid.UUID) -> ChatSession | None:
    async with async_sessionmaker(bind=engine, expire_on_commit=False)() as db:
        return await db.get(ChatSession, session_id)


# --------------------------------------------------------------------------------------
# a stage
# --------------------------------------------------------------------------------------


async def test_a_saved_block_completes_the_stage_and_enqueues_the_next(
    chat: uuid.UUID, engine: AsyncEngine, task_db: None, redis_client: Redis, provider: FakeProvider, enqueued
) -> None:
    state = await store.save(new_state(chat, requested_hours=72, requirements="Курс по анализу данных"))
    provider.answers.extend([save_turn(COMPETENCES_BLOCK), answer("Компетенции сохранены.")])

    await run_task(program.program_stage, chat, Stage.COMPETENCES.value, state.run_id)

    saved = await store.load(chat)
    assert saved is not None
    assert saved.title == "Анализ данных"
    assert saved.stage_status(Stage.COMPETENCES) is StageStatus.COMPLETED
    assert saved.current_stage is Stage.HOURS
    assert saved.stage_status(Stage.HOURS) is StageStatus.PENDING
    assert saved.attempts[Stage.HOURS.value] == 1
    stages, _ = enqueued
    stages.assert_awaited_once_with(chat, Stage.HOURS.value, state.run_id)


async def test_a_citing_stage_is_offered_the_search_and_its_own_block(
    chat: uuid.UUID, task_db: None, redis_client: Redis, provider: FakeProvider, enqueued
) -> None:
    state = await store.save(new_state(chat, requested_hours=72, requirements="Курс"))
    provider.answers.extend([save_turn(COMPETENCES_BLOCK), answer("готово")])

    await run_task(program.program_stage, chat, Stage.COMPETENCES.value, state.run_id)

    assert provider.calls[0]["tools"] == ["search_sources", "save_program_draft"]
    assert provider.calls[0]["format"] == "text"
    assert provider.calls[0]["session"] == chat


async def test_the_stage_prompt_carries_the_task_and_the_saved_blocks(
    chat: uuid.UUID, task_db: None, redis_client: Redis, provider: FakeProvider, enqueued
) -> None:
    state = await store.save(new_state(chat, requested_hours=72, requirements="Курс по анализу данных"))
    provider.answers.extend([save_turn(COMPETENCES_BLOCK), answer("готово")])

    await run_task(program.program_stage, chat, Stage.COMPETENCES.value, state.run_id)

    prompt = " ".join(part.text for message in provider.calls[0]["messages"] for part in message.content)
    assert "Курс по анализу данных" in prompt
    assert "72" in prompt


async def test_a_stage_that_saves_nothing_is_retried(
    chat: uuid.UUID, task_db: None, redis_client: Redis, provider: FakeProvider, enqueued
) -> None:
    state = await store.save(new_state(chat, requested_hours=None, requirements=None))
    provider.answers.append(answer("Я подумал и решил не сохранять блок."))

    await run_task(program.program_stage, chat, Stage.COMPETENCES.value, state.run_id)

    saved = await store.load(chat)
    assert saved is not None
    assert not saved.failed
    assert saved.stage_status(Stage.COMPETENCES) is StageStatus.PENDING
    assert saved.attempts[Stage.COMPETENCES.value] == 2
    stages, _ = enqueued
    stages.assert_awaited_once_with(chat, Stage.COMPETENCES.value, state.run_id)


async def test_the_last_attempt_ends_the_run_and_tells_the_methodist(
    chat: uuid.UUID, engine: AsyncEngine, task_db: None, redis_client: Redis, provider: FakeProvider, enqueued
) -> None:
    state = await store.save(new_state(chat, requested_hours=None, requirements=None))
    spent = await store.save(state.enqueued(Stage.COMPETENCES).enqueued(Stage.COMPETENCES))
    provider.answers.append(answer(""))

    await run_task(program.program_stage, chat, Stage.COMPETENCES.value, spent.run_id)

    saved = await store.load(chat)
    assert saved is not None
    assert saved.failed
    stages, _ = enqueued
    stages.assert_not_awaited()
    result = await read_result(engine, chat)
    assert result is not None
    assert "Не удалось собрать программу" in result.content
    messages = await read_messages(engine, chat)
    assert messages[-1].role is UserRole.ASSISTANT


async def test_a_stage_of_another_run_does_not_touch_the_new_one(
    chat: uuid.UUID, task_db: None, redis_client: Redis, provider: FakeProvider, enqueued
) -> None:
    await store.save(new_state(chat, requested_hours=None, requirements=None))
    provider.answers.append(answer("готово"))

    await run_task(program.program_stage, chat, Stage.COMPETENCES.value, uuid.uuid4())

    assert provider.calls == []
    saved = await store.load(chat)
    assert saved is not None
    assert saved.stage_status(Stage.COMPETENCES) is StageStatus.PENDING


async def test_a_stage_another_worker_holds_is_left_alone(
    chat: uuid.UUID, task_db: None, redis_client: Redis, provider: FakeProvider, enqueued
) -> None:
    state = await store.save(new_state(chat, requested_hours=None, requirements=None))
    await redis_client.set(get_program_stage_lock_key(chat, Stage.COMPETENCES), "other-worker", ex=60)

    await run_task(program.program_stage, chat, Stage.COMPETENCES.value, state.run_id)

    assert provider.calls == []


async def test_a_completed_stage_is_not_generated_again(
    chat: uuid.UUID, task_db: None, redis_client: Redis, provider: FakeProvider, enqueued
) -> None:
    state = await store.save(new_state(chat, requested_hours=None, requirements=None))
    await store.save(state.completed(Stage.COMPETENCES))

    await run_task(program.program_stage, chat, Stage.COMPETENCES.value, state.run_id)

    assert provider.calls == []
    saved = await store.load(chat)
    assert saved is not None
    assert saved.attempts[Stage.HOURS.value] == 1
    stages, _ = enqueued
    stages.assert_awaited_once_with(chat, Stage.HOURS.value, state.run_id)


# --------------------------------------------------------------------------------------
# finalising
# --------------------------------------------------------------------------------------


async def test_finalising_renders_the_draft_and_asks_for_confirmation(
    chat: uuid.UUID, engine: AsyncEngine, task_db: None, redis_client: Redis, enqueued
) -> None:
    state = await store.save(complete_state(chat))

    await run_task(program.finalize_program_draft, chat, state.run_id)

    saved = await store.load(chat)
    assert saved is not None
    assert saved.finalized
    assert saved.status is ProgramStatus.PENDING_APPROVAL
    result = await read_result(engine, chat)
    assert result is not None
    assert "Анализ данных на Python" in result.content
    assert "подтверждаю" in result.content
    assert result.update_name == "Анализ данных на Python"
    session = await read_session(engine, chat)
    assert session is not None
    assert session.name == "Анализ данных на Python"


async def test_finalising_twice_renders_one_message(
    chat: uuid.UUID, engine: AsyncEngine, task_db: None, redis_client: Redis, enqueued
) -> None:
    state = await store.save(complete_state(chat))

    await run_task(program.finalize_program_draft, chat, state.run_id)
    await run_task(program.finalize_program_draft, chat, state.run_id)

    assert len(await read_messages(engine, chat)) == 1


async def test_an_incomplete_draft_ends_the_run(
    chat: uuid.UUID, engine: AsyncEngine, task_db: None, redis_client: Redis, enqueued
) -> None:
    state = new_state(chat, requested_hours=None, requirements=None)
    for stage in STAGE_ORDER:
        state = state.completed(stage)
    await store.save(state)

    await run_task(program.finalize_program_draft, chat, state.run_id)

    saved = await store.load(chat)
    assert saved is not None
    assert saved.failed
    result = await read_result(engine, chat)
    assert result is not None
    assert "Не удалось собрать программу" in result.content


# --------------------------------------------------------------------------------------
# recovery
# --------------------------------------------------------------------------------------


async def test_recovery_requeues_the_stage_of_a_stalled_run(
    chat: uuid.UUID, task_db: None, redis_client: Redis, enqueued
) -> None:
    state = await store.save(new_state(chat, requested_hours=None, requirements=None))
    await store.save(aged(state.started(Stage.COMPETENCES)))

    await run_task(program.recover_stalled_programs)

    stages, _ = enqueued
    stages.assert_awaited_once_with(chat, Stage.COMPETENCES.value, state.run_id)
    saved = await store.load(chat)
    assert saved is not None
    assert saved.attempts[Stage.COMPETENCES.value] == 2


async def test_recovery_leaves_a_fresh_run_alone(chat: uuid.UUID, task_db: None, redis_client: Redis, enqueued) -> None:
    await store.save(new_state(chat, requested_hours=None, requirements=None))

    await run_task(program.recover_stalled_programs)

    stages, _ = enqueued
    stages.assert_not_awaited()


async def test_recovery_leaves_a_stage_whose_worker_is_alive(
    chat: uuid.UUID, task_db: None, redis_client: Redis, enqueued
) -> None:
    state = await store.save(new_state(chat, requested_hours=None, requirements=None))
    await store.save(aged(state.started(Stage.COMPETENCES)))
    await redis_client.set(get_program_stage_lock_key(chat, Stage.COMPETENCES), "other-worker", ex=60)

    await run_task(program.recover_stalled_programs)

    stages, _ = enqueued
    stages.assert_not_awaited()


async def test_recovery_gives_up_when_the_attempts_are_spent(
    chat: uuid.UUID, task_db: None, redis_client: Redis, enqueued
) -> None:
    state = await store.save(new_state(chat, requested_hours=None, requirements=None))
    spent = state.enqueued(Stage.COMPETENCES).enqueued(Stage.COMPETENCES)
    await store.save(aged(spent.started(Stage.COMPETENCES)))

    await run_task(program.recover_stalled_programs)

    saved = await store.load(chat)
    assert saved is not None
    assert saved.failed
    stages, _ = enqueued
    stages.assert_not_awaited()


async def test_recovery_renders_a_draft_that_was_never_rendered(
    chat: uuid.UUID, task_db: None, redis_client: Redis, enqueued
) -> None:
    state = await store.save(aged(complete_state(chat)))

    await run_task(program.recover_stalled_programs)

    _, finalizes = enqueued
    finalizes.assert_awaited_once_with(chat, state.run_id)


async def test_a_stage_of_a_closed_run_does_nothing(
    chat: uuid.UUID, task_db: None, redis_client: Redis, provider: FakeProvider, enqueued
) -> None:
    state = await store.save(new_state(chat, requested_hours=None, requirements=None).failed_run("не вышло"))

    await run_task(program.program_stage, chat, Stage.COMPETENCES.value, state.run_id)

    assert provider.calls == []
    stages, _ = enqueued
    stages.assert_not_awaited()


async def test_a_stage_of_a_run_that_is_not_generating_does_nothing(
    chat: uuid.UUID, task_db: None, redis_client: Redis, provider: FakeProvider, enqueued
) -> None:
    state = await store.save(new_state(chat, requested_hours=None, requirements=None).completed(Stage.SUMMARY))

    await run_task(program.program_stage, chat, Stage.COMPETENCES.value, state.run_id)

    assert provider.calls == []
    stages, _ = enqueued
    stages.assert_not_awaited()


async def test_a_search_round_before_the_save_is_not_checkpointed(
    chat: uuid.UUID, task_db: None, redis_client: Redis, provider: FakeProvider, enqueued
) -> None:
    state = await store.save(new_state(chat, requested_hours=72, requirements="Курс"))
    search_turn = answer(
        finish_reason="function_call",
        calls=(
            ToolCall(
                id="call-0",
                function=FunctionCall(name="search_sources", arguments='{"query": "профстандарт аналитика"}'),
            ),
        ),
    )
    provider.answers.extend([search_turn, save_turn(COMPETENCES_BLOCK), answer("готово")])

    await run_task(program.program_stage, chat, Stage.COMPETENCES.value, state.run_id)

    saved = await store.load(chat)
    assert saved is not None
    assert saved.title == "Анализ данных"
    assert len(provider.calls) == 3


@pytest.mark.parametrize(
    ("turn", "reason"),
    [
        (None, "провайдер не ответил"),
        (answer("начал и не закончил", finish_reason="length"), "ответ модели обрезан по длине"),
        (answer(finish_reason="function_call_error"), "модель вернула некорректный вызов инструмента"),
    ],
)
async def test_a_broken_turn_is_retried_with_a_reason(
    chat: uuid.UUID, task_db: None, redis_client: Redis, provider: FakeProvider, enqueued, turn, reason: str
) -> None:
    state = await store.save(new_state(chat, requested_hours=None, requirements=None))
    provider.answers.append(turn)

    await run_task(program.program_stage, chat, Stage.COMPETENCES.value, state.run_id)

    saved = await store.load(chat)
    assert saved is not None
    assert saved.error == reason
    assert saved.attempts[Stage.COMPETENCES.value] == 2


async def test_a_stage_superseded_mid_flight_does_not_overwrite_the_row(
    chat: uuid.UUID, task_db: None, redis_client: Redis, provider: FakeProvider, enqueued
) -> None:
    state = await store.save(new_state(chat, requested_hours=None, requirements=None))

    async def bump() -> None:
        current = await store.load(chat)
        assert current is not None
        await store.save(current.enqueued(Stage.COMPETENCES))

    provider.on_generate = bump
    provider.answers.append(save_turn(COMPETENCES_BLOCK))

    await run_task(program.program_stage, chat, Stage.COMPETENCES.value, state.run_id)

    saved = await store.load(chat)
    assert saved is not None
    assert saved.title is None
    stages, _ = enqueued
    stages.assert_not_awaited()


async def test_a_stage_stops_when_the_chat_is_deleted_under_it(
    chat: uuid.UUID, engine: AsyncEngine, task_db: None, redis_client: Redis, provider: FakeProvider, enqueued
) -> None:
    state = await store.save(new_state(chat, requested_hours=None, requirements=None))

    async def delete_chat() -> None:
        async with async_sessionmaker(bind=engine, expire_on_commit=False)() as db:
            await db.execute(delete(ChatSession).where(ChatSession.session_id == chat))
            await db.commit()

    provider.on_generate = delete_chat
    provider.answers.append(save_turn(COMPETENCES_BLOCK))

    await run_task(program.program_stage, chat, Stage.COMPETENCES.value, state.run_id)

    stages, _ = enqueued
    stages.assert_not_awaited()


async def test_a_completed_stage_does_not_enqueue_a_successor_that_is_running(
    chat: uuid.UUID, task_db: None, redis_client: Redis, provider: FakeProvider, enqueued
) -> None:
    state = await store.save(
        new_state(chat, requested_hours=None, requirements=None).completed(Stage.COMPETENCES).started(Stage.HOURS)
    )

    await run_task(program.program_stage, chat, Stage.COMPETENCES.value, state.run_id)

    assert provider.calls == []
    stages, _ = enqueued
    stages.assert_not_awaited()


async def test_finalising_a_run_that_is_still_generating_does_nothing(
    chat: uuid.UUID, task_db: None, redis_client: Redis, enqueued
) -> None:
    state = await store.save(new_state(chat, requested_hours=None, requirements=None))

    await run_task(program.finalize_program_draft, chat, state.run_id)

    saved = await store.load(chat)
    assert saved is not None
    assert not saved.finalized


async def test_recovery_leaves_a_finished_run_alone(
    chat: uuid.UUID, task_db: None, redis_client: Redis, enqueued
) -> None:
    await store.save(aged(complete_state(chat).mark_finalized()))

    await run_task(program.recover_stalled_programs)

    stages, finalizes = enqueued
    stages.assert_not_awaited()
    finalizes.assert_not_awaited()


async def test_recovery_does_not_stampede_when_redis_is_unreadable(
    chat: uuid.UUID, task_db: None, redis_client: Redis, enqueued, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = await store.save(new_state(chat, requested_hours=None, requirements=None))
    await store.save(aged(state.started(Stage.COMPETENCES)))

    def unavailable() -> None:
        raise ConnectionError("redis is down")

    monkeypatch.setattr(program, "get_redis_client", unavailable)

    await run_task(program.recover_stalled_programs)

    stages, _ = enqueued
    stages.assert_not_awaited()


async def test_a_failure_replaces_the_result_the_intake_left(
    chat: uuid.UUID, engine: AsyncEngine, task_db: None, redis_client: Redis, provider: FakeProvider, enqueued
) -> None:
    state = await store.save(new_state(chat, requested_hours=None, requirements=None))
    spent = await store.save(state.enqueued(Stage.COMPETENCES).enqueued(Stage.COMPETENCES))
    async with async_sessionmaker(bind=engine, expire_on_commit=False)() as db:
        db.add(
            GenerationResult(
                chat_session_id=chat,
                type=GenerationResultType.SUCCESS,
                content="Программа собирается…",
                update_name="Анализ данных",
            )
        )
        await db.commit()
    provider.answers.append(answer(""))

    await run_task(program.program_stage, chat, Stage.COMPETENCES.value, spent.run_id)

    result = await read_result(engine, chat)
    assert result is not None
    assert "Не удалось собрать программу" in result.content
    assert result.update_name is None


async def test_a_replay_of_the_last_stage_enqueues_the_finalize(
    chat: uuid.UUID, task_db: None, redis_client: Redis, provider: FakeProvider, enqueued
) -> None:
    state = await store.save(complete_state(chat))

    await run_task(program.program_stage, chat, Stage.SUMMARY.value, state.run_id)

    assert provider.calls == []
    stages, finalizes = enqueued
    stages.assert_not_awaited()
    finalizes.assert_awaited_once_with(chat, state.run_id)


def test_a_long_programme_title_is_cut_to_the_column_length() -> None:
    name = program._chat_name("я" * 500)

    assert len(name) == MAX_CHAT_NAME_LENGTH
    assert name.endswith("...")
