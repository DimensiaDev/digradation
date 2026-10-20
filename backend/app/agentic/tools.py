"""The user functions the model may call, and their implementations.

This module is one half of the agent contract. The specifications below are sent
to the provider with a generation request, and the pydantic schemas of
``app.agentic.models`` and ``app.agentic.state`` are the other half: the model
fills the arguments from the specification, the arguments are validated at the
call boundary, and the handlers work with the validated models rather than with
dictionaries.

The handlers keep no storage of their own. They mutate :class:`ToolContext`,
which carries the programme state of the run, and the caller checkpoints that
state after every call. That is what makes a worker death survivable: the block
saved before the death is already committed, and the stage that follows it
reads the state back instead of starting over.

:func:`execute_tool` is the boundary with the provider: it turns a
:class:`FunctionCall` — a name and the JSON string of arguments — into one handler
call, and every failure, arguments that do not validate included, comes back as a
result with ``ok: false`` instead of a raise, so the model can correct itself
inside the same reply.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog.stdlib
from pydantic import BaseModel, Field, ValidationError

from app.agentic.models import ProgramDraft, ProgramStatus, Source, SourceKind, Stage, StageStatus
from app.agentic.sources import SourceFragment, get_source, search
from app.agentic.state import (
    STAGE_BLOCKS,
    ProgramState,
    apply_block,
    approve_state,
    new_state,
    set_known_sources,
)
from app.providers.models import FunctionCall, FunctionDefinition, Tool

log = structlog.stdlib.get_logger(__name__)


@dataclass
class ToolContext:
    """What a handler needs beyond the arguments of the call.

    ``state`` is the programme envelope the intake created and the stages fill;
    it is ``None`` until :func:`begin_program` runs. ``stage`` is the stage the
    current worker is executing, which is how :func:`save_program_draft` knows
    which block the model is allowed to write. ``dirty`` is set by every handler
    that changed the state, and the loop's checkpoint acts on it.
    """

    session_id: uuid.UUID
    state: ProgramState | None = None
    stage: Stage | None = None
    dirty: bool = False
    started: bool = False
    approved: bool = False
    known_source_ids: set[str] = field(default_factory=set)


class ToolResult(BaseModel):
    """What a call answers.

    A refusal shares the shape of a success, so the model can always tell what the
    call did and, when it did nothing, what to change before calling again.
    """

    ok: bool = True
    error: str | None = None
    details: list[str] | None = None
    note: str | None = None


class SearchSourcesResult(ToolResult):
    """Fragments the catalog returned for the query."""

    query: str
    fragments: list[SourceFragment] = Field(default_factory=list)


class BeginProgramResult(ToolResult):
    """State of the process after the intake started it."""

    status: ProgramStatus | None = None
    current_stage: Stage | None = None


class SaveProgramDraftResult(ToolResult):
    """State of the process after one stage's block was accepted."""

    status: ProgramStatus | None = None
    stage: Stage | None = None
    current_stage: Stage | None = None


class ApproveProgramDraftResult(ToolResult):
    """State of the process after the methodist confirmed the draft."""

    status: ProgramStatus | None = None


class GetProgramDraftResult(ToolResult):
    """The programme state this run has, and the draft if it is complete."""

    status: ProgramStatus
    current_stage: Stage | None = None
    state: ProgramState | None = None
    draft: ProgramDraft | None = None


class SearchSourcesArguments(BaseModel):
    """Arguments of :func:`search_sources`."""

    query: str = Field(
        min_length=1,
        max_length=500,
        description="Поисковый запрос: тема, вид деятельности или компетенция",
    )
    kinds: list[SourceKind] = Field(
        default_factory=list,
        description="Ограничить типы источников (необязательно)",
    )
    limit: int = Field(default=5, ge=1, le=10, description="Сколько фрагментов вернуть")


class GetProgramDraftArguments(BaseModel):
    """Arguments of :func:`get_program_draft`: the tool takes none."""


class BeginProgramArguments(BaseModel):
    """Arguments of :func:`begin_program`."""

    requested_hours: float = Field(
        gt=0,
        le=2000,
        description="Заявленный объём программы в академических часах",
    )
    requirements: str = Field(
        min_length=1,
        max_length=8000,
        description="Тема, аудитория, цель и дополнительные требования одним текстом",
    )
    title: str | None = Field(default=None, max_length=500, description="Рабочее название, если оно уже известно")


class ApproveProgramDraftArguments(BaseModel):
    """Arguments of :func:`approve_program_draft`: the tool takes none."""


class SaveProgramDraftArguments(BaseModel):
    """Arguments of :func:`save_program_draft`.

    The payload is validated against the block model of ``stage`` inside the
    handler. Keeping it a dictionary here lets one tool specification serve all
    stages: the stage prompt carries the exact block schema, and the handler
    refuses a payload that does not match it.
    """

    stage: Stage = Field(description="Этап, блок которого сохраняется")
    payload: dict[str, Any] = Field(description="Блок документа по схеме текущего этапа")


SEARCH_SOURCES_SPEC = FunctionDefinition(
    name="search_sources",
    description=(
        "Найти релевантные фрагменты в базе источников: ФГОС, профессиональные стандарты, "
        "примеры образовательных программ. Возвращает фрагменты с идентификаторами документов; "
        "ссылаться в программе можно только на документы из результата поиска."
    ),
    parameters=SearchSourcesArguments.model_json_schema(),
)

BEGIN_PROGRAM_SPEC = FunctionDefinition(
    name="begin_program",
    description=(
        "Начать разработку образовательной программы. Вызывай, когда методист назвал тему, "
        "аудиторию, цель, объём в часах и дополнительные требования. После вызова backend "
        "последовательно сгенерирует блоки программы и пришлёт черновик на подтверждение. "
        "Если критически важных сведений не хватает, сначала задай уточняющие вопросы."
    ),
    parameters=BeginProgramArguments.model_json_schema(),
)

SAVE_PROGRAM_DRAFT_SPEC = FunctionDefinition(
    name="save_program_draft",
    description=(
        "Сохранить один блок программы на текущем этапе. Параметр stage — текущий этап, "
        "payload — блок по схеме из системной инструкции этапа. Вызывай ровно один раз, "
        "когда блок готов; после сохранения следующий этап запустит backend."
    ),
    parameters=SaveProgramDraftArguments.model_json_schema(),
)

GET_PROGRAM_DRAFT_SPEC = FunctionDefinition(
    name="get_program_draft",
    description=(
        "Получить текущее состояние программы и сохранённый черновик, если все блоки уже "
        "заполнены. Вызывай перед правками или если нужно вспомнить, что уже сохранено."
    ),
    parameters=GetProgramDraftArguments.model_json_schema(),
)

APPROVE_PROGRAM_DRAFT_SPEC = FunctionDefinition(
    name="approve_program_draft",
    description=(
        "Утвердить готовый черновик программы. Вызывай только после явного подтверждения "
        "методиста («подтверждаю», «утверждаю», «согласен»). После утверждения черновик "
        "становится основой следующего этапа полной генерации."
    ),
    parameters=ApproveProgramDraftArguments.model_json_schema(),
)


def _tool(spec: FunctionDefinition) -> Tool:
    return Tool(function=spec)


SEARCH_SOURCES_TOOL = _tool(SEARCH_SOURCES_SPEC)
BEGIN_PROGRAM_TOOL = _tool(BEGIN_PROGRAM_SPEC)
SAVE_PROGRAM_DRAFT_TOOL = _tool(SAVE_PROGRAM_DRAFT_SPEC)
GET_PROGRAM_DRAFT_TOOL = _tool(GET_PROGRAM_DRAFT_SPEC)
APPROVE_PROGRAM_DRAFT_TOOL = _tool(APPROVE_PROGRAM_DRAFT_SPEC)

#: The tools the intake chat uses: it starts the process or approves the draft.
INTAKE_TOOLS: list[Tool] = [BEGIN_PROGRAM_TOOL, APPROVE_PROGRAM_DRAFT_TOOL, GET_PROGRAM_DRAFT_TOOL]

#: The tools a stage uses: it searches and saves exactly one block.
STAGE_TOOLS: list[Tool] = [SEARCH_SOURCES_TOOL, SAVE_PROGRAM_DRAFT_TOOL, GET_PROGRAM_DRAFT_TOOL]

#: Every tool, kept for the provider container's default and for tests.
AGENT_TOOLS: list[Tool] = [
    SEARCH_SOURCES_TOOL,
    BEGIN_PROGRAM_TOOL,
    SAVE_PROGRAM_DRAFT_TOOL,
    GET_PROGRAM_DRAFT_TOOL,
    APPROVE_PROGRAM_DRAFT_TOOL,
]


async def search_sources(ctx: ToolContext, arguments: SearchSourcesArguments) -> SearchSourcesResult:
    """Search the source catalog and return the fragments to cite.

    The ids of everything returned are remembered on the context: a source the
    model has not seen here (or read back in the state) must not end up in the
    programme, so this is the only place that widens what may be cited.
    """
    fragments = await search(arguments.query, kinds=arguments.kinds or None, limit=arguments.limit)
    if fragments:
        ctx.known_source_ids.update(fragment.source.id for fragment in fragments)
        if ctx.state is not None:
            ctx.dirty = True
    if not fragments:
        return SearchSourcesResult(
            query=arguments.query,
            note=(
                "По запросу ничего не найдено. Сформулируй запрос иначе или уточни вид деятельности; "
                "не выдумывай источники и не ссылайся на документы, которых нет в результатах."
            ),
        )
    return SearchSourcesResult(query=arguments.query, fragments=fragments)


@dataclass(frozen=True)
class _Citations:
    """What resolving cited ids against the catalog found."""

    sources: list[Source]
    unknown: list[str]
    unsearched: list[str]
    not_standards: list[str]


def _resolve_citations(
    ctx: ToolContext,
    source_ids: list[str],
    *,
    standards: frozenset[str] = frozenset(),
) -> _Citations:
    """Replace cited ids with their catalog records.

    Three ways a citation can be wrong, and all of them are refused. An id the
    catalog does not know is an invented document. An id the catalog knows but
    this run has not shown the model — no ``search_sources`` call returned it and
    no earlier block recorded it — is equally unverified. An id a competence
    names as its ``standard_source_id`` must be a professional standard, because
    that field says where the competence comes from. The canonical catalog record
    wins over the one the model reproduced, so titles cannot drift.
    """
    citations = _Citations(sources=[], unknown=[], unsearched=[], not_standards=[])
    seen: set[str] = set()
    for source_id in source_ids:
        if source_id in seen:
            continue
        seen.add(source_id)
        source = get_source(source_id)
        if source is None:
            citations.unknown.append(source_id)
        elif source_id not in ctx.known_source_ids:
            citations.unsearched.append(source_id)
        elif source_id in standards and source.kind is not SourceKind.PROFESSIONAL_STANDARD:
            citations.not_standards.append(source_id)
        else:
            citations.sources.append(source)
            ctx.known_source_ids.add(source_id)
    return citations


def _citation_error(citations: _Citations) -> SaveProgramDraftResult | None:
    if citations.unknown:
        return SaveProgramDraftResult(
            ok=False,
            error="Неизвестные источники: " + ", ".join(citations.unknown),
            details=["Используй только источники из результатов search_sources; выдуманные документы запрещены."],
        )
    if citations.unsearched:
        return SaveProgramDraftResult(
            ok=False,
            error="Не подтверждённые поиском источники: " + ", ".join(citations.unsearched),
            details=["Сначала найди документ вызовом search_sources и ссылайся только на то, что вернул поиск."],
        )
    if citations.not_standards:
        return SaveProgramDraftResult(
            ok=False,
            error="Не профессиональные стандарты в standard_source_id: " + ", ".join(citations.not_standards),
            details=["Компетенция опирается на профстандарт: у стандарта kind = professional_standard."],
        )
    return None


def _describe_validation_error(error: ValidationError) -> list[str]:
    """Flatten pydantic errors into ``path: message`` lines the model can act on."""
    return [f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in error.errors()]


async def begin_program(ctx: ToolContext, arguments: BeginProgramArguments) -> BeginProgramResult:
    """Start a programme run from the intake conversation.

    The handler only creates the envelope; the worker that owns the chat enqueues
    the first stage after this reply is stored. That keeps the tool free of a
    dependency on the task module and makes the transition testable on its own.
    """
    if ctx.stage is not None:
        return BeginProgramResult(ok=False, error="Начать программу можно только из обычного диалога.")
    if ctx.state is not None and not ctx.state.failed:
        return BeginProgramResult(
            ok=False,
            error="Программа уже создана.",
            details=["Дождись черновика или подтверди его; начинать заново не нужно."],
        )
    ctx.state = new_state(
        ctx.session_id,
        requested_hours=arguments.requested_hours,
        requirements=arguments.requirements,
        title=arguments.title,
    )
    ctx.known_source_ids = set(ctx.state.known_source_ids)
    ctx.started = True
    ctx.dirty = True
    log.info("program_started", chat_session_id=str(ctx.session_id), requested_hours=arguments.requested_hours)
    return BeginProgramResult(
        status=ctx.state.status,
        current_stage=ctx.state.current_stage,
        note="Программа поставлена в работу. Блоки будут сгенерированы последовательно.",
    )


async def save_program_draft(ctx: ToolContext, arguments: SaveProgramDraftArguments) -> SaveProgramDraftResult:
    """Validate one stage's block and write it into the programme state."""
    if ctx.stage is None:
        return SaveProgramDraftResult(
            ok=False,
            error="Сохранение блока доступно только внутри этапа генерации.",
        )
    if ctx.state is None:
        return SaveProgramDraftResult(ok=False, error="Программа ещё не создана.")
    if arguments.stage is not ctx.stage:
        return SaveProgramDraftResult(
            ok=False,
            error=f"Сейчас выполняется этап {ctx.stage.value}, а передан блок {arguments.stage.value}.",
            details=["Сохрани блок текущего этапа и не переходи к следующему."],
        )
    if ctx.state.stage_status(arguments.stage) is StageStatus.COMPLETED:
        return SaveProgramDraftResult(
            status=ctx.state.status,
            stage=arguments.stage,
            current_stage=ctx.state.current_stage,
            note="Блок этого этапа уже сохранён.",
        )

    block_type = STAGE_BLOCKS[arguments.stage]
    try:
        block = block_type.model_validate(arguments.payload)
    except ValidationError as error:
        return SaveProgramDraftResult(
            ok=False,
            error="Блок не соответствует схеме этапа",
            details=_describe_validation_error(error),
        )

    citations = _resolve_citations(
        ctx,
        _cited_ids(block),
        standards=_standard_ids(block),
    )
    refusal = _citation_error(citations)
    if refusal is not None:
        refusal.stage = arguments.stage
        return refusal

    block = _with_canonical_sources(block, citations.sources)
    try:
        ctx.state = apply_block(ctx.state, arguments.stage, block)
    except ValueError as error:
        return SaveProgramDraftResult(ok=False, error=str(error))
    ctx.dirty = True
    return SaveProgramDraftResult(
        status=ctx.state.status,
        stage=arguments.stage,
        current_stage=ctx.state.current_stage,
        note="Блок сохранён. Следующий этап запустит backend.",
    )


def _cited_ids(block: BaseModel) -> list[str]:
    """Every source id the block refers to, in first-seen order."""
    ids: list[str] = []
    sources = getattr(block, "sources", None)
    if sources:
        ids.extend(source.id for source in sources)
    summary = getattr(block, "summary", None)
    if summary is not None:
        ids.extend(summary.basis_source_ids)
    competencies = getattr(block, "competencies", None)
    if competencies:
        ids.extend(item.standard_source_id for item in competencies if item.standard_source_id)
    return ids


def _standard_ids(block: BaseModel) -> frozenset[str]:
    competencies = getattr(block, "competencies", None) or []
    return frozenset(item.standard_source_id for item in competencies if item.standard_source_id)


def _with_canonical_sources(block: BaseModel, sources: list[Source]) -> BaseModel:
    """Replace the reproduced records with the catalog ones where the block has a list."""
    if not sources or not hasattr(block, "sources"):
        return block
    return block.model_copy(update={"sources": sources})


async def approve_program_draft(
    ctx: ToolContext, _arguments: ApproveProgramDraftArguments
) -> ApproveProgramDraftResult:
    """Freeze a complete draft after the methodist explicitly confirmed it."""
    if ctx.state is None:
        return ApproveProgramDraftResult(ok=False, error="Черновик ещё не создан.")
    if ctx.state.status is ProgramStatus.APPROVED:
        return ApproveProgramDraftResult(status=ctx.state.status, note="Черновик уже утверждён.")
    if ctx.state.status is not ProgramStatus.PENDING_APPROVAL:
        return ApproveProgramDraftResult(
            ok=False,
            error="Черновик ещё не готов к утверждению.",
            details=["Дождись, пока все блоки программы будут сгенерированы."],
        )
    try:
        ctx.state = approve_state(ctx.state)
    except (ValueError, ValidationError) as error:
        return ApproveProgramDraftResult(
            ok=False,
            error="Черновик не прошёл проверку",
            details=[str(error)],
        )
    ctx.approved = True
    ctx.dirty = True
    log.info("program_approved", chat_session_id=str(ctx.session_id))
    return ApproveProgramDraftResult(
        status=ctx.state.status,
        note="Черновик утверждён и сохранён для следующего этапа.",
    )


async def get_program_draft(ctx: ToolContext, _arguments: GetProgramDraftArguments) -> GetProgramDraftResult:
    """Return a copy of the current state and, when complete, the draft."""
    if ctx.state is None:
        return GetProgramDraftResult(status=ProgramStatus.DRAFT, note="Программа ещё не создана.")
    ctx.known_source_ids.update(ctx.state.known_source_ids)
    state = ctx.state.model_copy(deep=True)
    draft = None
    try:
        draft = state.to_draft()
    except ValueError, ValidationError:
        draft = None
    return GetProgramDraftResult(
        status=state.status,
        current_stage=state.current_stage,
        state=state,
        draft=draft,
    )


def snapshot_state(ctx: ToolContext) -> ProgramState | None:
    """The state to persist, with the run's known sources folded in.

    The handlers track provenance on the context because a search does not touch
    a document block; this is where that provenance joins the envelope so it
    survives the worker that found it.
    """
    if ctx.state is None:
        return None
    return set_known_sources(ctx.state, ctx.known_source_ids)


#: A handler takes the context and its own arguments model, and answers a result.
Handler = Callable[[ToolContext, Any], Awaitable[ToolResult]]


@dataclass(frozen=True)
class AgentTool:
    """One tool as the dispatcher needs it: the spec, its arguments, its handler."""

    spec: FunctionDefinition
    arguments: type[BaseModel]
    handler: Handler


AGENT_TOOL_REGISTRY: dict[str, AgentTool] = {
    SEARCH_SOURCES_SPEC.name: AgentTool(SEARCH_SOURCES_SPEC, SearchSourcesArguments, search_sources),
    BEGIN_PROGRAM_SPEC.name: AgentTool(BEGIN_PROGRAM_SPEC, BeginProgramArguments, begin_program),
    SAVE_PROGRAM_DRAFT_SPEC.name: AgentTool(SAVE_PROGRAM_DRAFT_SPEC, SaveProgramDraftArguments, save_program_draft),
    GET_PROGRAM_DRAFT_SPEC.name: AgentTool(GET_PROGRAM_DRAFT_SPEC, GetProgramDraftArguments, get_program_draft),
    APPROVE_PROGRAM_DRAFT_SPEC.name: AgentTool(
        APPROVE_PROGRAM_DRAFT_SPEC, ApproveProgramDraftArguments, approve_program_draft
    ),
}


async def execute_tool(ctx: ToolContext, call: FunctionCall) -> ToolResult:
    """Run one function call: validate its arguments, then hand them to the tool.

    ``FunctionCall.arguments`` is the JSON string the providers normalise to, and
    validation belongs here rather than in a handler, which receives its arguments
    model ready to use. A broken call is not a broken generation: the model may
    correct itself in the next round, so every failure — an unknown name,
    malformed JSON, arguments that do not validate, an unexpected exception —
    becomes a result rather than a raise.
    """
    tool = AGENT_TOOL_REGISTRY.get(call.name)
    if tool is None:
        log.warning("unknown_tool_call", tool=call.name)
        return ToolResult(ok=False, error=f"Неизвестная функция: {call.name}")
    try:
        arguments = json.loads(call.arguments)
    except json.JSONDecodeError as error:
        log.warning("tool_arguments_invalid", tool=call.name, exc=error)
        return ToolResult(ok=False, error="Некорректные аргументы вызова")
    try:
        parsed = tool.arguments.model_validate(arguments)
    except ValidationError as error:
        log.warning("tool_arguments_invalid", tool=call.name, errors=_describe_validation_error(error))
        return ToolResult(ok=False, error="Некорректные аргументы вызова", details=_describe_validation_error(error))
    try:
        result = await tool.handler(ctx, parsed)
    except Exception as error:
        log.error("tool_call_failed", tool=call.name, exc=error)
        return ToolResult(ok=False, error="Внутренняя ошибка инструмента")
    log.info("tool_call", tool=call.name, ok=result.ok)
    return result
