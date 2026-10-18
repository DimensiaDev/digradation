"""The user functions GigaChat may call, and their implementations.

This module is one half of the agent contract. The specifications below are sent
to the provider with every generation request, and the pydantic schemas of
``app.agentic.models`` are the other half: the model fills the arguments, and the
handlers validate them against the very same schema the rest of the backend uses.
The parameters are generated from those models rather than maintained by hand, so
a schema change reaches the model without a second edit here.

Handlers never raise at the model. A failed call comes back as a result with
``ok: false`` and an explanation, which lets the model correct itself inside the
same reply instead of failing the whole generation.
"""

import copy
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any

import structlog.stdlib
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agentic.models import ProgramDraft, ProgramStatus, Source, SourceKind
from app.agentic.sources import get_source, search
from app.models.chat import DEFAULT_CHAT_NAME, MAX_CHAT_NAME_LENGTH, ChatSession
from app.models.program import ChatProgram
from app.providers.models import FunctionCall, FunctionSpecification, Tool, ToolFunctions

log = structlog.stdlib.get_logger(__name__)


@dataclass
class ToolContext:
    """What a handler needs beyond the arguments of the call.

    ``open_db`` is handed in rather than imported: the worker owns its session
    factory (``tsq_db``), and tests bind the handlers to their own database by
    passing a different factory. ``renamed_to`` is set when a handler names the
    chat, which the worker forwards to ``process_response``.
    """

    session_id: uuid.UUID
    open_db: Callable[[], AbstractAsyncContextManager[AsyncSession]]
    renamed_to: str | None = None


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


def _inline_refs(schema: dict) -> dict:
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


SEARCH_SOURCES_SPEC = FunctionSpecification(
    name="search_sources",
    description=(
        "Найти релевантные фрагменты в базе источников: ФГОС, профессиональные стандарты, "
        "примеры образовательных программ. Возвращает фрагменты с идентификаторами документов; "
        "ссылаться в программе можно только на документы из результата поиска."
    ),
    parameters=_inline_refs(SearchSourcesArguments.model_json_schema()),
)

SAVE_PROGRAM_DRAFT_SPEC = FunctionSpecification(
    name="save_program_draft",
    description=(
        "Сохранить структурированный черновик программы и передать его методисту на подтверждение. "
        "Вызывай, когда собраны тема, аудитория, цель, часы и требования. После сохранения представь "
        "черновик в читаемом виде, попроси подтверждение и остановись."
    ),
    parameters=_inline_refs(ProgramDraft.model_json_schema()),
)

GET_PROGRAM_DRAFT_SPEC = FunctionSpecification(
    name="get_program_draft",
    description=(
        "Получить сохранённый черновик программы и его состояние (draft — ещё нет черновика, "
        "pending_approval — ждёт подтверждения, approved — утверждён). Вызывай перед правками "
        "по просьбе методиста."
    ),
    parameters=_inline_refs(GetProgramDraftArguments.model_json_schema()),
)

#: The tool sent with every generation request. All the functions live in one
#: ``functions.specifications`` list, which is how the v2 endpoint groups them.
AGENT_TOOL = Tool(
    functions=ToolFunctions(specifications=[SEARCH_SOURCES_SPEC, SAVE_PROGRAM_DRAFT_SPEC, GET_PROGRAM_DRAFT_SPEC])
)


async def search_sources(_ctx: ToolContext, arguments: dict) -> dict:
    """Search the source catalog and return the fragments to cite.

    The context is part of the handler interface; this tool reads no database.
    """
    arguments = SearchSourcesArguments.model_validate(arguments)
    fragments = await search(arguments.query, kinds=arguments.kinds or None, limit=arguments.limit)
    result: dict[str, Any] = {
        "ok": True,
        "query": arguments.query,
        "fragments": [fragment.model_dump(mode="json") for fragment in fragments],
    }
    if not fragments:
        result["note"] = (
            "По запросу ничего не найдено. Сформулируй запрос иначе или уточни вид деятельности; "
            "не выдумывай источники и не ссылайся на документы, которых нет в результатах."
        )
    return result


def _resolve_sources(draft: ProgramDraft) -> tuple[list[Source], list[str]]:
    """Replace the cited sources with their catalog records.

    The model knows only the sources it received from ``search_sources``, and
    those are in the catalog; anything else is an invented document and must not
    reach the stored draft. The canonical record is stored instead of the one the
    model reproduced, so titles and identifiers cannot drift.
    """
    cited = dict.fromkeys([source.id for source in draft.sources] + list(draft.summary.basis_source_ids))
    resolved: list[Source] = []
    missing: list[str] = []
    for source_id in cited:
        source = get_source(source_id)
        if source is None:
            missing.append(source_id)
        else:
            resolved.append(source)
    return resolved, missing


def _describe_validation_error(error: ValidationError) -> list[str]:
    """Flatten pydantic errors into ``path: message`` lines the model can act on."""
    return [f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in error.errors()]


def _chat_name(title: str) -> str:
    """Fit a programme title into a chat name."""
    if len(title) <= MAX_CHAT_NAME_LENGTH:
        return title
    return title[: MAX_CHAT_NAME_LENGTH - 3].rstrip() + "..."


async def _name_session_after_draft(db: AsyncSession, ctx: ToolContext, title: str) -> None:
    """Name the chat after the draft, unless the methodist named it themselves."""
    session = await db.get(ChatSession, ctx.session_id)
    if session is None or session.name != DEFAULT_CHAT_NAME:
        return
    session.name = _chat_name(title)
    ctx.renamed_to = session.name


async def save_program_draft(ctx: ToolContext, arguments: dict) -> dict:
    """Validate the draft, bind its sources to the catalog and store it.

    Saving is what moves the process to ``pending_approval``: from this call on
    the methodist has something to confirm, and until they do, the agent is told
    to present the draft and stop. An approved draft is frozen — revisions would
    silently invalidate the confirmation the next stage relies on.
    """
    try:
        draft = ProgramDraft.model_validate(arguments)
    except ValidationError as error:
        return {
            "ok": False,
            "error": "Черновик не соответствует схеме программы",
            "details": _describe_validation_error(error),
        }

    sources, missing = _resolve_sources(draft)
    if missing:
        return {
            "ok": False,
            "error": "Неизвестные источники: " + ", ".join(missing),
            "details": "Используй только источники из результатов search_sources; выдуманные документы запрещены.",
        }
    payload = draft.model_copy(update={"sources": sources}).model_dump(mode="json")

    async with ctx.open_db() as db:
        row = await db.get(ChatProgram, ctx.session_id)
        if row is not None and row.status in (ProgramStatus.APPROVED, ProgramStatus.VERIFIED):
            return {
                "ok": False,
                "error": "Черновик уже утверждён методистом, изменить его нельзя.",
                "details": "Сообщи об этом и дождись указаний по следующему этапу.",
            }
        if row is None:
            db.add(ChatProgram(chat_session_id=ctx.session_id, status=ProgramStatus.PENDING_APPROVAL, draft=payload))
        else:
            row.status = ProgramStatus.PENDING_APPROVAL
            row.draft = payload
        await _name_session_after_draft(db, ctx, draft.title)
        await db.commit()

    log.info("program_draft_saved", chat_session_id=str(ctx.session_id), title=draft.title, sources=len(sources))
    return {
        "ok": True,
        "status": ProgramStatus.PENDING_APPROVAL,
        "note": "Черновик сохранён и ждёт подтверждения методиста. Представь его и остановись.",
    }


async def get_program_draft(ctx: ToolContext, arguments: dict) -> dict:
    """Return the stored draft and its state."""
    GetProgramDraftArguments.model_validate(arguments)
    async with ctx.open_db() as db:
        row = await db.get(ChatProgram, ctx.session_id)
    if row is None:
        return {
            "ok": True,
            "status": ProgramStatus.DRAFT,
            "draft": None,
            "note": "Черновик ещё не сохранён.",
        }
    return {"ok": True, "status": row.status, "draft": row.draft}


#: The functions the model may call, keyed by the name in the specification.
HANDLERS: dict[str, Callable[[ToolContext, dict], Awaitable[dict]]] = {
    SEARCH_SOURCES_SPEC.name: search_sources,
    SAVE_PROGRAM_DRAFT_SPEC.name: save_program_draft,
    GET_PROGRAM_DRAFT_SPEC.name: get_program_draft,
}


async def execute_tool(ctx: ToolContext, call: FunctionCall) -> dict:
    """Run one function call, turning any failure into a result for the model.

    A broken call is not a broken generation: the model gets the error and may
    correct itself in the next round, so every exception becomes a result rather
    than a raise.
    """
    handler = HANDLERS.get(call.name)
    if handler is None:
        log.warning("unknown_tool_call", tool=call.name)
        return {"ok": False, "error": f"Неизвестная функция: {call.name}"}
    try:
        result = await handler(ctx, call.arguments)
    except ValidationError as error:
        log.warning("tool_arguments_invalid", tool=call.name, errors=_describe_validation_error(error))
        return {"ok": False, "error": "Некорректные аргументы вызова", "details": _describe_validation_error(error)}
    except Exception as error:
        log.error("tool_call_failed", tool=call.name, exc=error)
        return {"ok": False, "error": "Внутренняя ошибка инструмента"}
    log.info("tool_call", tool=call.name, ok=bool(result.get("ok")))
    return result
