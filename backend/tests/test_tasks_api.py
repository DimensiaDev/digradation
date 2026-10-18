"""Tests for the chat workers in ``app.tasks.api``.

Every test here drives the real task bodies through :func:`tests.helpers.run_task`,
so the database, Redis and Garage work the workers do is genuinely executed. The
only thing stubbed out is the enqueue itself (``process_response.kiq``), which
would otherwise need a live RabbitMQ.
"""

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.agentic.models import ProgramStatus
from app.auth import hash_password
from app.cache import get_deletion_key, get_generation_key, get_redis_client
from app.models.auth import User
from app.models.chat import (
    Attachment,
    ChatMessage,
    ChatSession,
    GenerationResult,
    GenerationResultType,
    UserRole,
)
from app.models.program import ChatProgram
from app.providers.mock import MOCK_DRAFT_ARGUMENTS
from app.providers.models import Message
from app.tasks import api
from tests.helpers import run_task

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
    completion; only the message list and the call arguments are captured, which
    is what the retry and tool-passing regression tests assert on.
    """

    def __init__(self, original) -> None:
        self._original = original
        self.histories: list[list[Message]] = []
        self.calls: list[dict] = []

    async def __call__(self, messages: list[Message], *args, **kwargs):
        self.histories.append(messages)
        self.calls.append({"args": args, "kwargs": kwargs})
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
# generate_chat_message
# --------------------------------------------------------------------------------------


async def test_generate_chat_message_enqueues_process_response(task_db, rows, redis_keys, kiq_spy, mock_mode):
    mock_mode("ok")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "Посчитай кронштейн")

    await run_task(api.generate_chat_message, session.session_id)

    kiq_spy.assert_awaited_once()
    args = kiq_spy.await_args.args
    assert args[0] == user.uuid
    assert args[1] == session.session_id
    assert isinstance(args[2], str) and args[2]
    # The `ok` reply names nothing, so there is no rename to forward.
    assert args[3] is None
    # No result is written by this task: that is process_response's job.
    async with rows.session() as db:
        assert await db.scalar(select(func.count()).select_from(GenerationResult)) == 0


async def test_generate_chat_message_sends_the_agent_tools(
    task_db, rows, redis_keys, kiq_spy, mock_mode, provider_recorder
):
    """Every round of the loop offers the same user functions to the model."""
    mock_mode("ok")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id)

    await run_task(api.generate_chat_message, session.session_id)

    assert provider_recorder.calls
    kwargs = provider_recorder.calls[0]["kwargs"]
    assert kwargs["tool_config"].mode == "auto"
    names = [spec.name for spec in kwargs["tools"][0].functions.specifications]
    assert names == ["search_sources", "save_program_draft", "get_program_draft"]
    assert kwargs["x_session_id"] == session.session_id


async def test_generate_chat_message_clears_a_stale_result_and_takes_the_lock(
    task_db, rows, redis_keys, kiq_spy, mock_mode
):
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

    await run_task(api.generate_chat_message, session.session_id)

    async with rows.session() as db:
        assert await db.get(GenerationResult, session.session_id) is None
    assert await redis_keys.get(get_generation_key(user.uuid)) == "1"
    kiq_spy.assert_awaited_once()


async def test_generate_chat_message_without_messages_writes_an_error(task_db, rows, redis_keys, kiq_spy, mock_mode):
    mock_mode("ok")
    user = await rows.user()
    session = await rows.chat_session(user)
    key = redis_keys.track(get_generation_key(user.uuid))

    await run_task(api.generate_chat_message, session.session_id)

    async with rows.session() as db:
        result = await db.get(GenerationResult, session.session_id)
    assert result is not None
    assert result.type == GenerationResultType.ERROR
    assert result.content == "Invalid message session: no messages to send"
    assert await redis_keys.get(key) is None
    kiq_spy.assert_not_awaited()


async def test_generate_chat_message_with_null_provider_response_writes_an_error(
    task_db, rows, redis_keys, kiq_spy, mock_mode
):
    mock_mode("error")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id)
    key = redis_keys.track(get_generation_key(user.uuid))

    await run_task(api.generate_chat_message, session.session_id)

    async with rows.session() as db:
        result = await db.get(GenerationResult, session.session_id)
    assert result is not None
    assert result.type == GenerationResultType.ERROR
    assert result.content == "Internal server error occurred"
    assert await redis_keys.get(key) is None
    kiq_spy.assert_not_awaited()


async def test_generate_chat_message_with_contentless_response_writes_an_error(
    task_db, rows, redis_keys, kiq_spy, mock_mode
):
    mock_mode("empty")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id)
    key = redis_keys.track(get_generation_key(user.uuid))

    await run_task(api.generate_chat_message, session.session_id)

    async with rows.session() as db:
        result = await db.get(GenerationResult, session.session_id)
    assert result is not None
    assert result.type == GenerationResultType.ERROR
    assert result.content == "Provider did not respond properly"
    assert await redis_keys.get(key) is None
    kiq_spy.assert_not_awaited()


async def test_generate_chat_message_saves_the_draft_and_forwards_the_name(
    task_db, rows, redis_keys, kiq_spy, mock_mode, provider_recorder
):
    """The `draft` mock runs a whole methodist turn: call, execute, answer.

    The model asks for ``save_program_draft``, the loop executes it against the
    real database and hands the result back, and the reply that reaches
    ``process_response`` carries the name the handler gave the chat.
    """
    mock_mode("draft")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "Сделай программу по искусственному интеллекту")

    await run_task(api.generate_chat_message, session.session_id)

    async with rows.session() as db:
        program = await db.get(ChatProgram, session.session_id)
        stored = await db.scalar(select(ChatSession).where(ChatSession.session_id == session.session_id))
    assert program is not None
    assert program.status == ProgramStatus.PENDING_APPROVAL
    assert program.draft["title"] == MOCK_DRAFT_ARGUMENTS["title"]
    assert stored is not None
    assert stored.name == MOCK_DRAFT_ARGUMENTS["title"]

    # The second round is the one that sees the function result.
    assert len(provider_recorder.histories) == 2
    tool_message = provider_recorder.histories[1][-1]
    assert tool_message.role == "tool"
    assert tool_message.content[0].function_result.name == "save_program_draft"
    assert tool_message.content[0].function_result.result["ok"] is True

    kiq_spy.assert_awaited_once()
    args = kiq_spy.await_args.args
    assert args[3] == MOCK_DRAFT_ARGUMENTS["title"]


async def test_generate_chat_message_stops_on_a_deletion_tombstone(task_db, rows, redis_keys, kiq_spy, mock_mode):
    mock_mode("ok")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id)
    await redis_keys.put(get_deletion_key(session.session_id))

    await run_task(api.generate_chat_message, session.session_id)

    kiq_spy.assert_not_awaited()
    # The response is dropped, but process_response never runs, so nothing is stored.
    async with rows.session() as db:
        assert await db.scalar(select(func.count()).select_from(GenerationResult)) == 0


async def test_generate_chat_message_skips_when_the_generation_lock_is_held(
    task_db, rows, redis_keys, kiq_spy, mock_mode
):
    mock_mode("ok")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id)
    key = await redis_keys.put(get_generation_key(user.uuid))

    await run_task(api.generate_chat_message, session.session_id)

    assert await redis_keys.get(key) == "1", "the running task keeps the lock, not this one"
    kiq_spy.assert_not_awaited()
    async with rows.session() as db:
        assert await db.scalar(select(func.count()).select_from(GenerationResult)) == 0


async def test_generate_chat_message_without_an_owning_user_reports_an_error(task_db, redis_keys, monkeypatch):
    add_error = AsyncMock()
    monkeypatch.setattr(api, "_add_error_result", add_error)
    session = uuid.uuid4()

    await run_task(api.generate_chat_message, session)

    add_error.assert_awaited_once()
    assert add_error.await_args.args[1] == session
    assert add_error.await_args.args[2] == "Invalid message session"


async def test_generate_chat_message_for_a_missing_session_hits_the_foreign_key(task_db, redis_keys):
    """The ``no_user_for_session`` guard cannot persist anything for a phantom session.

    ``_add_error_result`` inserts a ``GenerationResult`` keyed by the session id,
    and that column references ``chat_sessions``. When the session really is gone
    the insert fails, so the task raises instead of returning quietly.
    """
    with pytest.raises(IntegrityError):
        await run_task(api.generate_chat_message, uuid.uuid4())


async def test_generate_chat_message_reports_an_unexpected_failure(task_db, rows, redis_keys, mock_mode):
    """A failure outside the provider call still produces an error result and unlocks."""
    mock_mode("ok")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id)
    key = redis_keys.track(get_generation_key(user.uuid))
    boom = AsyncMock(side_effect=RuntimeError("broker is down"))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(api.process_response, "kiq", boom)
        await run_task(api.generate_chat_message, session.session_id)

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
        stored(UserRole.ASSISTANT, "уточните"),
    ]

    kept = api._history_up_to_last_user_message(rows)

    assert [m.content for m in kept] == ["system", "спроси"]


def test_history_keeps_assistant_turns_before_the_last_user_message():
    """Earlier answers are context: the harness prompt asks what to do with them."""
    rows = [
        stored(UserRole.SYSTEM, "system"),
        stored(UserRole.USER, "посчитай"),
        stored(UserRole.ASSISTANT, "старый ответ"),
        stored(UserRole.USER, "а теперь пересчитай"),
    ]

    kept = api._history_up_to_last_user_message(rows)

    assert [m.content for m in kept] == ["system", "посчитай", "старый ответ", "а теперь пересчитай"]


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
# generate_chat_message: the history sent to the provider
# --------------------------------------------------------------------------------------


async def test_generate_chat_message_does_not_resend_the_assistant_turn_being_retried(
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
        "Прошлый, неудачный ответ.",
        role=UserRole.ASSISTANT,
    )
    await rows.result(session.session_id, type=GenerationResultType.ERROR, content="Internal server error occurred")

    await run_task(api.generate_chat_message, session.session_id)

    kiq_spy.assert_awaited_once()
    assert provider_recorder.roles() == [UserRole.SYSTEM, UserRole.USER]
    assert provider_recorder.texts() == ["system prompt", "Посчитай кронштейн"]


async def test_generate_chat_message_keeps_an_assistant_turn_that_precedes_the_last_user_message(
    task_db, rows, redis_keys, kiq_spy, mock_mode, provider_recorder
):
    """An answer the user has already seen is context, not something to cut."""
    mock_mode("ok")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "Посчитай кронштейн")
    await rows.message(session.session_id, "Готово.", role=UserRole.ASSISTANT)
    await rows.message(session.session_id, "И пересчитай с другой толщиной")

    await run_task(api.generate_chat_message, session.session_id)

    kiq_spy.assert_awaited_once()
    # The first answer is legitimate context; only the trailing one is dropped.
    assert provider_recorder.roles() == [UserRole.USER, UserRole.ASSISTANT, UserRole.USER]
    assert provider_recorder.texts() == ["Посчитай кронштейн", "Готово.", "И пересчитай с другой толщиной"]


async def test_generate_chat_message_keeps_the_whole_history_of_a_healthy_session(
    task_db, rows, redis_keys, kiq_spy, mock_mode, provider_recorder
):
    """A plain turn ending on a user message reaches the provider untouched."""
    mock_mode("ok")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "system prompt", role=UserRole.SYSTEM)
    await rows.message(session.session_id, "Посчитай кронштейн")
    await rows.message(session.session_id, "Готово.", role=UserRole.ASSISTANT)
    await rows.message(session.session_id, "А ещё одна деталь")

    await run_task(api.generate_chat_message, session.session_id)

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
        "Готово.",
        "А ещё одна деталь",
    ]


async def test_generate_chat_message_without_a_user_message_writes_an_error(
    task_db, rows, redis_keys, kiq_spy, mock_mode, provider_recorder
):
    """A history that holds no user turn has nothing to answer."""
    mock_mode("ok")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "system prompt", role=UserRole.SYSTEM)
    redis_keys.track(get_generation_key(user.uuid))

    await run_task(api.generate_chat_message, session.session_id)

    async with rows.session() as db:
        result = await db.get(GenerationResult, session.session_id)
    assert result is not None
    assert result.type == GenerationResultType.ERROR
    assert result.content == "Invalid message session: no messages to send"
    assert provider_recorder.histories == []
    kiq_spy.assert_not_awaited()


# --------------------------------------------------------------------------------------
# process_response
# --------------------------------------------------------------------------------------


async def test_process_response_stores_the_reply_and_forwards_the_name(task_db, rows, redis_keys):
    user = await rows.user()
    session = await rows.chat_session(user)
    await redis_keys.put(get_generation_key(user.uuid))

    await run_task(api.process_response, user.uuid, session.session_id, "Расчёт готов.", "Расчёт кронштейна")

    async with rows.session() as db:
        result = await db.get(GenerationResult, session.session_id)
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
    assert assistant is not None
    assert assistant.content == "Расчёт готов."
    # The reply is already readable, so there is no display text to extract.
    assert assistant.display_text is None
    assert attachment_count == 0
    assert await redis_keys.get(get_generation_key(user.uuid)) is None


async def test_process_response_without_a_name_stores_the_plain_reply(task_db, rows, redis_keys):
    user = await rows.user()
    session = await rows.chat_session(user)

    await run_task(api.process_response, user.uuid, session.session_id, "Сначала уточните аудиторию.")

    async with rows.session() as db:
        result = await db.get(GenerationResult, session.session_id)
        stored = await db.scalar(select(ChatSession).where(ChatSession.session_id == session.session_id))
    assert result is not None
    assert result.content == "Сначала уточните аудиторию."
    assert result.update_name is None
    assert stored is not None and stored.name == "Новый чат"


async def test_process_response_for_a_deleted_session_still_releases_the_lock(task_db, rows, redis_keys):
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
        await run_task(api.process_response, user.uuid, session.session_id, "Готово.")

    assert await redis_keys.get(key) is None
