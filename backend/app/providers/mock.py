import hashlib
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal, get_args

from app.providers.client import AIClient, Scope
from app.providers.models import (
    Content,
    GenerationResponse,
    InputTokenDetails,
    Message,
    ResponseFormat,
    Tool,
    Usage,
)

MOCK_API_KEY = "mock"

#: Scopes the offline provider accepts: every provider the container can select,
#: because the mock stands in for whichever one ``SBER_API_SCOPE`` names. Derived
#: from ``Scope`` so a new scope cannot be added without the mock accepting it —
#: ``SBER_API_KEY=mock`` picks the mock regardless of scope, and a scope it
#: rejected would fail at import instead.
MOCK_SCOPES = get_args(Scope)

Mode = Literal["ok", "empty", "error"]

#: Modes the mock understands. ``ok`` is the happy path: a well-formed reply and a
#: chat name. ``empty`` returns a response with no content at all, and ``error``
#: returns ``None``, both of which drive the backend's error paths.
MODES: tuple[Mode, ...] = ("ok", "empty", "error")

DEFAULT_MODE: Mode = "ok"

MOCK_CHAT_NAME = "Чат (mock)"


def get_mock_mode() -> Mode:
    """Read the mode from the environment, falling back to the happy path.

    Kept as a function (not a module constant) so tests can change
    ``MOCK_PROVIDER_MODE`` with ``monkeypatch.setenv``.
    """
    raw = os.getenv("MOCK_PROVIDER_MODE", DEFAULT_MODE).strip().lower()
    return raw if raw in MODES else DEFAULT_MODE  # type: ignore[return-value]


def _seed(*parts: str) -> int:
    """A stable integer derived from the inputs.

    Deterministic on purpose: the same conversation must always produce the same
    reply, otherwise end-to-end assertions drift between runs.
    """
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


class MockProvider(AIClient):
    """Offline stand-in for :class:`~app.providers.sber.SberProvider`.

    Selected automatically when ``SBER_API_KEY`` is the literal string ``"mock"``
    (see ``app.providers.containers``). It performs no network I/O at all, which
    is what makes the worker, route and end-to-end suites possible without
    credentials and without incurring GigaChat cost.
    """

    def __init__(self, api_key: str, scope: str, tools: list[Tool] | None = None):
        if scope not in MOCK_SCOPES:
            raise ValueError(f"Scope must be one of {', '.join(MOCK_SCOPES)}")
        super().__init__(api_key, scope, tools)

    async def auth(self):
        self._log.info("mock_auth", scope=self.scope)
        self.token = f"mock-token-{_seed(self.api_key, self.scope):08x}"
        # Far enough out that ensure_fresh_token() never re-authenticates mid-test.
        self.token_expires_at = datetime.now(UTC) + timedelta(days=1)

    async def upload(
        self,
        filename: str,
        data: bytes,
        x_client_id: uuid.UUID,
        x_session_id: uuid.UUID,
        content_type: str,
    ) -> str:
        self._log.debug(
            "mock_upload",
            filename=filename,
            size=len(data),
            content_type=content_type,
            x_client_id=str(x_client_id),
            x_session_id=str(x_session_id),
        )
        return f"mock-file-{_seed(str(x_session_id), filename):08x}"

    def _payload(self, fingerprint: str) -> dict:
        mode = get_mock_mode()
        if mode == "empty":
            return {"chat_name": MOCK_CHAT_NAME, "message": ""}
        return {
            "chat_name": MOCK_CHAT_NAME,
            "message": f"Это ответ офлайн-мока на сообщение №{_seed(fingerprint) % 1000}.",
        }

    async def generate(
        self,
        message_history: list[Message],
        response_format: ResponseFormat,
        x_client_id: uuid.UUID,
        x_session_id: uuid.UUID,
    ) -> GenerationResponse | None:
        fingerprint = "|".join(m.content[0].text for m in message_history if m.content)
        mode = get_mock_mode()
        self._log.debug(
            "mock_generate",
            mode=mode,
            messages=len(message_history),
            format=response_format.type,
            x_client_id=str(x_client_id),
            x_session_id=str(x_session_id),
        )

        if mode == "error":
            return None

        if mode == "empty":
            # A well-formed response carrying no content, which the backend must
            # treat as "Provider did not respond properly".
            return self._wrap([Message(role="assistant", content=[])], fingerprint)

        payload = json.dumps(self._payload(fingerprint), ensure_ascii=False)
        return self._wrap([Message(role="assistant", content=[Content(text=payload)])], fingerprint)

    def _wrap(self, messages: list[Message], fingerprint: str) -> GenerationResponse:
        seed = _seed(fingerprint, "usage")
        return GenerationResponse(
            messages=messages,
            model="mock-model",
            thread_id=f"mock-thread-{seed:08x}",
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            finish_reason="stop",
            usage=Usage(
                input_tokens=100 + seed % 400,
                input_tokens_details=InputTokenDetails(prompt_tokens=100 + seed % 400, cached_tokens=0),
                output_tokens=50 + seed % 200,
                total_tokens=150 + seed % 600,
            ),
        )
