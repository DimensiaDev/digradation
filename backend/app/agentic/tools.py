"""The user functions the model may call, and their implementations.

This module is one half of the agent contract. The specifications below are sent
to the provider with a generation request, and the pydantic schemas of
:mod:`app.agentic.state` are the other half: the model fills the arguments from
the specification, the arguments are validated against that schema at the call
boundary, and the handlers work with validated models on both sides — in and out.

The handlers keep no storage of their own. They resolve citations against the
catalog and leave the run state on :class:`ToolContext` for the caller to
checkpoint; that is what makes a worker that dies mid-stage lose nothing, because
the block it already saved is on the state the worker commits after every call.

What is offered depends on where the run is. The intake offers
:func:`begin_program` and :func:`approve_program_draft`; each stage offers
:func:`save_program_draft` with the arguments model of its own block, plus the
catalog search where the stage cites documents. The dispatcher refuses a call the
caller did not offer, so a model that remembers a tool from another stage cannot
write a block early.

:func:`execute_tool` is the boundary with the provider: it turns a
:class:`FunctionCall` — a name and the JSON string of arguments — into one handler
call, and every failure, arguments that do not validate included, comes back as a
result with ``ok: false`` instead of a raise, so the model can correct itself
inside the same reply. Validating the arguments is the caller's job, never the
handler's: a handler may take its arguments model for granted.
"""

import copy
import json
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

import structlog.stdlib
from pydantic import BaseModel, Field, ValidationError

from app.agentic.models import ProgramStatus, Source, SourceKind, Stage
from app.agentic.sources import SourceFragment, get_source, search
from app.agentic.state import (
    STAGE_ORDER,
    CompetencesBlock,
    HoursBlock,
    ProgramState,
    SourcesBlock,
    SummaryBlock,
    new_state,
)
from app.providers.models import FunctionCall, FunctionDefinition, Tool, ToolCall

log = structlog.stdlib.get_logger(__name__)

#: Name of the tool that saves the block of the current stage. One name, and the
#: arguments model changes with the stage, so the model has one habit to learn.
SAVE_PROGRAM_DRAFT = "save_program_draft"

#: The block model of each stage, in the order the stages run.
STAGE_BLOCK: dict[Stage, type[BaseModel]] = {
    Stage.COMPETENCES: CompetencesBlock,
    Stage.HOURS: HoursBlock,
    Stage.SOURCES: SourcesBlock,
    Stage.SUMMARY: SummaryBlock,
}

#: Stages that may look documents up before saving their block.
SEARCHING_STAGES = frozenset({Stage.COMPETENCES, Stage.SOURCES, Stage.SUMMARY})


@dataclass
class ToolContext:
    """What a handler needs beyond the arguments of the call.

    ``state`` is the run this call belongs to; a handler that saves a block
    replaces it with the transitioned state and sets ``dirty`` so the caller knows
    there is something to commit. ``known_source_ids`` is the provenance of the
    run: only a document the model has actually seen — through ``search_sources``
    or in a block an earlier stage committed — may be cited, so an invented or
    half-remembered id is refused rather than kept.
    """

    session_id: uuid.UUID
    state: ProgramState | None = None
    known_source_ids: set[str] = field(default_factory=set)
    dirty: bool = False
    #: Whether the stage's block was accepted in this turn. The stage task reads
    #: it to tell "the model saved and answered" from "the model only answered".
    saved: bool = False


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


class SaveProgramDraftResult(ToolResult):
    """State of the run after a block was accepted."""

    status: ProgramStatus | None = None
    stage: Stage | None = None


class BeginProgramResult(ToolResult):
    """What starting a run tells the model."""

    stage: Stage | None = None


class ApproveProgramDraftResult(ToolResult):
    """What confirming the drafted programme tells the model."""


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


class BeginProgramArguments(BaseModel):
    """Arguments of :func:`begin_program`."""

    requirements: str = Field(
        min_length=1,
        max_length=4000,
        description=(
            "Всё, что методист сказал о программе: тема, аудитория, цель, требования, "
            "пожелания к содержанию и объёму"
        ),
    )
    requested_hours: float | None = Field(
        default=None,
        gt=0,
        le=1000,
        description="Объём программы в академических часах, если методист его назвал",
    )


class ApproveProgramDraftArguments(BaseModel):
    """Arguments of :func:`approve_program_draft`: the tool takes none."""


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline ``$defs``/``$ref`` so the parameters are one self-contained object.

    The arguments schema is sent to the model verbatim, and a schema that requires
    walking ``$defs`` to understand is needless work for it. The definitions of
    the blocks are small enough to inline without bloating the request.
    """
    definitions = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            reference = node.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/$defs/"):
                return resolve(copy.deepcopy(definitions[reference.removeprefix("#/$defs/")]))
            return {key: resolve(value) for key, value in node.items() if key != "$defs"}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return resolve(schema)


SEARCH_SOURCES_SPEC = FunctionDefinition(
    name="search_sources",
    description=(
        "Найти релевантные фрагменты в базе источников: ФГОС, профессиональные стандарты, "
        "примеры образовательных программ. Возвращает фрагменты с идентификаторами документов; "
        "ссылаться в программе можно только на документы из результата поиска."
    ),
    parameters=_inline_refs(SearchSourcesArguments.model_json_schema()),
)

BEGIN_PROGRAM_SPEC = FunctionDefinition(
    name="begin_program",
    description=(
        "Запустить формирование образовательной программы по заданию методиста. "
        "Вызывай, когда из сообщения понятны тема и аудитория программы. Не вызывай повторно: "
        "программа формируется этапами сама."
    ),
    parameters=_inline_refs(BeginProgramArguments.model_json_schema()),
)

APPROVE_PROGRAM_DRAFT_SPEC = FunctionDefinition(
    name="approve_program_draft",
    description=(
        "Подтвердить готовый черновик программы. Вызывай только после того, как методист явно "
        "согласился с черновиком; при сомнении уточни, чего не хватает."
    ),
    parameters=_inline_refs(ApproveProgramDraftArguments.model_json_schema()),
)

#: What each stage's save call is for, in the words the model reads.
_SAVE_DESCRIPTIONS: dict[Stage, str] = {
    Stage.COMPETENCES: (
        "Сохранить название и тип программы, а также компетенции с результатами освоения: "
        "знаниями, умениями и практическим опытом."
    ),
    Stage.HOURS: (
        "Сохранить учебный план: модули, темы с часами и график обучения. Сумма часов тем "
        "должна совпадать с заявленным объёмом программы."
    ),
    Stage.SOURCES: (
        "Сохранить источники, на которые опирается программа. Ссылайся только на документы, "
        "которые вернул search_sources."
    ),
    Stage.SUMMARY: ("Сохранить паспорт программы: цель, задачи, аудиторию, форму обучения и документ по итогам."),
}

#: The save specification of each stage, built from that stage's block model, so
#: the model never sees the fields of a block it is not writing.
STAGE_SPECS: dict[Stage, FunctionDefinition] = {
    stage: FunctionDefinition(
        name=SAVE_PROGRAM_DRAFT,
        description=_SAVE_DESCRIPTIONS[stage],
        parameters=_inline_refs(STAGE_BLOCK[stage].model_json_schema()),
    )
    for stage in STAGE_ORDER
}


async def search_sources(ctx: ToolContext, arguments: SearchSourcesArguments) -> SearchSourcesResult:
    """Search the source catalog and return the fragments to cite.

    The ids of everything returned are remembered on the context: a source the
    model has not seen here (or in a block an earlier stage committed) must not end
    up in the draft, so this is the only place that widens what may be cited.
    """
    fragments = await search(arguments.query, kinds=arguments.kinds or None, limit=arguments.limit)
    ctx.known_source_ids.update(fragment.source.id for fragment in fragments)
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
    """What resolving a set of citations against the catalog found."""

    sources: list[Source]
    unknown: list[str]
    unsearched: list[str]
    not_standards: list[str]


def _resolve_sources(ctx: ToolContext, source_ids: Iterable[str], *, standards: Iterable[str] = ()) -> _Citations:
    """Replace the cited ids with their catalog records.

    Three ways a citation can be wrong, and all of them must be refused. An id the
    catalog does not know is an invented document. An id the catalog knows but
    this run has not shown the model — no ``search_sources`` call returned it and
    no earlier block carried it — is equally unverified: the catalog is wide, and a
    plausible-looking ``src-NN`` is easy to guess. An id a competence names as its
    ``standard_source_id`` must be a professional standard, because that field
    says where the competence comes from: a ФГОС or a handbook there would
    misattribute the labour function. The canonical record is kept instead of the
    one the model reproduced, so titles cannot drift.
    """
    standard_ids = set(standards)
    citations = _Citations(sources=[], unknown=[], unsearched=[], not_standards=[])
    for source_id in dict.fromkeys(source_ids):
        source = get_source(source_id)
        if source is None:
            citations.unknown.append(source_id)
        elif source_id not in ctx.known_source_ids:
            citations.unsearched.append(source_id)
        elif source_id in standard_ids and source.kind is not SourceKind.PROFESSIONAL_STANDARD:
            citations.not_standards.append(source_id)
        else:
            citations.sources.append(source)
    return citations


def _citation_error(citations: _Citations) -> SaveProgramDraftResult | None:
    """The refusal a bad citation earns, or ``None`` when every one resolved."""
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


def _save_competences(ctx: ToolContext, state: ProgramState, block: CompetencesBlock) -> SaveProgramDraftResult:
    standards = [competence.standard_source_id for competence in block.competencies if competence.standard_source_id]
    citations = _resolve_sources(ctx, standards, standards=standards)
    if error := _citation_error(citations):
        return error
    ctx.state = state.with_competences(block, citations.sources)
    ctx.dirty = True
    return SaveProgramDraftResult(
        status=ctx.state.status,
        stage=ctx.state.current_stage,
        note="Компетенции сохранены. Кратко подтверди, что блок готов, и остановись.",
    )


def _save_hours(ctx: ToolContext, state: ProgramState, block: HoursBlock) -> SaveProgramDraftResult:
    declared = {
        outcome.id
        for competence in state.competencies
        for outcome in (*competence.experience, *competence.skills, *competence.knowledge)
    }
    problems = state.topic_problems(block.curriculum, declared) + state.curriculum_problems(block.curriculum)
    if problems:
        return SaveProgramDraftResult(
            ok=False,
            error="Учебный план не принят",
            details=problems,
            note="Исправь часы и ссылки на результаты освоения и сохрани блок снова.",
        )
    ctx.state = state.with_curriculum(block)
    ctx.dirty = True
    return SaveProgramDraftResult(
        status=ctx.state.status,
        stage=ctx.state.current_stage,
        note="Учебный план сохранён. Кратко подтверди, что блок готов, и остановись.",
    )


def _save_sources(ctx: ToolContext, state: ProgramState, block: SourcesBlock) -> SaveProgramDraftResult:
    citations = _resolve_sources(ctx, [source.id for source in block.sources])
    if error := _citation_error(citations):
        return error
    ctx.state = state.with_sources(SourcesBlock(sources=citations.sources))
    ctx.dirty = True
    return SaveProgramDraftResult(
        status=ctx.state.status,
        stage=ctx.state.current_stage,
        note="Источники сохранены. Кратко подтверди, что блок готов, и остановись.",
    )


def _save_summary(ctx: ToolContext, state: ProgramState, block: SummaryBlock) -> SaveProgramDraftResult:
    citations = _resolve_sources(ctx, block.summary.basis_source_ids)
    if error := _citation_error(citations):
        return error
    ctx.state = state.with_summary(block, citations.sources)
    ctx.dirty = True
    return SaveProgramDraftResult(
        status=ctx.state.status,
        stage=ctx.state.current_stage,
        note="Паспорт программы сохранён. Черновик собирается, подтверждение попросит методист.",
    )


async def save_program_draft(ctx: ToolContext, arguments: BaseModel) -> SaveProgramDraftResult:
    """Keep the block the stage produced, after binding its citations.

    The dispatcher validates the arguments against the block model of the stage
    the run is on, so the handler body only has to route the block to its
    transition. A block saved here is not a stage completed: the worker marks the
    stage done after the model stops calling tools, and the caller checkpoints the
    state in between.
    """
    state = ctx.state
    if state is None:
        return SaveProgramDraftResult(ok=False, error="Программа ещё не начата")
    match arguments:
        case CompetencesBlock():
            result = _save_competences(ctx, state, arguments)
        case HoursBlock():
            result = _save_hours(ctx, state, arguments)
        case SourcesBlock():
            result = _save_sources(ctx, state, arguments)
        case SummaryBlock():
            result = _save_summary(ctx, state, arguments)
        case _:
            return SaveProgramDraftResult(ok=False, error="Неизвестный блок программы")
    if result.ok:
        ctx.saved = True
    return result


async def begin_program(ctx: ToolContext, arguments: BeginProgramArguments) -> BeginProgramResult:
    """Start a run from the methodist's task.

    A chat holds one programme. A failed run may be started again — that is the
    retry the methodist is owed after the pipeline gave up — but a run that is
    still working, still awaiting confirmation, or already approved is not
    silently replaced: its blocks would be lost with no way to get them back.
    """
    if ctx.state is not None and not ctx.state.failed:
        return BeginProgramResult(
            ok=False,
            error="В этом чате уже есть программа",
            note=(
                "Не начинай новую программу: скажи методисту, что в этом чате уже есть черновик "
                "или идёт генерация, и предложи продолжить в нём."
            ),
        )
    ctx.state = new_state(
        ctx.session_id, requested_hours=arguments.requested_hours, requirements=arguments.requirements
    )
    ctx.dirty = True
    log.info("program_started", chat_session_id=str(ctx.session_id), requested_hours=arguments.requested_hours)
    return BeginProgramResult(
        stage=ctx.state.current_stage,
        note="Программа запущена и формируется этапами. Скажи методисту, что генерация началась.",
    )


async def approve_program_draft(
    ctx: ToolContext, _arguments: ApproveProgramDraftArguments
) -> ApproveProgramDraftResult:
    """Confirm the rendered draft.

    Only a rendered draft can be approved: before the finalize task writes it the
    methodist has not seen the document, and approving a state they never read is
    not a confirmation.
    """
    state = ctx.state
    if state is None:
        return ApproveProgramDraftResult(ok=False, error="В этом чате нет программы")
    if state.status is ProgramStatus.APPROVED:
        return ApproveProgramDraftResult(ok=False, error="Программа уже утверждена", note="Ничего делать не нужно.")
    if not state.awaiting_approval:
        return ApproveProgramDraftResult(
            ok=False,
            error="Черновик ещё не готов к подтверждению",
            note="Дождись, когда черновик будет показан методисту, и только потом подтверждай.",
        )
    ctx.state = state.approved()
    ctx.dirty = True
    log.info("program_approved", chat_session_id=str(ctx.session_id))
    return ApproveProgramDraftResult(note="Черновик утверждён. Скажи методисту, что программа принята.")


#: A handler takes the context and its own arguments model, and answers a result.
Handler = Callable[[ToolContext, Any], Awaitable[ToolResult]]


@dataclass(frozen=True)
class AgentTool:
    """One tool as the dispatcher needs it: the spec, its arguments, its handler.

    ``arguments`` is ``None`` for :func:`save_program_draft`, whose payload is the
    block of the stage the run is on rather than one fixed model.
    """

    spec: FunctionDefinition
    arguments: type[BaseModel] | None
    handler: Handler


#: The tools the model may call, keyed by the name in the specification. What the
#: caller offers is a subset: :data:`INTAKE_TOOLS` or :func:`stage_tools`.
AGENT_TOOL_REGISTRY: dict[str, AgentTool] = {
    SEARCH_SOURCES_SPEC.name: AgentTool(SEARCH_SOURCES_SPEC, SearchSourcesArguments, search_sources),
    BEGIN_PROGRAM_SPEC.name: AgentTool(BEGIN_PROGRAM_SPEC, BeginProgramArguments, begin_program),
    APPROVE_PROGRAM_DRAFT_SPEC.name: AgentTool(
        APPROVE_PROGRAM_DRAFT_SPEC, ApproveProgramDraftArguments, approve_program_draft
    ),
    SAVE_PROGRAM_DRAFT: AgentTool(STAGE_SPECS[Stage.COMPETENCES], None, save_program_draft),
}

#: Tools offered while the methodist is still describing the programme.
INTAKE_TOOLS: list[Tool] = [Tool(function=spec) for spec in (BEGIN_PROGRAM_SPEC, APPROVE_PROGRAM_DRAFT_SPEC)]


def stage_tools(stage: Stage) -> list[Tool]:
    """The tools offered at one stage: its own block, and the search where it cites."""
    specs = [STAGE_SPECS[stage]]
    if stage in SEARCHING_STAGES:
        specs.insert(0, SEARCH_SOURCES_SPEC)
    return [Tool(function=spec) for spec in specs]


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
    arguments_model = tool.arguments
    if arguments_model is None:
        if ctx.state is None:
            return ToolResult(ok=False, error="Программа ещё не начата")
        arguments_model = STAGE_BLOCK[ctx.state.current_stage]
    try:
        arguments = json.loads(call.arguments)
    except json.JSONDecodeError as error:
        log.warning("tool_arguments_invalid", tool=call.name, exc=error)
        return ToolResult(ok=False, error="Некорректные аргументы вызова")
    try:
        parsed = arguments_model.model_validate(arguments)
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


def call_executor(ctx: ToolContext, offered: list[Tool]) -> Callable[[ToolCall], Awaitable[ToolResult]]:
    """A call handler that runs only the tools the caller offered.

    The provider sees one list per turn, but the registry holds every tool. This
    is what keeps a model that remembers ``begin_program`` from starting a second
    run in the middle of a stage.
    """
    names = {tool.function.name for tool in offered}

    async def call(tool_call: ToolCall) -> ToolResult:
        if tool_call.function.name not in names:
            log.warning("tool_not_offered", tool=tool_call.function.name)
            return ToolResult(ok=False, error=f"Функция {tool_call.function.name} недоступна на этом шаге")
        return await execute_tool(ctx, tool_call.function)

    return call


def _describe_validation_error(error: ValidationError) -> list[str]:
    """Flatten pydantic errors into ``path: message`` lines the model can act on."""
    return [f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in error.errors()]
