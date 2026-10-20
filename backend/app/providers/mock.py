import hashlib
import json
import math
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
#: drives the programme pipeline: the mock starts a run at the intake, then saves
#: one valid block per stage, which is what lets the whole cycle run end to end
#: with no provider and no cost.
MODES: tuple[Mode, ...] = ("ok", "empty", "error", "stage")

DEFAULT_MODE: Mode = "ok"

MOCK_CHAT_NAME = "Чат (mock)"

#: Tools of the programme pipeline the ``stage`` mode reacts to.
BEGIN_PROGRAM = "begin_program"
SAVE_PROGRAM_DRAFT = "save_program_draft"

#: Volume the fabricated programme takes when the methodist named no hours.
DEFAULT_PROGRAMME_HOURS = 72.0

#: Daily load of the fabricated schedule, used to size it against the volume.
DAILY_HOURS = 4


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


def _last_text(message_history: list[Message]) -> str:
    """Everything the conversation says, which is where the volume is named."""
    return " ".join(part.text for message in message_history for part in message.content if part.text)


def _requested_hours(text: str) -> float | None:
    """The volume in the text, or ``None`` when nobody named one.

    Two kinds of text reach this: the brief of a stage, which states the volume in
    one fixed phrase, and the methodist's own message at the intake, which names it
    however they like. Parsing both keeps the fabricated programme the size the
    methodist asked for.
    """
    match = re.search(r"Заявленный объём программы:\s*(\d+(?:[.,]\d+)?)", text)
    if match is None:
        match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:академических\s+)?час", text)
    return float(match.group(1).replace(",", ".")) if match is not None else None


def _curriculum(total: float) -> dict:
    """A curriculum whose hours add up to ``total`` and fit its schedule."""
    days = max(1, math.ceil(total / DAILY_HOURS))
    return {
        "modules": [
            {
                "id": "m-01",
                "title": "Содержание программы",
                "topics": [
                    {
                        "id": "t-01.1",
                        "title": "Основное содержание программы",
                        "hours": {"lecture": total / 2, "practice": total / 4, "self_study": total / 4},
                        "outcome_ids": ["ro-k-01", "ro-s-01", "ro-e-01"],
                    }
                ],
            }
        ],
        "schedule": {"days": days, "hours_per_day": float(DAILY_HOURS)},
    }


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
        *,
        tools: list[Tool] | None = None,
    ) -> GenerationResponse | None:
        fingerprint = "|".join(m.content[0].text for m in message_history if m.content)
        mode = get_mock_mode()
        offered = self.tools if tools is None else tools
        self._log.debug(
            "mock_generate",
            mode=mode,
            messages=len(message_history),
            format=response_format.type,
            # The mock answers without the model, so the offered set is only
            # logged; ``stage`` mode uses it to pick the call to script.
            tools=len(offered),
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
            return self._stage(message_history, response_format, offered, fingerprint)

        payload = json.dumps(self._payload(fingerprint), ensure_ascii=False)
        return self._wrap([Message(role="assistant", content=[Content(text=payload)])], fingerprint)

    def _stage(
        self,
        message_history: list[Message],
        response_format: ResponseFormat,
        offered: list[Tool],
        fingerprint: str,
    ) -> GenerationResponse:
        """Answer one turn of the programme pipeline with a usable call.

        The model is replaced by a fixed script: at the intake it starts a run, at
        each stage it saves the block that stage offered, and once a call has come
        back it answers. The block is picked from the offered tool — every stage
        offers ``save_program_draft`` with a different block — and the fabricated
        document is one fixed programme, because the pipeline validates the blocks
        against each other and the mock has to pass those checks.
        """
        if message_history and message_history[-1].role == "tool":
            return self._wrap(
                [Message(role="assistant", content=[Content(text=self._stage_answer(response_format))])],
                fingerprint,
            )
        names = {tool.function.name for tool in offered}
        if SAVE_PROGRAM_DRAFT in names:
            spec = next(tool.function for tool in offered if tool.function.name == SAVE_PROGRAM_DRAFT)
            return self._call(SAVE_PROGRAM_DRAFT, self._block(spec.parameters, message_history), fingerprint)
        if BEGIN_PROGRAM in names:
            text = _last_text(message_history).strip() or "Программа (mock)"
            arguments: dict = {"requirements": text[:4000]}
            hours = _requested_hours(text)
            if hours is not None:
                arguments["requested_hours"] = hours
            return self._call(BEGIN_PROGRAM, arguments, fingerprint)
        payload = json.dumps(self._payload(fingerprint), ensure_ascii=False)
        return self._wrap([Message(role="assistant", content=[Content(text=payload)])], fingerprint)

    def _block(self, parameters: dict | None, message_history: list[Message]) -> dict:
        """The block of the stage, told apart by the fields its spec requires."""
        properties = (parameters or {}).get("properties", {})
        if "competencies" in properties:
            return {
                "kind": "professional_development",
                "title": "Программа (mock)",
                "competencies": [
                    {
                        "id": "comp-01",
                        "statement": "Готов применять полученные знания и умения в профессиональной деятельности",
                        "knowledge": [{"id": "ro-k-01", "statement": "Знает основные понятия программы"}],
                        "skills": [{"id": "ro-s-01", "statement": "Умеет применять их в типовых задачах"}],
                        "experience": [{"id": "ro-e-01", "statement": "Владеет приёмами решения практических задач"}],
                    }
                ],
            }
        if "curriculum" in properties:
            total = _requested_hours(_last_text(message_history)) or DEFAULT_PROGRAMME_HOURS
            return {"curriculum": _curriculum(total)}
        if "sources" in properties:
            # The catalog is empty until the RAG index (#11), and a citation that
            # search never returned is refused, so an empty list is the only block
            # the pipeline accepts today.
            return {"sources": []}
        return {
            "summary": {
                "goal": "Сформировать у слушателей практические знания и умения по теме программы.",
                "tasks": ["Изучить основные понятия", "Отработать приёмы на практике"],
                "target_audience": "Специалисты, которым нужны знания по теме программы",
                "admission_requirements": "Высшее или среднее профессиональное образование",
                "form": "Очно-заочная, с применением дистанционных технологий",
                "issued_document": "Удостоверение о повышении квалификации",
            }
        }

    def _stage_answer(self, response_format: ResponseFormat) -> str:
        """What the mock says once the pipeline has run its call."""
        if response_format.type == ResponseFormat.TYPE_TEXT:
            return "Блок сохранён."
        return json.dumps(
            {"chat_name": None, "message": "Программа запущена. Черновик придёт в чат, когда все блоки будут готовы."},
            ensure_ascii=False,
        )

    def _call(self, name: str, arguments: dict, fingerprint: str) -> GenerationResponse:
        call = ToolCall(function=FunctionCall(name=name, arguments=json.dumps(arguments, ensure_ascii=False)))
        return self._wrap([Message(role="assistant", content=[])], fingerprint, calls=[call])

    def _wrap(
        self, messages: list[Message], fingerprint: str, *, calls: list[ToolCall] | None = None
    ) -> GenerationResponse:
        seed = _seed(fingerprint, "usage")
        return GenerationResponse(
            messages=messages,
            model="mock-model",
            thread_id=f"mock-thread-{seed:08x}",
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            finish_reason="function_call" if calls else "stop",
            usage=Usage(
                input_tokens=100 + seed % 400,
                input_tokens_details=InputTokenDetails(prompt_tokens=100 + seed % 400, cached_tokens=0),
                output_tokens=50 + seed % 200,
                total_tokens=150 + seed % 600,
            ),
            tool_calls=calls or [],
        )
