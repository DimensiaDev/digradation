"""The user functions the model may call, and their implementations.

This module is one half of the agent contract. The specifications below are sent
to the provider with every generation request, and the pydantic schemas of
``app.agentic.models`` are the other half: the model fills the arguments from the
specification, the arguments are validated against those schemas at the call
boundary, and the handlers work with the validated models on both sides — in and
out — rather than with dictionaries.

The handlers keep no storage of their own: they resolve citations against the
catalog, answer, and leave the validated draft on :class:`ToolContext` for the
caller to store.

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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog.stdlib
from pydantic import BaseModel, Field, ValidationError

from app.agentic.models import ProgramDraft, ProgramStatus, Source, SourceKind
from app.agentic.sources import SourceFragment, get_source, search
from app.providers.models import FunctionCall, FunctionDefinition, Tool

log = structlog.stdlib.get_logger(__name__)


@dataclass
class ToolContext:
    """What a handler needs beyond the arguments of the call.

    ``session_id`` names the chat in the logs. ``draft`` is what
    :func:`save_program_draft` validated and kept for the caller to store — a
    later revision of the same run reads it back through
    :func:`get_program_draft`. ``known_source_ids`` is the provenance of the run:
    only a document the model has actually seen — through ``search_sources`` or in
    the draft it saved — may be cited, so an invented or half-remembered id is
    refused rather than kept.
    """

    session_id: uuid.UUID
    draft: ProgramDraft | None = None
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


class SaveProgramDraftResult(ToolResult):
    """State of the process after a draft was accepted."""

    status: ProgramStatus | None = None


class GetProgramDraftResult(ToolResult):
    """The draft this run has saved, if there is one."""

    status: ProgramStatus
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


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline ``$defs``/``$ref`` so the parameters are one self-contained object.

    The arguments schema is sent to the model verbatim, and a schema that requires
    walking ``$defs`` to understand is needless work for it. The definitions of
    the draft are small enough to inline without bloating the request.
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

SAVE_PROGRAM_DRAFT_SPEC = FunctionDefinition(
    name="save_program_draft",
    description=(
        "Сохранить структурированный черновик программы и передать его методисту на подтверждение. "
        "Вызывай, когда собраны тема, аудитория, цель, часы и требования. После сохранения представь "
        "черновик в читаемом виде, попроси подтверждение и остановись."
    ),
    parameters=_inline_refs(ProgramDraft.model_json_schema()),
)

GET_PROGRAM_DRAFT_SPEC = FunctionDefinition(
    name="get_program_draft",
    description=(
        "Получить черновик программы, сохранённый в этом диалоге, и его состояние (draft — черновика "
        "ещё нет, pending_approval — ждёт подтверждения). Вызывай перед правками по просьбе методиста."
    ),
    parameters=_inline_refs(GetProgramDraftArguments.model_json_schema()),
)


async def search_sources(ctx: ToolContext, arguments: SearchSourcesArguments) -> SearchSourcesResult:
    """Search the source catalog and return the fragments to cite.

    The ids of everything returned are remembered on the context: a source the
    model has not seen here (or read back in the draft it saved) must not end up
    in the draft, so this is the only place that widens what may be cited.
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


def _cited_source_ids(draft: ProgramDraft) -> list[str]:
    """Every source id the draft refers to, in first-seen order, once each."""
    ids = [source.id for source in draft.sources]
    ids.extend(draft.summary.basis_source_ids)
    ids.extend(competence.standard_source_id for competence in draft.competencies if competence.standard_source_id)
    return list(dict.fromkeys(ids))


@dataclass(frozen=True)
class _Citations:
    """What resolving the draft's citations against the catalog found."""

    sources: list[Source]
    unknown: list[str]
    unsearched: list[str]
    not_standards: list[str]


def _resolve_sources(ctx: ToolContext, draft: ProgramDraft) -> _Citations:
    """Replace the cited sources with their catalog records.

    Three ways a citation can be wrong, and all of them must be refused. An id the
    catalog does not know is an invented document. An id the catalog knows but
    this run has not shown the model — no ``search_sources`` call returned it and
    no saved draft carried it — is equally unverified: the catalog is wide, and a
    plausible-looking ``src-NN`` is easy to guess. An id a competence names as its
    ``standard_source_id`` must be a professional standard, because that field
    says where the competence comes from: a ФГОС or a handbook there would
    misattribute the labour function. The canonical record is kept instead of the
    one the model reproduced, so titles cannot drift.

    The last two refusals wait for the catalog: while it answers nothing, every id
    is unknown and neither branch below can trigger.
    """
    standards = {competence.standard_source_id for competence in draft.competencies if competence.standard_source_id}
    citations = _Citations(sources=[], unknown=[], unsearched=[], not_standards=[])
    for source_id in _cited_source_ids(draft):
        source = get_source(source_id)
        if source is None:
            citations.unknown.append(source_id)
        elif source_id not in ctx.known_source_ids:
            citations.unsearched.append(source_id)
        elif source_id in standards and source.kind is not SourceKind.PROFESSIONAL_STANDARD:
            citations.not_standards.append(source_id)
        else:
            citations.sources.append(source)
    return citations


def _describe_validation_error(error: ValidationError) -> list[str]:
    """Flatten pydantic errors into ``path: message`` lines the model can act on."""
    return [f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in error.errors()]


async def save_program_draft(ctx: ToolContext, arguments: ProgramDraft) -> SaveProgramDraftResult:
    """Bind the draft's citations to the catalog and keep the draft.

    The arguments are the draft schema itself, so a draft that does not match the
    schema never reaches this handler. Saving is what moves the process to
    ``pending_approval``: from this call on the methodist has something to
    confirm, and until they do, the agent is told to present the draft and stop.
    The validated draft — the handler's own copy of it, which is what the caller
    stores — stays on the context.
    """
    citations = _resolve_sources(ctx, arguments)
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
    ctx.draft = arguments.model_copy(update={"sources": citations.sources})

    log.info("program_draft_validated", chat_session_id=str(ctx.session_id), sources=len(citations.sources))
    return SaveProgramDraftResult(
        status=ProgramStatus.PENDING_APPROVAL,
        note="Черновик проверен и ждёт подтверждения методиста. Представь его и остановись.",
    )


async def get_program_draft(ctx: ToolContext, _arguments: GetProgramDraftArguments) -> GetProgramDraftResult:
    """Return the draft this run has saved.

    Reading the draft back is what a revision starts with, and the citations it
    just read must stay usable on the way out: the sources of the saved draft are
    therefore counted as known to this run.
    """
    if ctx.draft is None:
        return GetProgramDraftResult(status=ProgramStatus.DRAFT, note="Черновик ещё не сохранён.")
    ctx.known_source_ids.update(source.id for source in ctx.draft.sources)
    return GetProgramDraftResult(status=ProgramStatus.PENDING_APPROVAL, draft=ctx.draft)


#: A handler takes the context and its own arguments model, and answers a result.
Handler = Callable[[ToolContext, Any], Awaitable[ToolResult]]


@dataclass(frozen=True)
class AgentTool:
    """One tool as the dispatcher needs it: the spec, its arguments, its handler."""

    spec: FunctionDefinition
    arguments: type[BaseModel]
    handler: Handler


#: The tools the model may call, keyed by the name in the specification. A
#: provider takes the specifications at construction — the v2 endpoint's
#: ``functions.specifications`` grouping is the provider's business, not the
#: agent's — and the dispatcher takes the arguments model to validate a call with.
AGENT_TOOL_REGISTRY: dict[str, AgentTool] = {
    SEARCH_SOURCES_SPEC.name: AgentTool(SEARCH_SOURCES_SPEC, SearchSourcesArguments, search_sources),
    SAVE_PROGRAM_DRAFT_SPEC.name: AgentTool(SAVE_PROGRAM_DRAFT_SPEC, ProgramDraft, save_program_draft),
    GET_PROGRAM_DRAFT_SPEC.name: AgentTool(GET_PROGRAM_DRAFT_SPEC, GetProgramDraftArguments, get_program_draft),
}

#: The tools offered to the model, one per function.
AGENT_TOOLS: list[Tool] = [Tool(function=tool.spec) for tool in AGENT_TOOL_REGISTRY.values()]


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
