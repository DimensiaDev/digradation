import hashlib
import json
import math
import os
import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import get_args

from app.agentic.intake import requested_hours as _requested_hours
from app.providers.client import AIClient, Scope
from app.providers.models import (
    Content,
    FinishReason,
    FunctionCall,
    GenerationResponse,
    InputTokenDetails,
    Message,
    ResponseFormat,
    Role,
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


class Mode(StrEnum):
    """What the mock answers: ``empty`` and ``error`` drive the error paths,
    ``stage`` scripts one valid call per programme stage, ``revise`` asks for
    one part of the programme to be redone."""

    OK = "ok"
    EMPTY = "empty"
    ERROR = "error"
    STAGE = "stage"
    REVISE = "revise"
    APPROVE = "approve"


MODES: tuple[Mode, ...] = tuple(Mode)
_RAW_MODES = frozenset(mode.value for mode in Mode)

DEFAULT_MODE = Mode.OK

#: Tools of the programme pipeline the ``stage`` mode reacts to.
BEGIN_PROGRAM = "begin_program"
SAVE_PROGRAM_DRAFT = "save_program_draft"
REVISE_PROGRAM = "revise_program"
APPROVE_PROGRAM = "approve_program_draft"

#: The question the mock asks when the methodist named no volume.
MOCK_QUESTION = "На сколько академических часов рассчитана программа?"

#: Volume the fabricated programme takes when the methodist named no hours.
DEFAULT_PROGRAMME_HOURS = 72.0

#: Daily load of the fabricated schedule, used to size it against the volume.
DAILY_HOURS = 4


def get_mock_mode() -> Mode:
    """Read the mode from the environment, falling back to the happy path."""
    raw = os.getenv("MOCK_PROVIDER_MODE", DEFAULT_MODE).strip().lower()
    return Mode(raw) if raw in _RAW_MODES else DEFAULT_MODE


def _seed(*parts: str) -> int:
    """A stable integer derived from the inputs.

    Deterministic on purpose: the same conversation must always produce the same
    reply, otherwise end-to-end assertions drift between runs.
    """
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def _last_text(message_history: list[Message]) -> str:
    """The last user turn: the methodist's task or a stage's brief."""
    return next(
        (
            " ".join(part.text for part in message.content if part.text)
            for message in reversed(message_history)
            if message.role == Role.USER
        ),
        "",
    )


def _curriculum(total: float) -> dict:
    """A curriculum whose hours add up to ``total`` and fit its schedule."""
    days = max(1, math.ceil(total / DAILY_HOURS))
    return {
        "modules": [
            {
                "id": "m-01",
                "title": "Содержание программы",
                "hours": total,
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
        "attestation_hours": 0.0,
    }


def _catalog_source() -> dict:
    """A document of the corpus, the way the agent would cite it."""
    return {
        "id": "src-04",
        "kind": "professional_standard",
        "title": "Профессиональный стандарт «Программист»",
        "identifier": "Приказ Минтруда России от 20.07.2022 № 424н",
    }


def _assessment() -> dict:
    """Assessment means that check every outcome of the mock programme twice."""
    outcomes = ["ro-k-01", "ro-s-01", "ro-e-01"]
    return {
        "tools": [
            {
                "id": "at-01",
                "kind": "test",
                "title": "Тест по итогам модуля",
                "topic_ids": ["t-01.1"],
                "outcome_ids": outcomes,
                "criteria": ["Верно отвечает не менее чем на 70% вопросов"],
                "materials": ["Банк вопросов с ключами"],
            }
        ],
        "final": {
            "form": "защита проекта",
            "hours": 0,
            "outcome_ids": outcomes,
            "criteria": ["Проект решает поставленную задачу и защищён перед комиссией"],
            "materials": ["Требования к проекту"],
        },
        "questions": [
            {
                "id": "q-01-01",
                "tool_id": "at-01",
                "topic_ids": ["t-01.1"],
                "outcome_ids": ["ro-k-01"],
                "text": "Что такое основное понятие программы?",
                "options": ["Определение", "Пример", "Процедура"],
                "answers": ["Определение"],
            }
        ],
        "seminar_tasks": [
            {
                "id": "st-01",
                "tool_id": "at-01",
                "topic_id": "t-01.1",
                "outcome_ids": ["ro-s-01", "ro-e-01"],
                "title": "Разбор типовой задачи",
                "statement": "Решите задачу по образцу и объясните ход решения.",
                "criteria": ["Задача решена верно, ход решения объяснён"],
            }
        ],
    }


def _materials() -> dict:
    """A lecture plan and a slide deck for the only topic of the mock programme."""
    return {
        "lecture_plans": [
            {
                "id": "lp-01",
                "topic_id": "t-01.1",
                "title": "Основное содержание программы",
                "plan": ["Понятие и область применения", "Типовые задачи", "Разбор примера"],
                "summary": "Лекция о том, что составляет основу программы.",
            }
        ],
        "slide_decks": [
            {
                "id": "sd-01",
                "topic_id": "t-01.1",
                "title": "Основное содержание программы",
                "slides": [
                    {"title": "О чём лекция", "bullets": ["Область применения", "Ключевые понятия"]},
                    {"title": "Пример", "bullets": ["Разбор типовой задачи"]},
                ],
            }
        ],
        # The stage refuses a block without delivery conditions and staff, so
        # the offline provider has to supply them or it cannot carry a run
        # through the materials stage.
        "conditions": {
            "equipment": ["Ноутбук", "Проектор"],
            "teaching_materials": ["Раздаточные материалы"],
            "staffing_requirements": ["Высшее образование", "Опыт преподавания"],
        },
        "staffing": [
            {
                "id": "ts-01",
                "position": "Преподаватель",
                "module_id": "m-01",
            }
        ],
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

    def _reply(self, fingerprint: str) -> str:
        """The plain answer the mock gives when it is not running a programme."""
        return f"Это ответ офлайн-мока на сообщение №{_seed(fingerprint) % 1000}."

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
            tools=len(offered),
            x_client_id=str(x_client_id),
            x_session_id=str(x_session_id),
        )

        if mode is Mode.ERROR:
            return None

        if mode is Mode.EMPTY:
            # A well-formed response carrying no content, which the backend must
            # treat as "Provider did not respond properly".
            return self._wrap([Message(role=Role.ASSISTANT, content=[])], fingerprint)

        if mode is Mode.REVISE:
            return self._revise(message_history, offered, fingerprint)

        if mode is Mode.APPROVE:
            return self._approve(message_history, offered, fingerprint)

        if mode is Mode.STAGE:
            return self._stage(message_history, offered, fingerprint)

        return self._wrap([Message(role=Role.ASSISTANT, content=[Content(text=self._reply(fingerprint))])], fingerprint)

    def _stage(
        self,
        message_history: list[Message],
        offered: list[Tool],
        fingerprint: str,
    ) -> GenerationResponse:
        """Script one turn: start a run, save the offered block, then answer."""
        if message_history and message_history[-1].role == Role.TOOL:
            return self._wrap(
                [Message(role=Role.ASSISTANT, content=[Content(text=self._stage_answer())])],
                fingerprint,
            )
        names = {tool.function.name for tool in offered}
        if SAVE_PROGRAM_DRAFT in names:
            spec = next(tool.function for tool in offered if tool.function.name == SAVE_PROGRAM_DRAFT)
            return self._call(SAVE_PROGRAM_DRAFT, self._block(spec.parameters, message_history), fingerprint)
        if BEGIN_PROGRAM in names:
            text = _last_text(message_history).strip() or "Программа (mock)"
            arguments: dict = {
                "requirements": text[:4000],
                "kind": "professional_development",
                "format": "course",
            }
            hours = _requested_hours(text)
            if hours is not None:
                arguments["requested_hours"] = hours
            else:
                arguments["questions"] = [MOCK_QUESTION]
            return self._call(BEGIN_PROGRAM, arguments, fingerprint)
        return self._wrap([Message(role=Role.ASSISTANT, content=[Content(text=self._reply(fingerprint))])], fingerprint)

    def _approve(
        self,
        message_history: list[Message],
        offered: list[Tool],
        fingerprint: str,
    ) -> GenerationResponse:
        """Script one turn: confirm the drafted programme."""
        if message_history and message_history[-1].role == Role.TOOL:
            return self._wrap(
                [Message(role=Role.ASSISTANT, content=[Content(text="Черновик утверждён.")])],
                fingerprint,
            )
        names = {tool.function.name for tool in offered}
        if APPROVE_PROGRAM in names:
            return self._call(APPROVE_PROGRAM, {}, fingerprint)
        return self._wrap([Message(role=Role.ASSISTANT, content=[Content(text=self._reply(fingerprint))])], fingerprint)

    def _revise(
        self,
        message_history: list[Message],
        offered: list[Tool],
        fingerprint: str,
    ) -> GenerationResponse:
        """Script one turn: ask for one part of the programme to be redone."""
        if message_history and message_history[-1].role == Role.TOOL:
            return self._wrap(
                [Message(role=Role.ASSISTANT, content=[Content(text="Правка принята.")])],
                fingerprint,
            )
        names = {tool.function.name for tool in offered}
        if REVISE_PROGRAM in names:
            return self._call(
                REVISE_PROGRAM,
                {"stage": "hours", "notes": "замени тему 2.3"},
                fingerprint,
            )
        return self._wrap([Message(role=Role.ASSISTANT, content=[Content(text=self._reply(fingerprint))])], fingerprint)

    def _block(self, parameters: dict | None, message_history: list[Message]) -> dict:
        """The block of the stage, told apart by the fields its spec requires."""
        properties = (parameters or {}).get("properties", {})
        if "competencies" in properties:
            return {
                "kind": "professional_development",
                "format": "course",
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
            # The catalogue holds the documents of the corpus, and the mock
            # cites one of them the way the agent would.
            return {"sources": [_catalog_source()]}
        if "assessment" in properties:
            return {"assessment": _assessment()}
        if "materials" in properties:
            return {"materials": _materials()}
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

    def _stage_answer(self) -> str:
        """What the mock says once the pipeline has run its call."""
        return "Блок сохранён."

    def _call(self, name: str, arguments: dict, fingerprint: str) -> GenerationResponse:
        call = ToolCall(function=FunctionCall(name=name, arguments=json.dumps(arguments, ensure_ascii=False)))
        return self._wrap([Message(role=Role.ASSISTANT, content=[])], fingerprint, calls=[call])

    def _wrap(
        self, messages: list[Message], fingerprint: str, *, calls: list[ToolCall] | None = None
    ) -> GenerationResponse:
        seed = _seed(fingerprint, "usage")
        return GenerationResponse(
            messages=messages,
            model="mock-model",
            thread_id=f"mock-thread-{seed:08x}",
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            finish_reason=FinishReason.FUNCTION_CALL if calls else FinishReason.STOP,
            usage=Usage(
                input_tokens=100 + seed % 400,
                input_tokens_details=InputTokenDetails(prompt_tokens=100 + seed % 400, cached_tokens=0),
                output_tokens=50 + seed % 200,
                total_tokens=150 + seed % 600,
            ),
            tool_calls=calls or [],
        )
