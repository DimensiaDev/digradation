"""Cover the remaining gaps: the mock provider's own branches, the lock/verify
dependencies, admin error paths, and the few schema and model guards.

The offline provider is production code (it ships in the image whenever
``SBER_API_KEY=mock``), so its modes and determinism are worth asserting
directly rather than only through the worker that uses it.
"""

import json
import uuid

import pytest

from app.providers.mock import (
    DEFAULT_MODE,
    MODES,
    MockProvider,
    _requested_hours,
    _seed,
    get_mock_mode,
)

# --- app.providers.mock ---------------------------------------------------


def test_get_mock_mode_defaults_and_validation(monkeypatch):
    monkeypatch.delenv("MOCK_PROVIDER_MODE", raising=False)
    assert get_mock_mode() == DEFAULT_MODE

    for mode in MODES:
        monkeypatch.setenv("MOCK_PROVIDER_MODE", mode.upper())
        assert get_mock_mode() == mode

    monkeypatch.setenv("MOCK_PROVIDER_MODE", "nonsense")
    assert get_mock_mode() == DEFAULT_MODE


def test_mock_provider_rejects_bad_scope():
    with pytest.raises(ValueError, match="Scope must be"):
        MockProvider("mock", "WRONG")


@pytest.mark.parametrize("scope", ["PERS", "B2B", "CORP", "openai", "mock"])
def test_mock_provider_accepts_every_selectable_scope(scope):
    # ``SBER_API_KEY=mock`` selects the mock whatever the scope, so it must not
    # reject a scope the container can hand it (``openai`` included).
    assert MockProvider("mock", scope).scope == scope


async def test_mock_auth_sets_a_long_lived_token():
    provider = MockProvider("mock", "PERS")
    assert provider.token is None
    await provider.auth()
    assert provider.token is not None
    assert provider.token.startswith("mock-token-")
    # ensure_fresh_token must not re-auth immediately afterwards.
    before = provider.token
    await provider.ensure_fresh_token()
    assert provider.token == before


async def test_mock_upload_returns_a_stable_id():
    provider = MockProvider("mock", "PERS")
    client_id, session_id = uuid.uuid4(), uuid.uuid4()
    first = await provider.upload("a.png", b"data", client_id, session_id, "image/png")
    second = await provider.upload("a.png", b"data", client_id, session_id, "image/png")
    other = await provider.upload("b.png", b"data", client_id, uuid.uuid4(), "image/png")
    assert first == second
    assert first != other
    assert first.startswith("mock-file-")


def _messages(text: str):
    from app.providers.models import Content, Message

    return [Message(role="user", content=[Content(text=text)])]


async def _generate(provider, text: str = "hello"):
    from app.providers.models import ResponseFormat

    return await provider.generate(
        _messages(text),
        ResponseFormat(type="text"),
        x_client_id=uuid.uuid4(),
        x_session_id=uuid.uuid4(),
    )


def _payload(response) -> dict:
    return json.loads(response.messages[0].content[0].text)


async def test_mock_ok_mode_returns_the_chat_contract(monkeypatch):
    monkeypatch.setenv("MOCK_PROVIDER_MODE", "ok")
    provider = MockProvider("mock", "PERS")
    payload = _payload(await _generate(provider))

    assert set(payload) == {"chat_name", "message"}
    assert payload["message"]
    assert payload["chat_name"]


async def test_mock_ok_mode_is_deterministic(monkeypatch):
    """The same conversation must produce the same reply, or e2e assertions drift."""
    monkeypatch.setenv("MOCK_PROVIDER_MODE", "ok")
    first = _payload(await _generate(MockProvider("mock", "PERS")))
    second = _payload(await _generate(MockProvider("mock", "PERS")))
    assert first == second


async def test_mock_error_mode_returns_none(monkeypatch):
    monkeypatch.setenv("MOCK_PROVIDER_MODE", "error")
    assert await _generate(MockProvider("mock", "PERS")) is None


async def test_mock_empty_mode_returns_a_message_with_no_content(monkeypatch):
    monkeypatch.setenv("MOCK_PROVIDER_MODE", "empty")
    response = await _generate(MockProvider("mock", "PERS"))
    assert response is not None
    assert response.messages[0].content == []


async def test_mock_output_is_deterministic():
    provider = MockProvider("mock", "PERS")
    first = _payload(await _generate(provider, "same input"))
    second = _payload(await _generate(provider, "same input"))
    assert first == second

    different = _payload(await _generate(provider, "other input"))
    assert different != first


def test_seed_is_stable_and_varies():
    assert _seed("a", "b") == _seed("a", "b")
    assert _seed("a", "b") != _seed("b", "a")


def test_requested_hours_reads_the_methodists_own_wording():
    assert _requested_hours("Сделайте программу на 20 часов") == 20.0
    assert _requested_hours("Программа без объёма") is None


@pytest.mark.parametrize(("text", "hours"), [("Программа на 40 часов", 40.0), ("Программа без объёма", None)])
async def test_mock_stage_starts_a_run_from_the_methodists_message(monkeypatch, text, hours):
    from app.providers.models import FunctionDefinition, ResponseFormat, Tool

    monkeypatch.setenv("MOCK_PROVIDER_MODE", "stage")
    provider = MockProvider("mock", "PERS")
    begin = Tool(function=FunctionDefinition(name="begin_program"))

    response = await provider.generate(
        _messages(text),
        ResponseFormat(type="text"),
        x_client_id=uuid.uuid4(),
        x_session_id=uuid.uuid4(),
        tools=[begin],
    )

    assert response is not None
    assert [call.function.name for call in response.tool_calls] == ["begin_program"]
    arguments = json.loads(response.tool_calls[0].function.arguments)
    assert arguments["requirements"] == text
    if hours is None:
        assert "requested_hours" not in arguments
    else:
        assert arguments["requested_hours"] == hours


async def test_mock_stage_without_a_recognised_tool_falls_back_to_the_chat_contract(monkeypatch):
    from app.providers.models import ResponseFormat

    monkeypatch.setenv("MOCK_PROVIDER_MODE", "stage")
    provider = MockProvider("mock", "PERS")

    response = await provider.generate(
        _messages("привет"),
        ResponseFormat(type="text"),
        x_client_id=uuid.uuid4(),
        x_session_id=uuid.uuid4(),
        tools=[],
    )

    assert response is not None
    assert set(_payload(response)) == {"chat_name", "message"}


def test_mock_stage_answer_is_json_when_a_schema_is_requested():
    from app.providers.models import ResponseFormat

    provider = MockProvider("mock", "PERS")

    answer = provider._stage_answer(ResponseFormat(type="json_schema", schema={"type": "object"}, strict=False))

    payload = json.loads(answer)
    assert payload["chat_name"] is None
    assert payload["message"]


async def test_mock_close_is_safe():
    # The base close() calls httpx; the mock never builds a client, so closing it
    # should not explode. Guard the behaviour rather than assuming.
    provider = MockProvider("mock", "PERS")
    assert not hasattr(provider, "_client")


# --- app.deps -------------------------------------------------------------


async def test_rate_limiter_blocks_after_the_limit(redis_client, test_user):
    from datetime import timedelta

    from fastapi import HTTPException

    from app.deps import user_rate_limiter

    limiter = user_rate_limiter(2, timedelta(minutes=5), "test:blocked")
    for _ in range(2):
        await limiter(redis_client=redis_client, user=test_user)
    with pytest.raises(HTTPException) as exc:
        await limiter(redis_client=redis_client, user=test_user)
    assert exc.value.status_code == 429
    assert "Retry-After" in exc.value.headers


async def test_avatar_url_cache_key_is_invalidated(redis_session, test_user):
    from app.cache import get_avatar_url_key, get_redis_client

    key = get_avatar_url_key(test_user.uuid)
    async with get_redis_client() as redis:
        await redis.set(key, "https://example.test/a.webp")
        assert await redis.get(key) is not None
        await redis.delete(key)
        assert await redis.get(key) is None


# --- app.routes.admin -----------------------------------------------------


async def test_admin_blocks_password_set_for_superuser(admin_client, test_admin_user):
    response = await admin_client.post(
        f"/admin/users/{test_admin_user.id}/set-password",
        json={"password": "newpassword123"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "You can't change password of superuser"


async def test_admin_blocks_password_set_under_test_instance_mode(admin_client, test_user, monkeypatch):
    monkeypatch.setenv("TEST_INSTANCE_MODE", "true")
    response = await admin_client.post(
        f"/admin/users/{test_user.id}/set-password",
        json={"password": "whatever12345"},
    )
    assert response.status_code == 450


async def test_admin_cannot_delete_a_superuser(admin_client, test_admin_user):
    response = await admin_client.delete(f"/admin/users/{test_admin_user.id}")
    assert response.status_code == 403
    assert response.json()["detail"] == "Superuser can't be deleted"


async def test_admin_edit_rejects_a_duplicate_username(admin_client, test_user, test_100_users):
    response = await admin_client.patch(
        f"/admin/users/{test_user.id}",
        json={"username": test_100_users[0].username},
    )
    assert response.status_code == 409


async def test_admin_set_password_unknown_user(admin_client):
    response = await admin_client.post("/admin/users/999999/set-password", json={"password": "abcdefgh123"})
    assert response.status_code == 404
    assert response.json()["detail"] == "User not found"


# --- models and schemas ---------------------------------------------------


def test_chat_message_rejects_system_display():
    from app.models.chat import ChatMessage, UserRole

    message = ChatMessage(role=UserRole.SYSTEM, content="x", chat_session_id=uuid.uuid4())
    with pytest.raises(ValueError, match="Cannot display system messages"):
        message.get_chat_text()

    normal = ChatMessage(
        role=UserRole.ASSISTANT,
        content="raw",
        display_text="pretty",
        chat_session_id=uuid.uuid4(),
    )
    assert normal.get_chat_text() == "pretty"

    fallback = ChatMessage(role=UserRole.USER, content="raw", chat_session_id=uuid.uuid4())
    assert fallback.get_chat_text() == "raw"


async def test_verify_chat_session_rejects_a_foreign_session(db_session, test_user):
    from fastapi import HTTPException

    from app.deps import verify_chat_session

    with pytest.raises(HTTPException) as exc:
        await verify_chat_session(session_id=uuid.uuid4(), user=test_user, db=db_session)
    assert exc.value.status_code == 403
    assert exc.value.detail == "Invalid session"


async def test_verify_attachment_id_rejects_a_missing_attachment(db_session, test_user, redis_session):
    from fastapi import HTTPException

    from app.cache import get_redis_client
    from app.deps import verify_attachment_id

    async with get_redis_client() as redis:
        with pytest.raises(HTTPException) as exc:
            await verify_attachment_id(
                session_id=uuid.uuid4(),
                attachment_id=4242,
                db=db_session,
                redis=redis,
            )
    assert exc.value.status_code == 403
    assert exc.value.detail == "Invalid session"


async def test_chat_lock_rejects_when_generation_is_running(redis_session, test_user):
    from fastapi import HTTPException

    from app.cache import get_generation_key, get_redis_client
    from app.deps import chat_lock

    async with get_redis_client() as redis:
        await redis.set(get_generation_key(test_user.uuid), "1", ex=60)

        # chat_lock is a bare async generator (FastAPI turns it into a
        # dependency), so drive it with __anext__ rather than `async with`.
        with pytest.raises(HTTPException) as exc:
            await chat_lock(redis_client=redis, user=test_user).__anext__()
    assert exc.value.status_code == 409
    assert exc.value.detail == "A generation job is already running"


async def test_chat_lock_throttles_repeated_creation(redis_session, test_user):
    from fastapi import HTTPException

    from app.cache import get_creation_key, get_redis_client
    from app.deps import chat_lock

    async with get_redis_client() as redis:
        await redis.set(get_creation_key(test_user.uuid), "1", ex=60)
        with pytest.raises(HTTPException) as exc:
            await chat_lock(redis_client=redis, user=test_user).__anext__()
    assert exc.value.status_code == 429
