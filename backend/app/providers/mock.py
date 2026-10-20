import hashlib
import json
import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal, get_args

from app.providers.client import AIClient, Scope
from app.providers.models import (
    Content,
    FunctionCall,
    GenerationResponse,
    InputTokenDetails,
    Message,
    ResponseFormat,
    Tool,
    ToolCall,
    Usage,
)

MOCK_API_KEY = "mock"

#: Scopes the offline provider accepts: every provider the container can select,
#: because the mock stands in for whichever one ``SBER_API_SCOPE`` names. Derived
#: from ``Scope`` so a new scope cannot be added without the mock accepting it —
#: ``SBER_API_KEY=mock`` picks the mock regardless of scope, and a scope it
#: rejected would fail at import instead.
MOCK_SCOPES = get_args(Scope)

Mode = Literal["ok", "empty", "error", "stage"]

#: Modes the mock understands. ``ok`` is the happy path: a well-formed reply and a
#: chat name. ``empty`` returns a response with no content at all, and ``error``
#: returns ``None``, both of which drive the backend's error paths. ``stage``
#: emulates the programme pipeline: the intake calls ``begin_program`` and every
#: stage answers with a schema-valid ``save_program_draft`` call.
MODES: tuple[Mode, ...] = ("ok", "empty", "error", "stage")

DEFAULT_MODE: Mode = "ok"

MOCK_CHAT_NAME = "Чат (mock)"

#: Marker the stage prompt writes into its system message. The mock reads it to
#: answer with the block that stage expects.
_STAGE_MARKER = re.compile(r"STAGE:\s*([a-z_]+)")
_HOURS_MARKER = re.compile(r'"requested_hours":\s*([0-9]+(?:\.[0-9]+)?)')

_STAGE_PAYLOADS: dict[str, dict] = {
    "competences": {
        "stage": "competences",
        "payload": {
            "competencies": [
                {
                    "id": "comp-01",
                    "statement": "Способен применять базовые методы анализа данных",
                    "knowledge": [
                        {"id": "ro-k-01", "statement": "Знает основные понятия и методы анализа данных"},
                    ],
                    "skills": [
                        {"id": "ro-s-01", "statement": "Умеет выбирать метод анализа под задачу"},
                    ],
                }
            ]
        },
    },
    "sources": {"stage": "sources", "payload": {"sources": []}},
    "summary": {
        "stage": "summary",
        "payload": {
            "kind": "general_development",
            "title": "Офлайн-программа (mock)",
            "summary": {
                "goal": "Сформировать базовые навыки анализа данных",
                "target_audience": "Взрослые слушатели без специальной подготовки",
                "form": "очно-заочная",
                "issued_document": "сертификат",
            },
        },
    },
}


def _hours_payload(hours: float) -> dict:
    """A curriculum whose topics and schedule add up to the state's volume."""
    lecture = hours / 2
    practice = hours / 2
    return {
        "stage": "hours",
        "payload": {
            "curriculum": {
                "modules": [
                    {
                        "id": "m-01",
                        "title": "Модуль 1. Основы",
                        "topics": [
                            {
                                "id": "t-01.1",
                                "title": "Тема 1. Понятия",
                                "hours": {"lecture": lecture},
                            },
                            {
                                "id": "t-01.2",
                                "title": "Тема 2. Практика",
                                "hours": {"practice": practice},
                            },
                        ],
                    }
                ],
                "schedule": {"days": 1, "hours_per_day": hours},
            }
        },
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

    @staticmethod
    def _system_text(messages: list[Message]) -> str:
        return "\n".join(
            part.text or "" for message in messages if message.role == "system" for part in message.content
        )

    @staticmethod
    def _last_user_text(messages: list[Message]) -> str:
        for message in reversed(messages):
            if message.role == "user":
                return "\n".join(part.text or "" for part in message.content)
        return ""

    @staticmethod
    def _has_tool_result(messages: list[Message]) -> bool:
        return any(message.role == "tool" for message in messages)

    def _requested_hours(self, messages: list[Message]) -> float:
        match = _HOURS_MARKER.search(self._system_text(messages) + "\n" + self._last_user_text(messages))
        if match is None:
            return 6.0
        try:
            value = float(match.group(1))
        except ValueError:
            return 6.0
        return value if value > 0 else 6.0

    def _stage_tool_call(self, messages: list[Message], fingerprint: str) -> GenerationResponse:
        """The scripted function call of ``stage`` mode, one per stage."""
        stage = (_STAGE_MARKER.search(self._system_text(messages)) or [None, None])[1]
        if self._has_tool_result(messages):
            if stage is None:
                # The intake loop sends the tool result back and expects the final
                # structured answer, not a stage's confirmation line.
                payload = json.dumps(self._payload(fingerprint), ensure_ascii=False)
                return self._wrap([Message(role="assistant", content=[Content(text=payload)])], fingerprint)
            return self._wrap(
                [Message(role="assistant", content=[Content(text="Блок сохранён. Следующий этап запустит backend.")])],
                fingerprint,
            )
        if stage == "hours":
            arguments = _hours_payload(self._requested_hours(messages))
        elif stage in _STAGE_PAYLOADS:
            arguments = _STAGE_PAYLOADS[stage]
        else:
            arguments = {
                "requested_hours": self._requested_hours(messages),
                "requirements": self._last_user_text(messages) or "Разработать образовательную программу",
                "title": "Офлайн-программа (mock)",
            }
            call = ToolCall(
                id=f"mock-call-{_seed(fingerprint, 'begin'):08x}",
                function=FunctionCall(name="begin_program", arguments=json.dumps(arguments, ensure_ascii=False)),
            )
            message = Message(
                role="assistant", content=[Content(text="Начинаю разработку программы.")], tool_calls=[call]
            )
            return self._wrap([message], fingerprint, tool_calls=[call], finish_reason="tool_calls")
        call = ToolCall(
            id=f"mock-call-{_seed(fingerprint, stage):08x}",
            function=FunctionCall(name="save_program_draft", arguments=json.dumps(arguments, ensure_ascii=False)),
        )
        message = Message(role="assistant", content=[Content(text="Сохраняю блок.")], tool_calls=[call])
        return self._wrap([message], fingerprint, tool_calls=[call], finish_reason="tool_calls")

    async def generate(
        self,
        message_history: list[Message],
        response_format: ResponseFormat,
        x_client_id: uuid.UUID,
        x_session_id: uuid.UUID,
        *,
        tools: list[Tool] | None = None,
    ) -> GenerationResponse | None:
        fingerprint = "|".join(message.content[0].text for message in message_history if message.content)
        mode = get_mock_mode()
        self._log.debug(
            "mock_generate",
            mode=mode,
            messages=len(message_history),
            format=response_format.type,
            tools=len(tools or self.tools),
            x_client_id=str(x_client_id),
            x_session_id=str(x_session_id),
        )

        if mode == "error":
            return None

        if mode == "empty":
            # A well-formed response carrying no content, which the backend must
            # treat as "Provider did not respond properly".
            return self._wrap([Message(role="assistant", content=[])], fingerprint)

        if mode == "stage":
            return self._stage_tool_call(message_history, fingerprint)

        payload = json.dumps(self._payload(fingerprint), ensure_ascii=False)
        return self._wrap([Message(role="assistant", content=[Content(text=payload)])], fingerprint)

    def _wrap(
        self,
        messages: list[Message],
        fingerprint: str,
        *,
        tool_calls: list[ToolCall] | None = None,
        finish_reason: str = "stop",
    ) -> GenerationResponse:
        seed = _seed(fingerprint, "usage")
        return GenerationResponse(
            messages=messages,
            model="mock-model",
            thread_id=f"mock-thread-{seed:08x}",
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            finish_reason=finish_reason,  # type: ignore[arg-type]
            usage=Usage(
                input_tokens=100 + seed % 400,
                input_tokens_details=InputTokenDetails(prompt_tokens=100 + seed % 400, cached_tokens=0),
                output_tokens=50 + seed % 200,
                total_tokens=150 + seed % 600,
            ),
            tool_calls=tool_calls or [],
        )
