import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from app.providers.client import AIClient
from app.providers.models import (
    Content,
    FunctionCall,
    GenerationResponse,
    InputTokenDetails,
    Message,
    ResponseFormat,
    Tool,
    ToolConfig,
    Usage,
)

MOCK_API_KEY = "mock"

Mode = Literal["ok", "empty", "error", "draft"]

#: Modes the mock understands. ``ok`` is the happy path: a well-formed plain-text
#: reply. ``empty`` returns a response with no content at all, and ``error``
#: returns ``None``, both of which drive the backend's error paths. ``draft``
#: emulates the methodist scenario end to end: the first round asks to save the
#: programme draft, and the round after the function result answers with text.
MODES: tuple[Mode, ...] = ("ok", "empty", "error", "draft")

DEFAULT_MODE: Mode = "ok"

#: The function ``draft`` mode calls, and the canned arguments it passes. The
#: arguments are a complete, schema-valid draft, so the offline stack exercises
#: the real ``save_program_draft`` handler rather than a shortcut around it.
MOCK_SAVE_FUNCTION = "save_program_draft"

MOCK_DRAFT_ARGUMENTS: dict = {
    "kind": "general_development",
    "title": "Интенсив по искусственному интеллекту (mock)",
    "summary": {
        "goal": "Научить собирать работающий прототип на основе готовых ИИ-моделей.",
        "target_audience": "Взрослые слушатели без опыта в машинном обучении.",
        "form": "очно-заочная с применением ЭО и ДОТ",
        "issued_document": "сертификат об освоении программы",
    },
    "competencies": [
        {
            "id": "comp-01",
            "statement": "Способен собрать работающий прототип на основе готовых ИИ-моделей",
            "knowledge": [{"id": "ro-k-01", "statement": "Знает принципы работы больших языковых моделей"}],
            "skills": [{"id": "ro-s-01", "statement": "Умеет вызывать готовые модели через API"}],
        }
    ],
    "curriculum": {
        "modules": [
            {
                "id": "m-01",
                "title": "Погружение в кейс",
                "topics": [
                    {
                        "id": "t-01.1",
                        "title": "Обзор готовых ИИ-моделей и их API",
                        "hours": {"lecture": 2, "practice": 2, "self_study": 2},
                    }
                ],
            }
        ],
        "schedule": {"days": 15, "hours_per_day": 1.6},
    },
    "sources": [],
}


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

    def __init__(self, api_key: str, scope: str):
        if scope not in ("PERS", "B2B", "CORP"):
            raise ValueError("Scope must be PERS, B2B or CORP")
        super().__init__(api_key, scope)

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

    @staticmethod
    def _has_draft_result(message_history: list[Message]) -> bool:
        """Whether a draft was already saved within this run."""
        return any(
            part.function_result is not None and part.function_result.name == MOCK_SAVE_FUNCTION
            for message in message_history
            for part in message.content
        )

    def _reply(self, fingerprint: str, mode: Mode) -> str:
        if mode == "draft":
            return "Черновик программы подготовлен (mock). Проверьте его и подтвердите."
        return f"Это ответ офлайн-мока на сообщение №{_seed(fingerprint) % 1000}."

    async def generate(
        self,
        message_history: list[Message],
        x_client_id: uuid.UUID,
        x_session_id: uuid.UUID,
        *,
        response_format: ResponseFormat | None = None,
        tools: list[Tool] | None = None,
        tool_config: ToolConfig | None = None,
    ) -> GenerationResponse | None:
        fingerprint = "|".join(
            part.text or "" for message in message_history if message.content for part in message.content
        )
        mode = get_mock_mode()
        self._log.debug(
            "mock_generate",
            mode=mode,
            messages=len(message_history),
            format=response_format.type if response_format is not None else None,
            tools=bool(tools),
            tool_mode=tool_config.mode if tool_config is not None else None,
            x_client_id=str(x_client_id),
            x_session_id=str(x_session_id),
        )

        if mode == "error":
            return None

        if mode == "empty":
            # A well-formed response carrying no content, which the backend must
            # treat as "Provider did not respond properly".
            return self._wrap([Message(role="assistant", content=[])], fingerprint)

        if mode == "draft" and tools and not self._has_draft_result(message_history):
            return self._wrap(
                [
                    Message(
                        role="assistant",
                        content=[
                            Content(
                                function_call=FunctionCall(
                                    id="call-mock-1",
                                    name=MOCK_SAVE_FUNCTION,
                                    arguments=MOCK_DRAFT_ARGUMENTS,
                                )
                            )
                        ],
                        tools_state_id="mock-tools-state",
                    )
                ],
                fingerprint,
                finish_reason="function_call",
            )

        return self._wrap(
            [Message(role="assistant", content=[Content(text=self._reply(fingerprint, mode))])],
            fingerprint,
        )

    def _wrap(
        self, messages: list[Message], fingerprint: str, finish_reason: Literal["stop", "function_call"] = "stop"
    ) -> GenerationResponse:
        seed = _seed(fingerprint, "usage")
        return GenerationResponse(
            messages=messages,
            model="mock-model",
            thread_id=f"mock-thread-{seed:08x}",
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            finish_reason=finish_reason,
            usage=Usage(
                input_tokens=100 + seed % 400,
                input_tokens_details=InputTokenDetails(prompt_tokens=100 + seed % 400, cached_tokens=0),
                output_tokens=50 + seed % 200,
                total_tokens=150 + seed % 600,
            ),
        )
