"""Tests for the chat workers in ``app.tasks.api``.

Every test here drives the real task bodies through :func:`tests.helpers.run_task`,
so the database, Redis and Garage work the workers do is genuinely executed. The
only thing stubbed out is the enqueue itself (``process_response.kiq``), which
would otherwise need a live RabbitMQ.
"""

import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.agentic import store
from app.agentic.models import ProgramStatus, Stage
from app.agentic.state import ProgramState, new_state
from app.auth import hash_password
from app.cache import get_deletion_key, get_generation_key, get_redis_client
from app.models.auth import User
from app.models.chat import (
    MAX_CHAT_NAME_LENGTH,
    Attachment,
    ChatMessage,
    ChatSession,
    GenerationResult,
    GenerationResultType,
    UserRole,
)
from app.providers.mock import MOCK_QUESTION
from app.providers.models import (
    Content,
    FunctionCall,
    GenerationResponse,
    InputTokenDetails,
    Message,
    ToolCall,
    Usage,
)
from app.tasks import api
from tests.helpers import run_task

# --------------------------------------------------------------------------------------
# payload helpers
# --------------------------------------------------------------------------------------


def payload(
    *,
    chat_name: str | None = "Расчёт детали",
    message: str = "Готово.",
) -> str:
    """The reply a turn forwards.

    The model no longer names the chat, so the payload is the answer itself.
    The `chat_name` argument is kept for the tests that used to read the name
    out of the model's reply; the name now comes from the first message.
    """
    return message


class FalsyResponse:
    """A provider response object that is falsy but not ``None``.

    ``run_chat_turn`` guards its provider output twice: once with
    ``is None`` (which raises and is reported as an internal error) and once with
    a plain truthiness check, which only a response that is falsy without being
    ``None`` can reach.
    """

    def __bool__(self) -> bool:
        return False


def answer(
    text: str = "",
    *,
    calls: tuple[ToolCall, ...] = (),
    finish_reason: str = "stop",
) -> GenerationResponse:
    """One scripted assistant turn."""
    return GenerationResponse(
        messages=[Message(role="assistant", content=[Content(text=text)] if text else [])],
        model="scripted",
        created_at=datetime.now(UTC),
        finish_reason=finish_reason,
        usage=Usage(
            input_tokens=1,
            input_tokens_details=InputTokenDetails(prompt_tokens=1, cached_tokens=0),
            output_tokens=1,
            total_tokens=2,
        ),
        tool_calls=list(calls),
    )


class ScriptedProvider:
    """A provider that answers from a script, one response per turn."""

    def __init__(self, responses: list[GenerationResponse]) -> None:
        self.responses = list(responses)

    async def generate(self, messages, response_format, x_client_id, x_session_id, *, tools=None):  # noqa: ARG002
        return self.responses.pop(0) if self.responses else None


# --------------------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------------------


class Rows:
    """Rows committed for real, then removed again.

    ``db_session`` writes inside a transaction that is rolled back, while the task
    bodies open their own connection through ``tsq_db``. A worker therefore cannot
    see anything ``db_session`` left uncommitted, so the data these tests need has
    to be committed on its own connection and cleaned up explicitly.
    """

    def __init__(self, maker: async_sessionmaker):
        self._maker = maker
        self._session_ids: list[uuid.UUID] = []
        self._user_ids: list[int] = []

    @asynccontextmanager
    async def session(self):
        async with self._maker() as db:
            yield db

    async def user(self, username: str = "worker-test") -> User:
        async with self.session() as db:
            row = User(username=username, password_hash=hash_password("password1234"))
            db.add(row)
            await db.commit()
            await db.refresh(row)
        self._user_ids.append(row.id)
        return row

    async def chat_session(self, user: User, name: str = "Новый чат") -> ChatSession:
        async with self.session() as db:
            row = ChatSession(session_id=uuid.uuid4(), user_id=user.id, name=name)
            db.add(row)
            await db.commit()
            await db.refresh(row)
        self._session_ids.append(row.session_id)
        return row

    async def message(
        self,
        session_id: uuid.UUID,
        content: str = "Считай, пожалуйста",
        *,
        role: UserRole = UserRole.USER,
        display_text: str | None = None,
    ) -> ChatMessage:
        async with self.session() as db:
            row = ChatMessage(
                chat_session_id=session_id,
                role=role,
                content=content,
                display_text=display_text,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
        return row

    async def result(
        self,
        session_id: uuid.UUID,
        *,
        type: GenerationResultType = GenerationResultType.SUCCESS,
        content: str = "старое",
        timestamp: datetime | None = None,
    ) -> GenerationResult:
        async with self.session() as db:
            row = GenerationResult(chat_session_id=session_id, type=type, content=content, timestamp=timestamp)
            db.add(row)
            await db.commit()
            await db.refresh(row)
        return row

    async def program_state(self, session_id: uuid.UUID, state: ProgramState) -> None:
        """Put a programme state on the chat row, the way the pipeline does."""
        async with self.session() as db:
            await db.execute(
                update(ChatSession)
                .where(ChatSession.session_id == session_id)
                .values(program_state=state.model_dump(mode="json"))
            )
            await db.commit()

    async def drop_sessions(self, session_ids: list[uuid.UUID]) -> None:
        async with self.session() as db:
            for session_id in session_ids:
                await db.execute(delete(ChatSession).where(ChatSession.session_id == session_id))
            await db.commit()
        self._session_ids = [sid for sid in self._session_ids if sid not in session_ids]

    async def purge(self) -> None:
        async with self.session() as db:
            await db.execute(delete(ChatSession).where(ChatSession.session_id.in_(self._session_ids)))
            await db.execute(delete(User).where(User.id.in_(self._user_ids)))
            await db.commit()
        self._session_ids.clear()
        self._user_ids.clear()


@pytest_asyncio.fixture()
async def rows(engine: AsyncEngine):
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    helper = Rows(maker)
    try:
        yield helper
    finally:
        await helper.purge()


class RedisKeys:
    """Redis helper that removes every key it touched during teardown.

    Keep this in the signature of every worker test, including ones that never
    call it. Requesting it pulls in ``redis_session``, which is what stands the
    Redis testcontainer up and repoints the cached pool at it; without it the
    worker still dials the Compose ``redis`` hostname and fails with
    ``socket.gaierror``.
    """

    def __init__(self) -> None:
        self._tracked: list[str] = []

    def track(self, key: str) -> str:
        self._tracked.append(key)
        return key

    async def put(self, key: str, value: str = "1", ex: int = 300) -> str:
        self.track(key)
        async with get_redis_client() as redis:
            await redis.set(key, value, ex=ex)
        return key

    async def get(self, key: str):
        async with get_redis_client() as redis:
            return await redis.get(key)

    async def purge(self) -> None:
        if not self._tracked:
            return
        async with get_redis_client() as redis:
            await redis.delete(*self._tracked)
        self._tracked.clear()


@pytest_asyncio.fixture()
async def redis_keys(redis_session) -> RedisKeys:
    helper = RedisKeys()
    try:
        yield helper
    finally:
        await helper.purge()


@pytest.fixture()
def kiq_spy(monkeypatch) -> AsyncMock:
    """Capture ``process_response.kiq`` instead of publishing to RabbitMQ."""
    spy = AsyncMock(name="process_response.kiq")
    monkeypatch.setattr(api.process_response, "kiq", spy)
    return spy


class ProviderRecorder:
    """Wraps ``provider.generate`` and keeps every history it was handed.

    The real (mock) provider still answers, so the task under test runs to
    completion; only the message list is captured, which is what the retry
    regression tests assert on.
    """

    def __init__(self, original) -> None:
        self._original = original
        self.histories: list[list[Message]] = []
        self.tools: list[list[str]] = []

    async def __call__(self, messages: list[Message], *args, **kwargs):
        self.histories.append(messages)
        tools = kwargs.get("tools")
        if tools is not None:
            self.tools.append([tool.function.name for tool in tools])
        return await self._original(messages, *args, **kwargs)

    @property
    def history(self) -> list[Message]:
        assert len(self.histories) == 1, f"expected one generate() call, got {len(self.histories)}"
        return self.histories[0]

    def roles(self) -> list[str]:
        return [m.role for m in self.history]

    def texts(self) -> list[str]:
        return [m.content[0].text for m in self.history]


@pytest.fixture()
def provider_recorder(monkeypatch) -> ProviderRecorder:
    recorder = ProviderRecorder(api.provider.generate)
    monkeypatch.setattr(api.provider, "generate", recorder)
    return recorder


# --------------------------------------------------------------------------------------
# _add_error_result
# --------------------------------------------------------------------------------------


async def test_add_error_result_writes_the_error_and_releases_the_lock(rows, redis_keys):
    user = await rows.user()
    session = await rows.chat_session(user)
    key = await redis_keys.put(get_generation_key(user.uuid))

    async with rows.session() as db:
        await api._add_error_result(db, session.session_id, "что-то сломалось", user.uuid)

    async with rows.session() as db:
        result = await db.get(GenerationResult, session.session_id)
    assert result is not None
    assert result.type == GenerationResultType.ERROR
    assert result.content == "что-то сломалось"
    assert await redis_keys.get(key) is None


# --------------------------------------------------------------------------------------
# cleanup_old_results
# --------------------------------------------------------------------------------------


async def test_cleanup_old_results_deletes_only_stale_rows(task_db, rows):
    user = await rows.user()
    old_session = await rows.chat_session(user)
    fresh_session = await rows.chat_session(user)
    stale = await rows.result(old_session.session_id, timestamp=datetime.now(tz=UTC) - timedelta(hours=2))
    fresh = await rows.result(fresh_session.session_id, timestamp=datetime.now(tz=UTC) - timedelta(minutes=5))

    await run_task(api.cleanup_old_results)

    async with rows.session() as db:
        assert await db.get(GenerationResult, stale.chat_session_id) is None
        kept = await db.get(GenerationResult, fresh.chat_session_id)
    assert kept is not None
    assert kept.content == fresh.content


async def test_cleanup_old_results_is_idempotent(task_db, rows):
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.result(session.session_id, timestamp=datetime.now(tz=UTC) - timedelta(hours=2))

    await run_task(api.cleanup_old_results)
    await run_task(api.cleanup_old_results)

    async with rows.session() as db:
        assert await db.scalar(select(func.count()).select_from(GenerationResult)) == 0


# --------------------------------------------------------------------------------------
# run_chat_turn
# --------------------------------------------------------------------------------------


async def test_run_chat_turn_enqueues_process_response(task_db, rows, redis_keys, kiq_spy, mock_mode):
    mock_mode("ok")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "Посчитай кронштейн")

    await run_task(api.run_chat_turn, session.session_id)

    kiq_spy.assert_awaited_once()
    args = kiq_spy.await_args.args
    assert args[0] == user.uuid
    assert args[1] == session.session_id
    assert args[2], "the turn forwards the model's answer"
    # No result is written by this task: that is process_response's job.
    async with rows.session() as db:
        assert await db.scalar(select(func.count()).select_from(GenerationResult)) == 0


async def test_run_chat_turn_clears_a_stale_result_and_takes_the_lock(task_db, rows, redis_keys, kiq_spy, mock_mode):
    mock_mode("ok")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id)
    await rows.result(
        session.session_id,
        type=GenerationResultType.ERROR,
        content="прошлый провал",
        timestamp=datetime.now(tz=UTC),
    )

    await run_task(api.run_chat_turn, session.session_id)

    async with rows.session() as db:
        assert await db.get(GenerationResult, session.session_id) is None
    assert await redis_keys.get(get_generation_key(user.uuid)) == "1"
    kiq_spy.assert_awaited_once()


async def test_run_chat_turn_without_messages_writes_an_error(task_db, rows, redis_keys, kiq_spy, mock_mode):
    mock_mode("ok")
    user = await rows.user()
    session = await rows.chat_session(user)
    key = redis_keys.track(get_generation_key(user.uuid))

    await run_task(api.run_chat_turn, session.session_id)

    async with rows.session() as db:
        result = await db.get(GenerationResult, session.session_id)
    assert result is not None
    assert result.type == GenerationResultType.ERROR
    assert result.content == "Invalid message session: no messages to send"
    assert await redis_keys.get(key) is None
    kiq_spy.assert_not_awaited()


async def test_run_chat_turn_with_null_provider_response_writes_an_error(task_db, rows, redis_keys, kiq_spy, mock_mode):
    mock_mode("error")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id)
    key = redis_keys.track(get_generation_key(user.uuid))

    await run_task(api.run_chat_turn, session.session_id)

    async with rows.session() as db:
        result = await db.get(GenerationResult, session.session_id)
    assert result is not None
    assert result.type == GenerationResultType.ERROR
    assert result.content == "Internal server error occurred"
    assert await redis_keys.get(key) is None
    kiq_spy.assert_not_awaited()


async def test_run_chat_turn_with_contentless_response_writes_an_error(task_db, rows, redis_keys, kiq_spy, mock_mode):
    mock_mode("empty")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id)
    key = redis_keys.track(get_generation_key(user.uuid))

    await run_task(api.run_chat_turn, session.session_id)

    async with rows.session() as db:
        result = await db.get(GenerationResult, session.session_id)
    assert result is not None
    assert result.type == GenerationResultType.ERROR
    assert result.content == "Provider did not respond properly"
    assert await redis_keys.get(key) is None
    kiq_spy.assert_not_awaited()


async def test_run_chat_turn_with_falsy_provider_response_writes_an_error(
    task_db, rows, redis_keys, kiq_spy, monkeypatch
):
    monkeypatch.setattr(api.provider, "generate", AsyncMock(return_value=FalsyResponse()))
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id)
    key = redis_keys.track(get_generation_key(user.uuid))

    await run_task(api.run_chat_turn, session.session_id)

    async with rows.session() as db:
        result = await db.get(GenerationResult, session.session_id)
    assert result is not None
    assert result.type == GenerationResultType.ERROR
    assert result.content == "Internal server error occurred"
    assert await redis_keys.get(key) is None
    kiq_spy.assert_not_awaited()


async def test_run_chat_turn_with_an_empty_answer_writes_an_error(task_db, rows, redis_keys, kiq_spy, mock_mode):
    """A provider that answers with nothing is not an answer."""
    mock_mode("empty")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id)
    key = redis_keys.track(get_generation_key(user.uuid))

    await run_task(api.run_chat_turn, session.session_id)

    async with rows.session() as db:
        result = await db.get(GenerationResult, session.session_id)
    assert result is not None
    assert result.type == GenerationResultType.ERROR
    assert result.content == "Provider did not respond properly"
    assert await redis_keys.get(key) is None
    kiq_spy.assert_not_awaited()


async def test_run_chat_turn_stops_on_a_deletion_tombstone(task_db, rows, redis_keys, kiq_spy, mock_mode):
    mock_mode("ok")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id)
    await redis_keys.put(get_deletion_key(session.session_id))

    await run_task(api.run_chat_turn, session.session_id)

    kiq_spy.assert_not_awaited()
    # The response is dropped, but process_response never runs, so nothing is stored.
    async with rows.session() as db:
        assert await db.scalar(select(func.count()).select_from(GenerationResult)) == 0


async def test_run_chat_turn_skips_when_the_generation_lock_is_held(task_db, rows, redis_keys, kiq_spy, mock_mode):
    mock_mode("ok")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id)
    key = await redis_keys.put(get_generation_key(user.uuid))

    await run_task(api.run_chat_turn, session.session_id)

    assert await redis_keys.get(key) == "1", "the running task keeps the lock, not this one"
    kiq_spy.assert_not_awaited()
    async with rows.session() as db:
        assert await db.scalar(select(func.count()).select_from(GenerationResult)) == 0


async def test_run_chat_turn_without_an_owning_user_reports_an_error(task_db, redis_keys, monkeypatch):
    add_error = AsyncMock()
    monkeypatch.setattr(api, "_add_error_result", add_error)
    session = uuid.uuid4()

    await run_task(api.run_chat_turn, session)

    add_error.assert_awaited_once()
    assert add_error.await_args.args[1] == session
    assert add_error.await_args.args[2] == "Invalid message session"


async def test_run_chat_turn_for_a_missing_session_hits_the_foreign_key(task_db, redis_keys):
    """The ``no_user_for_session`` guard cannot persist anything for a phantom session.

    ``_add_error_result`` inserts a ``GenerationResult`` keyed by the session id,
    and that column references ``chat_sessions``. When the session really is gone
    the insert fails, so the task raises instead of returning quietly.
    """
    with pytest.raises(IntegrityError):
        await run_task(api.run_chat_turn, uuid.uuid4())


async def test_run_chat_turn_reports_an_unexpected_failure(task_db, rows, redis_keys, mock_mode):
    """A failure outside the provider call still produces an error result and unlocks."""
    mock_mode("ok")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id)
    key = redis_keys.track(get_generation_key(user.uuid))
    boom = AsyncMock(side_effect=RuntimeError("broker is down"))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(api.process_response, "kiq", boom)
        await run_task(api.run_chat_turn, session.session_id)

    boom.assert_awaited_once()
    async with rows.session() as db:
        result = await db.get(GenerationResult, session.session_id)
    assert result is not None
    assert result.type == GenerationResultType.ERROR
    assert result.content == "Internal server error occurred"
    assert await redis_keys.get(key) is None


# --------------------------------------------------------------------------------------
# _history_up_to_last_user_message
# --------------------------------------------------------------------------------------


def stored(role: UserRole, content: str) -> ChatMessage:
    return ChatMessage(role=role, content=content, chat_session_id=uuid.uuid4())


def test_history_is_cut_after_the_last_user_message():
    rows = [
        stored(UserRole.SYSTEM, "system"),
        stored(UserRole.USER, "спроси"),
        stored(UserRole.ASSISTANT, '{"message": "уточните"}'),
    ]

    kept = api._history_up_to_last_user_message(rows)

    assert [m.content for m in kept] == ["system", "спроси"]


def test_history_keeps_assistant_turns_before_the_last_user_message():
    """Earlier answers are context: the harness prompt asks what to do with them."""
    rows = [
        stored(UserRole.SYSTEM, "system"),
        stored(UserRole.USER, "посчитай"),
        stored(UserRole.ASSISTANT, '{"message": "старый ответ"}'),
        stored(UserRole.USER, "а теперь пересчитай"),
    ]

    kept = api._history_up_to_last_user_message(rows)

    assert [m.content for m in kept] == ["system", "посчитай", '{"message": "старый ответ"}', "а теперь пересчитай"]


def test_history_drops_every_trailing_assistant_message():
    rows = [
        stored(UserRole.USER, "спроси"),
        stored(UserRole.ASSISTANT, "первый ответ"),
        stored(UserRole.ASSISTANT, "второй ответ"),
    ]

    kept = api._history_up_to_last_user_message(rows)

    assert [m.content for m in kept] == ["спроси"]


def test_history_is_empty_without_a_user_message():
    assert api._history_up_to_last_user_message([]) == []
    assert api._history_up_to_last_user_message([stored(UserRole.SYSTEM, "system")]) == []


# --------------------------------------------------------------------------------------
# run_chat_turn: the history sent to the provider
# --------------------------------------------------------------------------------------


async def test_run_chat_turn_does_not_resend_the_assistant_turn_being_retried(
    task_db, rows, redis_keys, kiq_spy, mock_mode, provider_recorder
):
    """A retried turn must not be preceded by the assistant's own previous answer.

    ``process_response`` stores the assistant reply as a ``ChatMessage``, so after
    an error it is still the last row of the session. Sending it back would make
    the provider continue its own message instead of answering the user.
    """
    mock_mode("ok")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "system prompt", role=UserRole.SYSTEM)
    await rows.message(session.session_id, "Посчитай кронштейн")
    await rows.message(
        session.session_id,
        payload(message="Прошлый, неудачный ответ."),
        role=UserRole.ASSISTANT,
        display_text="Прошлый, неудачный ответ.",
    )
    await rows.result(session.session_id, type=GenerationResultType.ERROR, content="Internal server error occurred")

    await run_task(api.run_chat_turn, session.session_id)

    kiq_spy.assert_awaited_once()
    assert provider_recorder.roles() == [UserRole.SYSTEM, UserRole.USER]
    assert provider_recorder.texts() == ["system prompt", "Посчитай кронштейн"]


async def test_run_chat_turn_keeps_an_assistant_turn_that_precedes_the_last_user_message(
    task_db, rows, redis_keys, kiq_spy, mock_mode, provider_recorder
):
    """An answer the user has already seen is context, not something to cut."""
    mock_mode("ok")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "Посчитай кронштейн")
    await rows.message(session.session_id, payload(), role=UserRole.ASSISTANT, display_text="Готово.")
    await rows.message(session.session_id, "И пересчитай с другой толщиной")

    await run_task(api.run_chat_turn, session.session_id)

    kiq_spy.assert_awaited_once()
    # The first answer is legitimate context; only the trailing one is dropped.
    assert provider_recorder.roles() == [UserRole.USER, UserRole.ASSISTANT, UserRole.USER]
    assert provider_recorder.texts() == ["Посчитай кронштейн", payload(), "И пересчитай с другой толщиной"]


async def test_run_chat_turn_keeps_the_whole_history_of_a_healthy_session(
    task_db, rows, redis_keys, kiq_spy, mock_mode, provider_recorder
):
    """A plain turn ending on a user message reaches the provider untouched."""
    mock_mode("ok")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "system prompt", role=UserRole.SYSTEM)
    await rows.message(session.session_id, "Посчитай кронштейн")
    await rows.message(session.session_id, payload(), role=UserRole.ASSISTANT, display_text="Готово.")
    await rows.message(session.session_id, "А ещё одна деталь")

    await run_task(api.run_chat_turn, session.session_id)

    kiq_spy.assert_awaited_once()
    assert provider_recorder.roles() == [
        UserRole.SYSTEM,
        UserRole.USER,
        UserRole.ASSISTANT,
        UserRole.USER,
    ]
    assert provider_recorder.texts() == [
        "system prompt",
        "Посчитай кронштейн",
        payload(),
        "А ещё одна деталь",
    ]


async def test_run_chat_turn_without_a_user_message_writes_an_error(
    task_db, rows, redis_keys, kiq_spy, mock_mode, provider_recorder
):
    """A history that holds no user turn has nothing to answer."""
    mock_mode("ok")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "system prompt", role=UserRole.SYSTEM)
    redis_keys.track(get_generation_key(user.uuid))

    await run_task(api.run_chat_turn, session.session_id)

    async with rows.session() as db:
        result = await db.get(GenerationResult, session.session_id)
    assert result is not None
    assert result.type == GenerationResultType.ERROR
    assert result.content == "Invalid message session: no messages to send"
    assert provider_recorder.histories == []
    kiq_spy.assert_not_awaited()


# --------------------------------------------------------------------------------------
# run_chat_turn: the tools the turn offers
# --------------------------------------------------------------------------------------


async def test_run_chat_turn_offers_the_intake_tools(task_db, rows, redis_keys, kiq_spy, mock_mode, provider_recorder):
    """The turn is a tool loop, so the model is offered the intake tools."""
    mock_mode("ok")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "Сделай программу по аналитике")

    await run_task(api.run_chat_turn, session.session_id)

    kiq_spy.assert_awaited_once()
    assert provider_recorder.tools == [["begin_program", "approve_program_draft", "revise_program"]]


async def test_run_chat_turn_starts_a_programme_and_enqueues_the_first_stage(
    task_db, rows, redis_keys, kiq_spy, mock_mode
):
    """A turn that calls ``begin_program`` leaves a run and a stage on the queue."""
    mock_mode("stage")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "Сделай программу по аналитике на 40 часов")
    stages = AsyncMock(name="program_stage.kiq")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(api.program_stage, "kiq", stages)
        await run_task(api.run_chat_turn, session.session_id)

    async with rows.session() as db:
        stored = await db.scalar(select(ChatSession).where(ChatSession.session_id == session.session_id))
    state = store.parse(stored.program_state)
    assert state is not None
    assert state.status is ProgramStatus.GENERATING
    assert state.title is None
    stages.assert_awaited_once()
    assert stages.await_args.args[0] == session.session_id
    assert stages.await_args.args[1] == Stage.COMPETENCES.value


async def test_run_chat_turn_releases_the_content_after_the_approval(task_db, rows, redis_keys, kiq_spy, mock_mode):
    """The approval of the skeleton is what puts the first content stage on the queue."""
    mock_mode("approve")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "Программа подходит, подтверждаю")
    state = new_state(session.session_id, requested_hours=72, requirements="Программа по аналитике")
    await rows.program_state(session.session_id, state.completed(Stage.SUMMARY).mark_finalized())
    stages = AsyncMock(name="program_stage.kiq")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(api.program_stage, "kiq", stages)
        await run_task(api.run_chat_turn, session.session_id)

    async with rows.session() as db:
        stored = await db.scalar(select(ChatSession).where(ChatSession.session_id == session.session_id))
    saved = store.parse(stored.program_state)
    assert saved is not None
    assert saved.status is ProgramStatus.GENERATING
    stages.assert_awaited_once()
    assert stages.await_args.args[1] == Stage.ASSESSMENT.value


async def test_run_chat_turn_puts_the_revised_stage_on_the_queue(task_db, rows, redis_keys, kiq_spy, mock_mode):
    """A change the methodist asked for reruns one stage, not the whole programme."""
    mock_mode("revise")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "Замени тему 2.3 в учебном плане")
    state = new_state(session.session_id, requested_hours=72, requirements="Программа по аналитике")
    await rows.program_state(session.session_id, state)
    stages = AsyncMock(name="program_stage.kiq")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(api.program_stage, "kiq", stages)
        await run_task(api.run_chat_turn, session.session_id)

    async with rows.session() as db:
        stored = await db.scalar(select(ChatSession).where(ChatSession.session_id == session.session_id))
    saved = store.parse(stored.program_state)
    assert saved is not None
    assert saved.revision is not None
    assert saved.revision.stage is Stage.HOURS
    assert saved.status is ProgramStatus.GENERATING
    stages.assert_awaited_once()
    assert stages.await_args.args[1] == Stage.HOURS.value


async def test_run_chat_turn_leaves_a_programme_waiting_for_answers(task_db, rows, redis_keys, kiq_spy, mock_mode):
    """A run that asked questions waits, and nothing is put on the queue."""
    mock_mode("stage")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "Сделай программу по аналитике")
    stages = AsyncMock(name="program_stage.kiq")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(api.program_stage, "kiq", stages)
        await run_task(api.run_chat_turn, session.session_id)

    async with rows.session() as db:
        stored = await db.scalar(select(ChatSession).where(ChatSession.session_id == session.session_id))
    state = store.parse(stored.program_state)
    assert state is not None
    assert state.status is ProgramStatus.AWAITING_ANSWERS
    assert [question.text for question in state.pending_questions] == [MOCK_QUESTION]
    stages.assert_not_awaited()


async def test_run_chat_turn_applies_the_answers_and_starts_the_programme(
    task_db, rows, redis_keys, kiq_spy, mock_mode
):
    """The answers move the run on, and the first stage goes on the queue."""
    mock_mode("stage")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "Сделай программу по аналитике")
    state = new_state(
        session.session_id,
        requested_hours=None,
        requirements="Программа по аналитике",
        questions=[MOCK_QUESTION],
    )
    await rows.program_state(session.session_id, state)
    stages = AsyncMock(name="program_stage.kiq")
    answers = [{"question_id": state.questions[0].id, "text": "72 часа"}]
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(api.program_stage, "kiq", stages)
        await run_task(api.run_chat_turn, session.session_id, answers)

    async with rows.session() as db:
        stored = await db.scalar(select(ChatSession).where(ChatSession.session_id == session.session_id))
    saved = store.parse(stored.program_state)
    assert saved is not None
    assert saved.status is ProgramStatus.GENERATING
    assert saved.pending_questions == []
    assert [answer.text for answer in saved.answers] == ["72 часа"]
    stages.assert_awaited_once()
    assert stages.await_args.args[1] == Stage.COMPETENCES.value


async def test_run_chat_turn_refuses_answers_to_questions_nobody_asked(task_db, rows, redis_keys, kiq_spy, mock_mode):
    """An answer to a question the run never asked is refused."""
    mock_mode("stage")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "Сделай программу по аналитике")
    state = new_state(
        session.session_id,
        requested_hours=None,
        requirements="Программа по аналитике",
        questions=[MOCK_QUESTION],
    )
    await rows.program_state(session.session_id, state)
    stages = AsyncMock(name="program_stage.kiq")
    answers = [{"question_id": "not-a-question", "text": "72 часа"}]
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(api.program_stage, "kiq", stages)
        await run_task(api.run_chat_turn, session.session_id, answers)

    async with rows.session() as db:
        stored = await db.scalar(select(ChatSession).where(ChatSession.session_id == session.session_id))
    saved = store.parse(stored.program_state)
    assert saved is not None
    assert saved.status is ProgramStatus.AWAITING_ANSWERS
    assert saved.answers == []
    stages.assert_not_awaited()


async def test_run_chat_turn_ignores_answers_without_a_programme(task_db, rows, redis_keys, kiq_spy, mock_mode):
    """Answers for a chat that holds no programme are ignored, not an error.

    Another turn may have applied them already, and a turn that carries answers
    it cannot use is still a turn: it must not report a failure over it.
    """
    mock_mode("stage")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "Сделай программу по аналитике")
    stages = AsyncMock(name="program_stage.kiq")
    answers = [{"question_id": "q-1", "text": "72 часа"}]
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(api.program_stage, "kiq", stages)
        await run_task(api.run_chat_turn, session.session_id, answers)

    async with rows.session() as db:
        result = await db.get(GenerationResult, session.session_id)
    # The success row is written by process_response, which the spy replaces, so
    # a row here would be the error one.
    assert result is None
    stages.assert_not_awaited()
    kiq_spy.assert_awaited_once()


async def test_run_chat_turn_keeps_a_programme_the_model_started_without_an_answer(
    task_db, rows, redis_keys, kiq_spy, monkeypatch
):
    """A model that starts a programme and then says nothing still starts it.

    The tool's effect is committed as soon as it happens, so a turn that ends
    without an answer cannot lose the programme.
    """
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "Сделай программу по аналитике")
    stages = AsyncMock(name="program_stage.kiq")
    call = ToolCall(
        id="call-1",
        function=FunctionCall(
            name="begin_program",
            arguments=json.dumps({"requirements": "Курс по статистике", "requested_hours": 72}),
        ),
    )
    scripted = ScriptedProvider(
        [
            answer(finish_reason="function_call", calls=(call,)),
            answer(""),
        ]
    )
    monkeypatch.setattr(api, "provider", scripted)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(api.program_stage, "kiq", stages)
        await run_task(api.run_chat_turn, session.session_id)

    async with rows.session() as db:
        stored = await db.scalar(select(ChatSession).where(ChatSession.session_id == session.session_id))
    state = store.parse(stored.program_state)
    assert state is not None
    assert state.status is ProgramStatus.GENERATING
    assert state.requested_hours == 72
    stages.assert_awaited_once()


async def test_process_response_does_not_insert_a_second_result_row(task_db, rows, redis_keys):
    """The result row is keyed by the session, so a second writer updates it.

    The programme can finalise a draft while a turn is still publishing its
    reply; whichever arrives second has to update the row, not insert into it.
    """
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.result(session.session_id, content="черновик программы")

    await run_task(api.process_response, user.uuid, session.session_id, payload(message="Готово."))

    async with rows.session() as db:
        results = list(
            await db.scalars(select(GenerationResult).where(GenerationResult.chat_session_id == session.session_id))
        )
    assert len(results) == 1
    assert results[0].content == "Готово."


# --------------------------------------------------------------------------------------
# process_response
# --------------------------------------------------------------------------------------


async def test_process_response_stores_the_reply_and_renames_the_session(task_db, rows, redis_keys):
    """The chat takes its name from the first thing the methodist said."""
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "Расчёт кронштейна")
    await redis_keys.put(get_generation_key(user.uuid))

    await run_task(api.process_response, user.uuid, session.session_id, payload(message="Расчёт готов."))

    async with rows.session() as db:
        result = await db.get(GenerationResult, session.session_id)
        stored = await db.scalar(select(ChatSession).where(ChatSession.session_id == session.session_id))
        assistant = await db.scalar(
            select(ChatMessage).where(
                ChatMessage.chat_session_id == session.session_id,
                ChatMessage.role == UserRole.ASSISTANT,
            )
        )
        # Nothing is generated any more, so no attachment may be linked to the reply.
        attachment_count = await db.scalar(select(func.count()).select_from(Attachment))
    assert result is not None
    assert result.type == GenerationResultType.SUCCESS
    assert result.content == "Расчёт готов."
    assert result.update_name == "Расчёт кронштейна"
    assert result.attachment_id is None
    assert stored is not None and stored.name == "Расчёт кронштейна"
    assert assistant is not None
    assert assistant.content == "Расчёт готов."
    assert assistant.display_text == "Расчёт готов."
    assert attachment_count == 0
    assert await redis_keys.get(get_generation_key(user.uuid)) is None


async def test_process_response_keeps_an_existing_session_name(task_db, rows, redis_keys):
    user = await rows.user()
    session = await rows.chat_session(user, name="Мой расчёт")
    await rows.message(session.session_id, "Расчёт кронштейна")

    await run_task(api.process_response, user.uuid, session.session_id, payload(message="Готово."))

    async with rows.session() as db:
        result = await db.get(GenerationResult, session.session_id)
        stored = await db.scalar(select(ChatSession).where(ChatSession.session_id == session.session_id))
    assert result is not None and result.update_name is None
    assert stored is not None and stored.name == "Мой расчёт"


async def test_process_response_without_a_user_message_leaves_the_session_alone(task_db, rows, redis_keys):
    """A chat with nothing said in it has no name to take."""
    user = await rows.user()
    session = await rows.chat_session(user)

    await run_task(api.process_response, user.uuid, session.session_id, payload(message="Готово."))

    async with rows.session() as db:
        result = await db.get(GenerationResult, session.session_id)
        stored = await db.scalar(select(ChatSession).where(ChatSession.session_id == session.session_id))
    assert result is not None and result.update_name is None
    assert stored is not None and stored.name == "Новый чат"


async def test_process_response_names_a_chat_with_a_programme_after_the_first_message(task_db, rows, redis_keys):
    """A running programme takes the name of the first thing the methodist said.

    The programme's own title replaces it when the draft is rendered, so the
    chat is never nameless while it is being built.
    """
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "Программа по аналитике")
    await rows.program_state(
        session.session_id, new_state(session.session_id, requested_hours=None, requirements="Программа")
    )

    await run_task(api.process_response, user.uuid, session.session_id, payload(message="Готово."))

    async with rows.session() as db:
        result = await db.get(GenerationResult, session.session_id)
        stored = await db.scalar(select(ChatSession).where(ChatSession.session_id == session.session_id))
    assert result is not None and result.update_name == "Программа по аналитике"
    assert stored is not None and stored.name == "Программа по аналитике"


async def test_process_response_truncates_an_overlong_chat_name(task_db, rows, redis_keys, s3):
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "Д" * 200)

    await run_task(api.process_response, user.uuid, session.session_id, payload(message="Готово."))

    async with rows.session() as db:
        result = await db.get(GenerationResult, session.session_id)
        stored = await db.scalar(select(ChatSession).where(ChatSession.session_id == session.session_id))
    assert result is not None
    assert result.update_name == "Д" * (MAX_CHAT_NAME_LENGTH - 3) + "..."
    assert len(result.update_name) == MAX_CHAT_NAME_LENGTH
    assert stored is not None and stored.name == result.update_name


async def test_process_response_reports_a_failure(task_db, rows, redis_keys):
    """A failure while publishing still releases the generation lock."""
    user = await rows.user()
    session = await rows.chat_session(user)
    key = await redis_keys.put(get_generation_key(user.uuid))

    def boom(*_args, **_kwargs):
        raise RuntimeError("database is down")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(api, "ChatMessage", boom)
        await run_task(api.process_response, user.uuid, session.session_id, payload())

    async with rows.session() as db:
        result = await db.get(GenerationResult, session.session_id)
    assert result is not None
    assert result.type == GenerationResultType.ERROR
    assert await redis_keys.get(key) is None


async def test_process_response_for_a_deleted_session_still_releases_the_lock(task_db, rows, redis_keys, s3):
    """The session lookup returning ``None`` is handled, but the result insert is not.

    ``GenerationResult.chat_session_id`` references ``chat_sessions``, so once the
    row is gone the insert fails no matter what the rename guard decided. The
    ``finally`` block still releases the generation lock.
    """
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.drop_sessions([session.session_id])
    key = await redis_keys.put(get_generation_key(user.uuid))

    with pytest.raises(IntegrityError):
        await run_task(api.process_response, user.uuid, session.session_id, payload())

    assert await redis_keys.get(key) is None
