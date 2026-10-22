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
from contextlib import asynccontextmanager
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
from app.agentic.errors import MissingChat, StaleState
from app.agentic.models import ProgramStatus, Stage, StageStatus
from app.agentic.state import (
    CONTENT_ORDER,
    SKELETON_ORDER,
    STAGE_ORDER,
    AssessmentBlock,
    CompetencesBlock,
    HoursBlock,
    MaterialsBlock,
    ProgramState,
    SourcesBlock,
    SummaryBlock,
    new_state,
)
from app.auth import hash_password
from app.cache import get_program_stage_lock_key
from app.database import db_session
from app.models.auth import User
from app.models.chat import (
    MAX_CHAT_NAME_LENGTH,
    ChatMessage,
    ChatSession,
    GenerationResult,
    GenerationResultType,
    UserRole,
)
from app.naming import resolve_chat_name
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


def example() -> dict:
    """The documented example programme, as raw blocks."""
    return json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))


def skeleton_state(session_id: uuid.UUID) -> ProgramState:
    """A run that committed the skeleton and waits for the methodist.

    The skeleton is what the methodist approves: the goal, the competences
    and the hours. The content is written only after that.
    """
    raw = example()
    state = new_state(session_id, requested_hours=72, requirements="Курс по анализу данных")
    for stage in SKELETON_ORDER:
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


def complete_state(session_id: uuid.UUID) -> ProgramState:
    """A run that committed every block of the example document."""
    raw = example()
    state = skeleton_state(session_id)
    for stage in CONTENT_ORDER:
        state = state.started(stage)
        if stage is Stage.ASSESSMENT:
            state = state.with_assessment(AssessmentBlock(assessment=raw["assessment"]))
        else:
            state = state.with_materials(MaterialsBlock(materials=raw["materials"]))
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


async def test_the_stage_receives_the_files_of_the_request(
    chat: uuid.UUID, task_db: None, redis_client: Redis, provider: FakeProvider, enqueued
) -> None:
    """The template the methodist attached is readable by the stage that writes the plan."""
    async with db_session() as db:
        db.add(
            ChatMessage(
                chat_session_id=chat,
                role=UserRole.USER,
                content="Программа по такому образцу",
                files_str="giga-file-1,giga-file-2",
            )
        )
        await db.commit()
    state = await store.save(new_state(chat, requested_hours=None, requirements=None))
    provider.answers.extend([save_turn(COMPETENCES_BLOCK), answer("готово")])

    await run_task(program.program_stage, chat, Stage.COMPETENCES.value, state.run_id)

    assert provider.calls, "the stage did not call the provider"
    sent = [
        [file.id for file in (part.files or [])]
        for message in provider.calls[0]["messages"]
        if message.role == "user"
        for part in message.content
    ]
    assert sent == [["giga-file-1", "giga-file-2"]]


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


async def test_the_approval_releases_the_content_and_the_programme_ends_complete(
    chat: uuid.UUID, task_db: None, redis_client: Redis, provider: FakeProvider, enqueued
) -> None:
    """The whole cycle: the skeleton waits, the approval writes the content.

    This is the path the criteria describe — the methodist approves the goal,
    the competences and the hours, and only then is the content written — and
    it is the only test that proves the content stages run at all.
    """
    state = await store.save(skeleton_state(chat))
    await run_task(program.finalize_program_draft, chat, state.run_id)

    waiting = await store.load(chat)
    assert waiting is not None
    assert waiting.status is ProgramStatus.PENDING_APPROVAL

    # The methodist approves the skeleton.
    await store.save(waiting.approved())
    raw = example()
    provider.answers.extend(
        [
            save_turn({"assessment": raw["assessment"]}),
            answer("Оценочные средства сохранены."),
            save_turn({"materials": raw["materials"]}),
            answer("Материалы сохранены."),
        ]
    )

    await run_task(program.program_stage, chat, Stage.ASSESSMENT.value, state.run_id)
    after_assessment = await store.load(chat)
    assert after_assessment is not None
    assert after_assessment.assessment is not None
    assert after_assessment.status is ProgramStatus.GENERATING

    await run_task(program.program_stage, chat, Stage.MATERIALS.value, state.run_id)
    saved = await store.load(chat)
    assert saved is not None
    assert saved.materials is not None
    assert saved.status is ProgramStatus.APPROVED
    assert saved.terminal


async def test_a_revision_of_the_skeleton_leads_to_the_content_being_written(
    chat: uuid.UUID, task_db: None, redis_client: Redis, provider: FakeProvider, enqueued
) -> None:
    """A change to the skeleton, the approval of the changed skeleton, the content.

    This is the whole cycle with a change in it: the methodist asks for one
    part to be redone, the part is redone, the content that was written from
    the old part is written again, and the programme ends complete.
    """
    state = await store.save(skeleton_state(chat))
    await run_task(program.finalize_program_draft, chat, state.run_id)
    waiting = await store.load(chat)
    assert waiting is not None

    # The methodist asks for the hours to be redone.
    await store.save(waiting.request_revision(Stage.HOURS, "замени тему 2.3"))
    provider.answers.extend([save_turn({"curriculum": example()["curriculum"]}), answer("готово")])

    await run_task(program.program_stage, chat, Stage.HOURS.value, state.run_id)

    revised = await store.load(chat)
    assert revised is not None
    assert revised.revision is None
    assert revised.status is ProgramStatus.PENDING_APPROVAL
    # The content was written from the old hours, so it is written again.
    assert revised.assessment is None
    assert revised.materials is None

    # The methodist approves the changed skeleton.
    await store.save(revised.approved())
    raw = example()
    provider.answers.extend(
        [
            save_turn({"assessment": raw["assessment"]}),
            answer("Оценочные средства сохранены."),
            save_turn({"materials": raw["materials"]}),
            answer("Материалы сохранены."),
        ]
    )

    await run_task(program.program_stage, chat, Stage.ASSESSMENT.value, state.run_id)
    await run_task(program.program_stage, chat, Stage.MATERIALS.value, state.run_id)

    saved = await store.load(chat)
    assert saved is not None
    assert saved.assessment is not None
    assert saved.materials is not None
    assert saved.status is ProgramStatus.APPROVED


async def test_a_revised_stage_runs_again_and_the_run_waits_for_the_methodist(
    chat: uuid.UUID, task_db: None, redis_client: Redis, provider: FakeProvider, enqueued
) -> None:
    """A change reruns one stage; the rest of the programme is not rebuilt."""
    state = await store.save(skeleton_state(chat))
    await store.save(state.request_revision(Stage.HOURS, "замени тему 2.3"))
    provider.answers.extend([save_turn({"curriculum": example()["curriculum"]}), answer("готово")])

    await run_task(program.program_stage, chat, Stage.HOURS.value, state.run_id)

    saved = await store.load(chat)
    assert saved is not None
    assert provider.calls, "the stage did not run"
    assert saved.revision is None
    assert saved.stage_status(Stage.HOURS) is StageStatus.COMPLETED
    assert saved.status is ProgramStatus.PENDING_APPROVAL
    stages, finalizes = enqueued
    stages.assert_not_awaited()
    finalizes.assert_awaited_once_with(chat, state.run_id)


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
    state = await store.save(skeleton_state(chat))

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
    state = await store.save(skeleton_state(chat))

    await run_task(program.finalize_program_draft, chat, state.run_id)
    await run_task(program.finalize_program_draft, chat, state.run_id)

    assert len(await read_messages(engine, chat)) == 1


async def test_finalising_stops_when_another_writer_finalized_first(
    chat: uuid.UUID,
    engine: AsyncEngine,
    task_db: None,
    redis_client: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The row can move between the load and the lock; the lock's copy wins."""
    state = await store.save(skeleton_state(chat))
    real_parse = store.parse
    calls = 0

    def parse(raw):
        nonlocal calls
        calls += 1
        current = real_parse(raw)
        if calls == 2 and current is not None:
            return current.mark_finalized()
        return current

    monkeypatch.setattr(program.store, "parse", parse)

    await run_task(program.finalize_program_draft, chat, state.run_id)

    saved = await store.load(chat)
    assert saved is not None
    assert not saved.finalized
    assert await read_result(engine, chat) is None


async def test_finalising_a_chat_deleted_under_it_stops_quietly(
    chat: uuid.UUID,
    engine: AsyncEngine,
    task_db: None,
    redis_client: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lock raises ``MissingChat``; finalizing must swallow it, not crash."""
    state = await store.save(complete_state(chat))
    real_locked = store.locked
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)

    @asynccontextmanager
    async def delete_then_lock(session_id: uuid.UUID):
        async with maker() as db:
            await db.execute(delete(ChatSession).where(ChatSession.session_id == session_id))
            await db.commit()
        async with real_locked(session_id) as pair:
            yield pair

    monkeypatch.setattr(program.store, "locked", delete_then_lock)

    await run_task(program.finalize_program_draft, chat, state.run_id)

    assert await read_result(engine, chat) is None


async def test_an_incomplete_draft_ends_the_run(
    chat: uuid.UUID, engine: AsyncEngine, task_db: None, redis_client: Redis, enqueued
) -> None:
    state = new_state(chat, requested_hours=None, requirements=None)
    for stage in SKELETON_ORDER:
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


async def test_recovery_ignores_a_run_that_is_not_generating(
    chat: uuid.UUID, task_db: None, redis_client: Redis, enqueued
) -> None:
    """A draft status belongs to a different lifecycle; recovery leaves it alone."""
    state = new_state(chat, requested_hours=None, requirements=None)
    drafted = state.model_copy(update={"status": ProgramStatus.DRAFT})
    await store.save(aged(drafted))

    await run_task(program.recover_stalled_programs)

    stages, finalizes = enqueued
    stages.assert_not_awaited()
    finalizes.assert_not_awaited()


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
    state = await store.save(aged(skeleton_state(chat)))

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


async def test_a_search_round_continues_the_loop_before_the_save(
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


async def test_a_state_that_moved_before_the_stage_started_is_left_alone(
    chat: uuid.UUID,
    task_db: None,
    redis_client: Redis,
    provider: FakeProvider,
    enqueued,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The save that marks the stage started is inside the protected block too.

    A worker can read the row, lose the version to another writer and only then
    begin; that first save must stop it without spending an attempt.
    """
    state = await store.save(new_state(chat, requested_hours=None, requirements=None))
    real_user_uuid = program._user_uuid

    async def move_the_row(session_id: uuid.UUID) -> uuid.UUID:
        current = await store.load(session_id)
        assert current is not None
        await store.save(current.completed(Stage.COMPETENCES))
        user_uuid = await real_user_uuid(session_id)
        assert user_uuid is not None
        return user_uuid

    monkeypatch.setattr(program, "_user_uuid", move_the_row)
    provider.answers.append(save_turn(COMPETENCES_BLOCK))

    await run_task(program.program_stage, chat, Stage.COMPETENCES.value, state.run_id)

    saved = await store.load(chat)
    assert saved is not None
    assert saved.title is None
    assert not saved.failed
    assert saved.attempts[Stage.COMPETENCES.value] == 1
    assert provider.calls == []
    stages, _ = enqueued
    stages.assert_not_awaited()


@pytest.mark.parametrize("raced", [StaleState("moved"), MissingChat("gone")])
async def test_a_failure_that_loses_the_row_stops_quietly(
    chat: uuid.UUID,
    task_db: None,
    redis_client: Redis,
    provider: FakeProvider,
    enqueued,
    monkeypatch: pytest.MonkeyPatch,
    raced: Exception,
) -> None:
    """The failure save is protected like the completion save.

    A worker can lose the row between the provider call and recording the
    failure. That must stop the task without a retry and without an unhandled
    exception, exactly as a lost completion does.
    """
    state = await store.save(new_state(chat, requested_hours=None, requirements=None))
    provider.answers.append(None)
    saves = 0
    original = store.save

    async def save(current: ProgramState, *, expected_run_id: uuid.UUID | None = None) -> ProgramState:
        nonlocal saves
        saves += 1
        if saves == 2:
            raise raced
        return await original(current, expected_run_id=expected_run_id)

    monkeypatch.setattr(program.store, "save", save)

    await run_task(program.program_stage, chat, Stage.COMPETENCES.value, state.run_id)

    saved = await store.load(chat)
    assert saves == 2, "the race must hit the failure save"
    assert saved is not None
    assert saved.stage_status(Stage.COMPETENCES) is StageStatus.IN_PROGRESS
    assert not saved.failed
    assert saved.attempts[Stage.COMPETENCES.value] == 1
    stages, _ = enqueued
    stages.assert_not_awaited()


async def test_a_stage_that_lost_its_lock_stops_before_the_next_turn(
    chat: uuid.UUID, task_db: None, redis_client: Redis, provider: FakeProvider, enqueued
) -> None:
    """A lost lock ends the stage instead of paying for another provider call."""
    state = await store.save(new_state(chat, requested_hours=None, requirements=None))
    provider.answers.extend([save_turn(COMPETENCES_BLOCK), answer("готово")])

    async def drop_the_lock() -> None:
        await redis_client.delete(get_program_stage_lock_key(chat, Stage.COMPETENCES))

    provider.on_generate = drop_the_lock

    await run_task(program.program_stage, chat, Stage.COMPETENCES.value, state.run_id)

    saved = await store.load(chat)
    assert saved is not None
    assert saved.title == "Анализ данных"
    assert saved.stage_status(Stage.COMPETENCES) is StageStatus.IN_PROGRESS
    assert len(provider.calls) == 1
    stages, _ = enqueued
    stages.assert_not_awaited()


async def test_refreshing_a_stage_lock_extends_only_the_owner(
    chat: uuid.UUID, task_db: None, redis_client: Redis
) -> None:
    key = get_program_stage_lock_key(chat, Stage.COMPETENCES)
    await redis_client.set(key, "worker-1", ex=60)

    assert await program._refresh_lock(chat, Stage.COMPETENCES, "worker-1")
    assert await redis_client.ttl(key) > 60

    assert not await program._refresh_lock(chat, Stage.COMPETENCES, "worker-2")


async def test_a_lock_refresh_survives_a_redis_outage(monkeypatch: pytest.MonkeyPatch) -> None:
    """The worker keeps the stage; recovery treats the same outage as "held".

    The two policies are deliberately opposite: a stage that may still own its
    lock must not abort, while recovery must not start a second worker on a lock
    it cannot read. Pinning them together keeps a future cleanup from making both
    sides agree in the wrong direction.
    """
    session_id = uuid.uuid4()

    def unavailable() -> None:
        raise ConnectionError("redis is down")

    monkeypatch.setattr(program, "get_redis_client", unavailable)

    assert await program._refresh_lock(session_id, Stage.COMPETENCES, "worker-1")
    assert await program._locked(session_id, Stage.COMPETENCES)


async def test_unlocking_after_a_redis_outage_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stage is already over; a lock that cannot be released must not raise."""

    def unavailable() -> None:
        raise ConnectionError("redis is down")

    monkeypatch.setattr(program, "get_redis_client", unavailable)

    await program._unlock(uuid.uuid4(), Stage.COMPETENCES, "worker-1")


async def test_notifying_a_deleted_chat_writes_nothing(task_db: None) -> None:
    """The failure path can find the chat gone; that is not an error."""
    await program._notify(uuid.uuid4(), "Не удалось собрать программу.")


async def test_a_notification_that_fails_is_logged_not_raised(
    chat: uuid.UUID, task_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken_write(*_args, **_kwargs) -> None:
        raise RuntimeError("database is down")

    monkeypatch.setattr(program, "write_result", broken_write)

    await program._notify(chat, "Не удалось собрать программу.")


async def test_a_failed_programme_names_the_chat_after_the_first_message(
    chat: uuid.UUID, engine: AsyncEngine, task_db: None
) -> None:
    """A programme that never got a title still leaves the chat with a name."""
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with maker() as db:
        db.add(
            ChatMessage(
                chat_session_id=chat,
                role=UserRole.USER,
                content="Программа по аналитике",
            )
        )
        await db.commit()

    await program._notify(chat, "Не удалось собрать программу.")

    async with maker() as db:
        row = await db.get(ChatSession, chat)
    assert row is not None
    assert row.name == "Программа по аналитике"


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


async def test_a_stage_stops_when_the_chat_has_no_user(
    chat: uuid.UUID,
    task_db: None,
    redis_client: Redis,
    provider: FakeProvider,
    enqueued,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A chat whose user row vanished cannot attribute a provider call."""
    state = await store.save(new_state(chat, requested_hours=None, requirements=None))

    async def no_user(_session_id: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(program, "_user_uuid", no_user)

    await run_task(program.program_stage, chat, Stage.COMPETENCES.value, state.run_id)

    assert provider.calls == []
    stages, _ = enqueued
    stages.assert_not_awaited()
    saved = await store.load(chat)
    assert saved is not None
    assert saved.stage_status(Stage.COMPETENCES) is StageStatus.PENDING


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
    state = await store.save(skeleton_state(chat))

    await run_task(program.program_stage, chat, Stage.SUMMARY.value, state.run_id)

    assert provider.calls == []
    stages, finalizes = enqueued
    stages.assert_not_awaited()
    finalizes.assert_awaited_once_with(chat, state.run_id)


def test_a_long_programme_title_is_cut_to_the_column_length() -> None:
    name = resolve_chat_name("Новый чат", programme_title="я" * 500)

    assert name is not None
    assert len(name) == MAX_CHAT_NAME_LENGTH
    assert name.endswith("...")


async def test_the_offline_mock_carries_a_run_through_every_stage(
    chat: uuid.UUID, engine: AsyncEngine, task_db: None, redis_client: Redis, mock_mode, enqueued
) -> None:
    """The whole cycle on the mock provider: four stages, then a rendered draft.

    This is what ``MOCK_PROVIDER_MODE=stage`` exists for, and what the Docker and
    e2e stacks rely on. It is also the only test that proves the mock's fabricated
    blocks pass the cross-block checks of :meth:`ProgramState.to_draft`.
    """
    mock_mode("stage")
    await store.save(new_state(chat, requested_hours=40, requirements="Анализ данных"))
    for stage in STAGE_ORDER:
        current = await store.load(chat)
        assert current is not None
        await run_task(program.program_stage, chat, stage.value, current.run_id)

    saved = await store.load(chat)
    assert saved is not None
    assert saved.status is ProgramStatus.PENDING_APPROVAL
    assert not saved.finalized
    _, finalizes = enqueued
    finalizes.assert_awaited()

    await run_task(program.finalize_program_draft, chat, saved.run_id)

    final = await store.load(chat)
    assert final is not None
    assert final.finalized
    result = await read_result(engine, chat)
    assert result is not None
    assert "Программа (mock)" in result.content
    assert "подтверждаю" in result.content


async def test_finalising_replaces_the_result_the_intake_left(
    chat: uuid.UUID, engine: AsyncEngine, task_db: None, redis_client: Redis, enqueued
) -> None:
    """The intake writes a result row and it is one per chat, keyed by the chat id.

    The draft has to replace that row: inserting a second one fails on the primary
    key, and the message the methodist is waiting for is never written.
    """
    state = await store.save(skeleton_state(chat))
    async with async_sessionmaker(bind=engine, expire_on_commit=False)() as db:
        db.add(
            GenerationResult(
                chat_session_id=chat,
                type=GenerationResultType.SUCCESS,
                content="Программа запущена. Черновик придёт в чат, когда все блоки будут готовы.",
            )
        )
        await db.commit()

    await run_task(program.finalize_program_draft, chat, state.run_id)

    async with async_sessionmaker(bind=engine, expire_on_commit=False)() as db:
        rows = list(await db.scalars(select(GenerationResult).where(GenerationResult.chat_session_id == chat)))
    assert len(rows) == 1
    assert "подтверждаю" in rows[0].content
    assert rows[0].update_name == "Анализ данных на Python"


async def test_a_replayed_stage_does_not_spend_an_attempt_on_its_successor(
    chat: uuid.UUID, task_db: None, redis_client: Redis, provider: FakeProvider, enqueued
) -> None:
    """The successor was enqueued before the worker died, so the replay must not repeat it.

    Enqueueing it again would burn one of the successor's three attempts on a
    duplicate the queue never lost, and the run would fail a stage it never got
    to try properly.
    """
    state = new_state(chat, requested_hours=None, requirements=None)
    queued = await store.save(state.completed(Stage.COMPETENCES).enqueued(Stage.HOURS))

    await run_task(program.program_stage, chat, Stage.COMPETENCES.value, queued.run_id)

    assert provider.calls == []
    stages, _ = enqueued
    stages.assert_not_awaited()
    saved = await store.load(chat)
    assert saved is not None
    assert saved.attempts[Stage.HOURS.value] == 1


@pytest.mark.parametrize(
    "closed",
    [lambda state: state.failed_run("не вышло"), lambda state: state.completed(Stage.MATERIALS)],
    ids=["failed", "approved"],
)
async def test_advancing_a_closed_run_enqueues_nothing(enqueued, closed) -> None:
    """A failure or an approval ends the pipeline; nothing follows it."""
    state = closed(new_state(uuid.uuid4(), requested_hours=None, requirements=None))

    await program._advance(state)

    stages, finalizes = enqueued
    stages.assert_not_awaited()
    finalizes.assert_not_awaited()


async def test_recovery_keeps_going_when_one_row_moved_under_it(
    chat: uuid.UUID, engine: AsyncEngine, task_db: None, redis_client: Redis, enqueued, monkeypatch: pytest.MonkeyPatch
) -> None:
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with maker() as db:
        first = await db.get(ChatSession, chat)
        assert first is not None
        other = ChatSession(session_id=uuid.uuid4(), user_id=first.user_id)
        db.add(other)
        await db.commit()
        other_id = other.session_id
    try:
        stale = await store.save(new_state(chat, requested_hours=None, requirements=None))
        await store.save(aged(stale.started(Stage.COMPETENCES)))
        live = await store.save(new_state(other_id, requested_hours=None, requirements=None))
        await store.save(aged(live.started(Stage.COMPETENCES)))

        original = store.save

        async def save(state: ProgramState, *, expected_run_id: uuid.UUID | None = None) -> ProgramState:
            if state.session_id == chat:
                raise StaleState("moved")
            return await original(state, expected_run_id=expected_run_id)

        monkeypatch.setattr(program.store, "save", save)

        await run_task(program.recover_stalled_programs)

        stages, _ = enqueued
        stages.assert_awaited_once_with(other_id, Stage.COMPETENCES.value, live.run_id)
    finally:
        async with maker() as db:
            await db.execute(delete(ChatSession).where(ChatSession.session_id == other_id))
            await db.commit()


async def test_finalising_keeps_a_chat_name_the_intake_already_set(
    chat: uuid.UUID, engine: AsyncEngine, task_db: None, redis_client: Redis, enqueued
) -> None:
    state = await store.save(skeleton_state(chat))
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with maker() as db:
        row = await db.get(ChatSession, chat)
        assert row is not None
        row.name = "Анализ данных: обсуждение"
        await db.commit()

    await run_task(program.finalize_program_draft, chat, state.run_id)

    async with maker() as db:
        row = await db.get(ChatSession, chat)
        result = await db.get(GenerationResult, chat)
    assert row is not None
    assert row.name == "Анализ данных: обсуждение"
    assert result is not None
    assert result.update_name is None
    assert "подтверждаю" in result.content
