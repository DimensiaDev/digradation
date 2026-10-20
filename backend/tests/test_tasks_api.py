"""Tests for the chat workers in ``app.tasks.api``.

Every test here drives the real task bodies through :func:`tests.helpers.run_task`,
so the database, Redis and Garage work the workers do is genuinely executed. The
only thing stubbed out is the enqueue itself (``process_response.kiq``), which
would otherwise need a live RabbitMQ.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.agentic.models import ProgramStatus, Stage, StageStatus
from app.agentic.state import (
    CompetencesBlock,
    HoursBlock,
    ProgramState,
    SourcesBlock,
    SummaryBlock,
    apply_block,
    approve_state,
    mark_finalized,
    mark_stage_started,
    new_state,
)
from app.cache import get_deletion_key, get_generation_key, get_program_state_key
from app.models.chat import (
    MAX_CHAT_NAME_LENGTH,
    Attachment,
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
from app.tasks import api
from app.tasks import program as program_module
from app.tasks.program import finalize_program_draft as finalize_task
from app.tasks.program import program_stage as stage_task
from tests.helpers import run_task

# --------------------------------------------------------------------------------------
# payload helpers
# --------------------------------------------------------------------------------------


def payload(
    *,
    chat_name: str | None = "Расчёт детали",
    message: str = "Готово.",
) -> str:
    return json.dumps(
        {
            "chat_name": chat_name,
            "message": message,
        },
        ensure_ascii=False,
    )


class FalsyResponse:
    """A provider response object that is falsy but not ``None``.

    ``generate_chat_message`` guards its provider output twice: once with
    ``is None`` (which raises and is reported as an internal error) and once with
    a plain truthiness check, which only a response that is falsy without being
    ``None`` can reach.
    """

    def __bool__(self) -> bool:
        return False


# --------------------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------------------


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

    async def __call__(self, messages: list[Message], *args, **kwargs):
        self.histories.append(messages)
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
    forwarded = json.loads(args[2])
    assert set(forwarded) == {"chat_name", "message"}
    assert forwarded["message"]
    # No result is written by this task: that is process_response's job.
    async with rows.session() as db:
        assert await db.scalar(select(func.count()).select_from(GenerationResult)) == 0


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


async def test_generate_chat_message_with_falsy_provider_response_writes_an_error(
    task_db, rows, redis_keys, kiq_spy, monkeypatch
):
    monkeypatch.setattr(api.provider, "generate", AsyncMock(return_value=FalsyResponse()))
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id)
    key = redis_keys.track(get_generation_key(user.uuid))

    await run_task(api.generate_chat_message, session.session_id)

    async with rows.session() as db:
        result = await db.get(GenerationResult, session.session_id)
    assert result is not None
    assert result.type == GenerationResultType.ERROR
    assert result.content == "Internal server error occurred, please try again later or contact support."
    # This branch writes the result inline instead of going through
    # ``_add_error_result``, so nothing releases the generation lock here. It only
    # expires on its own TTL, which is what the assertion below pins down.
    assert await redis_keys.get(key) == "1"
    kiq_spy.assert_not_awaited()


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
        payload(message="Прошлый, неудачный ответ."),
        role=UserRole.ASSISTANT,
        display_text="Прошлый, неудачный ответ.",
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
    await rows.message(session.session_id, payload(), role=UserRole.ASSISTANT, display_text="Готово.")
    await rows.message(session.session_id, "И пересчитай с другой толщиной")

    await run_task(api.generate_chat_message, session.session_id)

    kiq_spy.assert_awaited_once()
    # The first answer is legitimate context; only the trailing one is dropped.
    assert provider_recorder.roles() == [UserRole.USER, UserRole.ASSISTANT, UserRole.USER]
    assert provider_recorder.texts() == ["Посчитай кронштейн", payload(), "И пересчитай с другой толщиной"]


async def test_generate_chat_message_keeps_the_whole_history_of_a_healthy_session(
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
        payload(),
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


async def test_process_response_stores_the_reply_and_renames_the_session(task_db, rows, redis_keys):
    user = await rows.user()
    session = await rows.chat_session(user)
    body = payload(chat_name="Расчёт кронштейна", message="Расчёт готов.")
    await redis_keys.put(get_generation_key(user.uuid))

    await run_task(api.process_response, user.uuid, session.session_id, body)

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
    assert assistant.content == body
    assert assistant.display_text == "Расчёт готов."
    assert attachment_count == 0
    assert await redis_keys.get(get_generation_key(user.uuid)) is None


async def test_process_response_keeps_an_existing_session_name(task_db, rows, redis_keys):
    user = await rows.user()
    session = await rows.chat_session(user, name="Мой расчёт")

    await run_task(api.process_response, user.uuid, session.session_id, payload(chat_name="Другое имя"))

    async with rows.session() as db:
        result = await db.get(GenerationResult, session.session_id)
        stored = await db.scalar(select(ChatSession).where(ChatSession.session_id == session.session_id))
    assert result is not None and result.update_name is None
    assert stored is not None and stored.name == "Мой расчёт"


async def test_process_response_without_a_chat_name_leaves_the_session_alone(task_db, rows, redis_keys):
    user = await rows.user()
    session = await rows.chat_session(user)

    await run_task(api.process_response, user.uuid, session.session_id, payload(chat_name=None))

    async with rows.session() as db:
        result = await db.get(GenerationResult, session.session_id)
        stored = await db.scalar(select(ChatSession).where(ChatSession.session_id == session.session_id))
    assert result is not None and result.update_name is None
    assert stored is not None and stored.name == "Новый чат"


async def test_process_response_truncates_an_overlong_chat_name(task_db, rows, redis_keys, s3):
    user = await rows.user()
    session = await rows.chat_session(user)

    await run_task(api.process_response, user.uuid, session.session_id, payload(chat_name="Д" * 200))

    async with rows.session() as db:
        result = await db.get(GenerationResult, session.session_id)
        stored = await db.scalar(select(ChatSession).where(ChatSession.session_id == session.session_id))
    assert result is not None
    assert result.update_name == "Д" * (MAX_CHAT_NAME_LENGTH - 3) + "..."
    assert len(result.update_name) == MAX_CHAT_NAME_LENGTH
    assert stored is not None and stored.name == result.update_name


async def test_process_response_reports_invalid_json(task_db, rows, redis_keys):
    user = await rows.user()
    session = await rows.chat_session(user)
    key = await redis_keys.put(get_generation_key(user.uuid))

    await run_task(api.process_response, user.uuid, session.session_id, "{не json")

    async with rows.session() as db:
        result = await db.get(GenerationResult, session.session_id)
    assert result is not None
    assert result.type == GenerationResultType.ERROR
    assert result.content == "Internal server error occurred"
    assert await redis_keys.get(key) is None


async def test_process_response_reports_a_schema_mismatch(task_db, rows, redis_keys):
    """Valid JSON that does not satisfy ``HarnessStructuredOutput``."""
    user = await rows.user()
    session = await rows.chat_session(user)

    # `message` is the one required field, so a payload without it is the mismatch.
    await run_task(api.process_response, user.uuid, session.session_id, json.dumps({"chat_name": "Без ответа"}))

    async with rows.session() as db:
        result = await db.get(GenerationResult, session.session_id)
    assert result is not None
    assert result.type == GenerationResultType.ERROR


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


# --------------------------------------------------------------------------------------
# generate_chat_message: the programme branch
# --------------------------------------------------------------------------------------

PROGRAM_TITLE = "Тестовая программа"


def program_response(
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


def program_json(message: str = "Программа запущена.") -> str:
    return json.dumps({"chat_name": PROGRAM_TITLE, "message": message}, ensure_ascii=False)


def begin_call() -> ToolCall:
    arguments = {
        "requested_hours": 2,
        "requirements": "Тема: анализ данных. Аудитория: взрослые.",
        "title": PROGRAM_TITLE,
    }
    return ToolCall(
        id="call-begin",
        function=FunctionCall(name="begin_program", arguments=json.dumps(arguments, ensure_ascii=False)),
    )


def _competences_block() -> CompetencesBlock:
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


def _hours_block(hours: float = 2) -> HoursBlock:
    return HoursBlock.model_validate(
        {
            "curriculum": {
                "modules": [
                    {
                        "id": "m-01",
                        "title": "Модуль 1",
                        "topics": [{"id": "t-01.1", "title": "Тема 1", "hours": {"lecture": hours}}],
                    }
                ],
                "schedule": {"days": 1, "hours_per_day": hours},
            }
        }
    )


def _summary_block() -> SummaryBlock:
    return SummaryBlock.model_validate(
        {
            "kind": "general_development",
            "title": PROGRAM_TITLE,
            "summary": {
                "goal": "Научить работать с данными",
                "target_audience": "Взрослые слушатели",
                "form": "очная",
                "issued_document": "сертификат",
            },
        }
    )


def program_started(session_id: uuid.UUID, *, run_id: str = "run-1") -> ProgramState:
    return new_state(
        session_id,
        requested_hours=2,
        requirements="Тема: анализ данных",
        title=PROGRAM_TITLE,
        run_id=run_id,
    )


def program_complete(session_id: uuid.UUID, *, run_id: str = "run-1") -> ProgramState:
    state = apply_block(program_started(session_id, run_id=run_id), Stage.COMPETENCES, _competences_block())
    state = apply_block(state, Stage.HOURS, _hours_block())
    state = apply_block(state, Stage.SOURCES, SourcesBlock(sources=[]))
    return apply_block(state, Stage.SUMMARY, _summary_block())


def program_finalized(session_id: uuid.UUID, *, run_id: str = "run-1") -> ProgramState:
    return mark_finalized(program_complete(session_id, run_id=run_id))


def program_approved(session_id: uuid.UUID, *, run_id: str = "run-1") -> ProgramState:
    return approve_state(program_finalized(session_id, run_id=run_id))


async def seed_program(rows, state: ProgramState) -> None:
    async with rows.session() as db:
        row = await db.get(ChatSession, state.meta.session_id)
        assert row is not None
        row.program_state = state.model_dump(mode="json")
        await db.commit()


async def load_program(rows, session_id: uuid.UUID) -> ProgramState | None:
    async with rows.session() as db:
        row = await db.get(ChatSession, session_id)
        if row is None or row.program_state is None:
            return None
        return ProgramState.model_validate(row.program_state)


class ProgramScript:
    """A provider whose turns are queued in advance."""

    def __init__(self, *turns: GenerationResponse):
        self._turns = list(turns)
        self.calls = 0

    async def generate(self, messages, response_format, *, x_client_id, x_session_id, tools=None):
        del messages, response_format, x_client_id, x_session_id, tools
        self.calls += 1
        return self._turns.pop(0)


@pytest.fixture()
def program_stage_spy(monkeypatch) -> AsyncMock:
    spy = AsyncMock(name="program_stage.kiq")
    monkeypatch.setattr(api.program_stage, "kiq", spy)
    return spy


async def test_an_intake_tool_call_starts_the_pipeline(
    task_db, rows, redis_keys, kiq_spy, program_stage_spy, mock_mode, monkeypatch
):
    """The model asks for ``begin_program``; the worker persists the run and enqueues stage one."""
    mock_mode("ok")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "Сделай программу по анализу данных на 2 часа")
    script = ProgramScript(program_response(calls=[begin_call()]), program_response(program_json()))
    monkeypatch.setattr(api.provider, "generate", script.generate)
    state_key = redis_keys.track(get_program_state_key(session.session_id))

    await run_task(api.generate_chat_message, session.session_id)

    state = await load_program(rows, session.session_id)
    assert state is not None
    assert state.stage_status(Stage.COMPETENCES) is StageStatus.IN_PROGRESS
    assert state.requested_hours == 2
    assert script.calls == 2
    program_stage_spy.assert_awaited_once_with(session.session_id, Stage.COMPETENCES, state.meta.run_id)
    kiq_spy.assert_awaited_once()
    assert json.loads(kiq_spy.await_args.args[2])["message"] == "Программа запущена."
    assert await redis_keys.get(state_key) is not None


async def test_a_failed_run_can_be_restarted_with_a_new_run_id(
    task_db, rows, redis_keys, kiq_spy, program_stage_spy, mock_mode, monkeypatch
):
    mock_mode("ok")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "Попробуй ещё раз")
    failed = program_started(session.session_id, run_id="old-run").model_copy(update={"failed": True})
    await seed_program(rows, failed)
    script = ProgramScript(program_response(calls=[begin_call()]), program_response(program_json()))
    monkeypatch.setattr(api.provider, "generate", script.generate)

    await run_task(api.generate_chat_message, session.session_id)

    state = await load_program(rows, session.session_id)
    assert state is not None
    assert state.meta.run_id != "old-run"
    assert state.failed is False
    program_stage_spy.assert_awaited_once_with(session.session_id, Stage.COMPETENCES, state.meta.run_id)


async def test_a_message_during_generation_gets_a_waiting_reply(
    task_db, rows, redis_keys, kiq_spy, mock_mode, monkeypatch
):
    mock_mode("ok")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "Что там с программой?")
    redis_keys.track(get_generation_key(user.uuid))
    state = mark_stage_started(program_started(session.session_id), Stage.COMPETENCES)
    await seed_program(rows, state)
    script = ProgramScript()
    monkeypatch.setattr(api.provider, "generate", script.generate)

    await run_task(api.generate_chat_message, session.session_id)

    assert script.calls == 0
    payload = json.loads(kiq_spy.await_args.args[2])
    assert "ещё идёт" in payload["message"]
    unchanged = await load_program(rows, session.session_id)
    assert unchanged is not None
    assert unchanged.meta.run_id == "run-1"


async def test_a_non_approval_message_on_a_ready_draft_gets_an_explanation(
    task_db, rows, redis_keys, kiq_spy, mock_mode, monkeypatch
):
    mock_mode("ok")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "Переделайте третий модуль")
    await seed_program(rows, program_finalized(session.session_id))
    script = ProgramScript()
    monkeypatch.setattr(api.provider, "generate", script.generate)

    await run_task(api.generate_chat_message, session.session_id)

    assert script.calls == 0
    payload = json.loads(kiq_spy.await_args.args[2])
    assert "ждёт подтверждения" in payload["message"]
    state = await load_program(rows, session.session_id)
    assert state is not None
    assert state.status is ProgramStatus.PENDING_APPROVAL


async def test_a_draft_that_has_not_been_rendered_is_not_approved(
    task_db, rows, redis_keys, kiq_spy, mock_mode, monkeypatch
):
    """Approval is only possible after the draft exists in the chat."""
    mock_mode("ok")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "подтверждаю")
    await seed_program(rows, program_complete(session.session_id))
    script = ProgramScript()
    monkeypatch.setattr(api.provider, "generate", script.generate)

    await run_task(api.generate_chat_message, session.session_id)

    assert script.calls == 0
    state = await load_program(rows, session.session_id)
    assert state is not None
    assert state.status is ProgramStatus.PENDING_APPROVAL
    assert state.finalized is False
    payload = json.loads(kiq_spy.await_args.args[2])
    assert "ещё готовится" in payload["message"]


async def test_an_explicit_confirmation_approves_the_draft(task_db, rows, redis_keys, kiq_spy, mock_mode, monkeypatch):
    mock_mode("ok")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "подтверждаю")
    await seed_program(rows, program_finalized(session.session_id))
    script = ProgramScript()
    monkeypatch.setattr(api.provider, "generate", script.generate)

    await run_task(api.generate_chat_message, session.session_id)

    assert script.calls == 0
    state = await load_program(rows, session.session_id)
    assert state is not None
    assert state.status is ProgramStatus.APPROVED
    assert state.approved_at is not None
    payload = json.loads(kiq_spy.await_args.args[2])
    assert "утверждён" in payload["message"]


async def test_an_invalid_ready_draft_is_not_approved(task_db, rows, redis_keys, kiq_spy, mock_mode, monkeypatch):
    """A state that skipped validation must not pass the approval gate."""
    mock_mode("ok")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "подтверждаю")
    broken = program_finalized(session.session_id).model_copy(update={"curriculum": _hours_block(10).curriculum})
    await seed_program(rows, broken)
    script = ProgramScript()
    monkeypatch.setattr(api.provider, "generate", script.generate)

    await run_task(api.generate_chat_message, session.session_id)

    state = await load_program(rows, session.session_id)
    assert state is not None
    assert state.status is ProgramStatus.PENDING_APPROVAL
    payload = json.loads(kiq_spy.await_args.args[2])
    assert "не прошёл проверку" in payload["message"]


async def test_an_approval_that_cannot_be_saved_is_reported(task_db, rows, redis_keys, kiq_spy, mock_mode, monkeypatch):
    mock_mode("ok")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "подтверждаю")
    await seed_program(rows, program_finalized(session.session_id))

    class FailingStore:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def save(self, *args, **kwargs):
            del args, kwargs
            return

    monkeypatch.setattr(api, "ProgramStateStore", FailingStore)

    await run_task(api.generate_chat_message, session.session_id)

    payload = json.loads(kiq_spy.await_args.args[2])
    assert "Не удалось сохранить" in payload["message"]


async def test_an_approved_program_continues_as_a_normal_chat(
    task_db, rows, redis_keys, kiq_spy, mock_mode, monkeypatch
):
    mock_mode("ok")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "Расскажи подробнее о модуле")
    await seed_program(rows, program_approved(session.session_id))
    script = ProgramScript(program_response(program_json("Подробности.")))
    monkeypatch.setattr(api.provider, "generate", script.generate)

    await run_task(api.generate_chat_message, session.session_id)

    assert script.calls == 1
    assert json.loads(kiq_spy.await_args.args[2])["message"] == "Подробности."
    state = await load_program(rows, session.session_id)
    assert state is not None
    assert state.status is ProgramStatus.APPROVED


async def test_an_unreadable_program_state_is_treated_as_an_ordinary_chat(
    task_db, rows, redis_keys, kiq_spy, mock_mode, monkeypatch
):
    mock_mode("ok")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "Привет")
    async with rows.session() as db:
        row = await db.get(ChatSession, session.session_id)
        row.program_state = {"unexpected": "shape"}
        await db.commit()
    script = ProgramScript(program_response(program_json("Обычный ответ.")))
    monkeypatch.setattr(api.provider, "generate", script.generate)

    await run_task(api.generate_chat_message, session.session_id)

    assert script.calls == 1
    assert json.loads(kiq_spy.await_args.args[2])["message"] == "Обычный ответ."


async def test_the_stage_mock_drives_the_intake_through_a_tool_call(
    task_db, rows, redis_keys, kiq_spy, program_stage_spy, mock_mode
):
    """The offline provider's ``stage`` mode is what lets the cycle run without an LLM."""
    mock_mode("stage")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "Сделай программу по анализу данных")
    redis_keys.track(get_program_state_key(session.session_id))

    await run_task(api.generate_chat_message, session.session_id)

    state = await load_program(rows, session.session_id)
    assert state is not None
    assert state.stage_status(Stage.COMPETENCES) is StageStatus.IN_PROGRESS
    program_stage_spy.assert_awaited_once_with(session.session_id, Stage.COMPETENCES, state.meta.run_id)
    payload = json.loads(kiq_spy.await_args.args[2])
    assert payload["message"]


async def test_the_stage_mock_runs_the_whole_cycle_to_a_finalised_draft(
    task_db, rows, redis_keys, kiq_spy, program_stage_spy, mock_mode, monkeypatch
):
    """Drive the intake and all four stages with the offline provider's stage script."""
    mock_mode("stage")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "Сделай программу по анализу данных на 6 часов")
    redis_keys.track(get_program_state_key(session.session_id))
    stage_enqueue = AsyncMock()
    finalize_enqueue = AsyncMock()
    monkeypatch.setattr(program_module, "program_stage", SimpleNamespace(kiq=stage_enqueue))
    monkeypatch.setattr(program_module, "finalize_program_draft", SimpleNamespace(kiq=finalize_enqueue))

    await run_task(api.generate_chat_message, session.session_id)
    state = await load_program(rows, session.session_id)
    assert state is not None
    run_id = state.meta.run_id
    program_stage_spy.assert_awaited_once_with(session.session_id, Stage.COMPETENCES, run_id)

    for stage in (Stage.COMPETENCES, Stage.HOURS, Stage.SOURCES, Stage.SUMMARY):
        await run_task(stage_task, session.session_id, stage, run_id)
        state = await load_program(rows, session.session_id)
        assert state is not None

    assert state.status is ProgramStatus.PENDING_APPROVAL
    finalize_enqueue.assert_awaited_once_with(session.session_id, run_id)

    await run_task(finalize_task, session.session_id, run_id)

    state = await load_program(rows, session.session_id)
    assert state is not None
    assert state.finalized is True
    async with rows.session() as db:
        chat = await db.get(ChatSession, session.session_id)
        result = await db.scalar(select(GenerationResult).where(GenerationResult.chat_session_id == session.session_id))
    assert chat is not None
    assert chat.name == "Офлайн-программа (mock)"
    assert result is not None
    assert "Черновик программы готов к подтверждению." in result.content
    kiq_spy.assert_awaited_once()


async def test_a_failed_program_is_not_answered_with_the_approval_prompt(
    task_db, rows, redis_keys, kiq_spy, mock_mode, monkeypatch
):
    """A terminal run falls through to the model, which can start a new one."""
    mock_mode("ok")
    user = await rows.user()
    session = await rows.chat_session(user)
    await rows.message(session.session_id, "Давай начнём заново")
    failed = program_complete(session.session_id).model_copy(update={"failed": True})
    await seed_program(rows, failed)
    script = ProgramScript(program_response(program_json("Начинаем заново.")))
    monkeypatch.setattr(api.provider, "generate", script.generate)

    await run_task(api.generate_chat_message, session.session_id)

    assert script.calls == 1
    assert json.loads(kiq_spy.await_args.args[2])["message"] == "Начинаем заново."
    state = await load_program(rows, session.session_id)
    assert state is not None
    assert state.status is ProgramStatus.PENDING_APPROVAL
