"""Tests for the agent tool handlers in ``app.agentic.tools``.

A handler takes its arguments model and answers a result model; the arguments are
validated by the dispatcher, so a call that does not match the schema never
reaches a handler. The handlers mutate a :class:`ToolContext` and leave storage to
the worker, so no database is involved here and neither is the provider — the
handlers are called through :func:`execute_tool`, which is the boundary a
``FunctionCall`` arrives at.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from pydantic import ValidationError
from structlog.testing import capture_logs

from app.agentic import tools
from app.agentic.models import ProgramStatus, Source, SourceKind, Stage, StageStatus
from app.agentic.tools import (
    BeginProgramResult,
    GetProgramDraftResult,
    SaveProgramDraftResult,
    SearchSourcesResult,
    ToolContext,
    execute_tool,
)
from app.providers.models import FunctionCall

DRAFT_TITLE = "Тестовая программа"
REQUESTED_HOURS = 2
CATALOG_SOURCE_ID = "src-01"
CATALOG_TITLE = "Профстандарт 06.042"


@pytest.fixture()
def tool_context() -> ToolContext:
    """A context bound to the chat the run belongs to."""
    return ToolContext(session_id=uuid.uuid4())


def call(name: str, arguments: dict | None = None) -> FunctionCall:
    payload = arguments if arguments is not None else {}
    return FunctionCall(name=name, arguments=json.dumps(payload, ensure_ascii=False))


def begin_call() -> FunctionCall:
    return call(
        "begin_program",
        {
            "requested_hours": REQUESTED_HOURS,
            "requirements": "Тема: анализ данных. Аудитория: взрослые слушатели.",
            "title": DRAFT_TITLE,
        },
    )


def approve_call() -> FunctionCall:
    return call("approve_program_draft")


def save_call(stage: Stage, payload: dict) -> FunctionCall:
    return call("save_program_draft", {"stage": stage.value, "payload": payload})


def search_call(query: str) -> FunctionCall:
    return call("search_sources", {"query": query})


def get_call() -> FunctionCall:
    return call("get_program_draft")


def competences_payload(**overrides: Any) -> dict:
    payload = {
        "competencies": [
            {
                "id": "comp-01",
                "statement": "Способен применять базовые методы анализа данных",
                "knowledge": [{"id": "ro-k-01", "statement": "Знает основы анализа данных"}],
                "skills": [{"id": "ro-s-01", "statement": "Умеет выбрать метод анализа"}],
            }
        ]
    }
    payload.update(overrides)
    return payload


def hours_payload(**overrides: Any) -> dict:
    payload = {
        "curriculum": {
            "modules": [
                {
                    "id": "m-01",
                    "title": "Модуль 1",
                    "topics": [
                        {
                            "id": "t-01.1",
                            "title": "Тема 1",
                            "hours": {"lecture": 1, "practice": 1},
                        }
                    ],
                }
            ],
            "schedule": {"days": 1, "hours_per_day": REQUESTED_HOURS},
        }
    }
    payload.update(overrides)
    return payload


def summary_payload(**overrides: Any) -> dict:
    payload = {
        "kind": "general_development",
        "title": DRAFT_TITLE,
        "summary": {
            "goal": "Научить основам работы с данными",
            "target_audience": "Взрослые слушатели",
            "form": "очно-заочная",
            "issued_document": "сертификат",
        },
    }
    payload.update(overrides)
    return payload


async def start(ctx: ToolContext) -> None:
    result = await execute_tool(ctx, begin_call())
    assert isinstance(result, BeginProgramResult)
    assert result.ok is True
    ctx.stage = ctx.state.current_stage


async def save_stage(ctx: ToolContext, stage: Stage, payload: dict) -> SaveProgramDraftResult:
    result = await execute_tool(ctx, save_call(stage, payload))
    assert isinstance(result, SaveProgramDraftResult)
    assert result.ok is True, result
    ctx.stage = ctx.state.current_stage
    return result


async def drive_to_sources(ctx: ToolContext) -> None:
    await start(ctx)
    await save_stage(ctx, Stage.COMPETENCES, competences_payload())
    await save_stage(ctx, Stage.HOURS, hours_payload())


async def drive_to_approval(ctx: ToolContext) -> None:
    await drive_to_sources(ctx)
    await save_stage(ctx, Stage.SOURCES, {"sources": []})
    await save_stage(ctx, Stage.SUMMARY, summary_payload())


def _patch_catalog(monkeypatch: pytest.MonkeyPatch, kind: SourceKind, title: str) -> None:
    """Answer the catalog lookup the way the RAG index (#11) will, with one record."""
    source = Source(id=CATALOG_SOURCE_ID, kind=kind, title=title)
    monkeypatch.setattr(tools, "get_source", lambda source_id: source if source_id == CATALOG_SOURCE_ID else None)


# --------------------------------------------------------------------------------------
# begin: the intake creates the envelope and stops at the first stage
# --------------------------------------------------------------------------------------


async def test_begin_program_creates_the_first_stage(tool_context):
    result = await execute_tool(tool_context, begin_call())

    assert isinstance(result, BeginProgramResult)
    assert result.ok is True
    assert result.status is ProgramStatus.GENERATING
    assert result.current_stage is Stage.COMPETENCES
    assert tool_context.state is not None
    assert tool_context.state.current_stage is Stage.COMPETENCES
    assert tool_context.state.stage_status(Stage.COMPETENCES) is StageStatus.IN_PROGRESS
    assert tool_context.started is True
    assert tool_context.dirty is True


async def test_begin_program_refuses_to_start_twice(tool_context):
    await start(tool_context)
    tool_context.stage = None

    result = await execute_tool(tool_context, begin_call())

    assert result.ok is False
    assert "уже создана" in result.error


async def test_begin_program_is_refused_inside_a_stage(tool_context):
    tool_context.stage = Stage.COMPETENCES

    result = await execute_tool(tool_context, begin_call())

    assert result.ok is False
    assert "обычного диалога" in result.error


# --------------------------------------------------------------------------------------
# save: each stage writes exactly its own block and advances the pipeline
# --------------------------------------------------------------------------------------


async def test_save_competences_advances_to_hours(tool_context):
    await start(tool_context)

    result = await save_stage(tool_context, Stage.COMPETENCES, competences_payload())

    assert result.status is ProgramStatus.GENERATING
    assert result.current_stage is Stage.HOURS
    assert tool_context.state is not None
    assert len(tool_context.state.competencies) == 1
    assert tool_context.state.stage_status(Stage.COMPETENCES) is StageStatus.COMPLETED


async def test_save_refuses_a_block_for_another_stage(tool_context):
    await start(tool_context)

    result = await execute_tool(tool_context, save_call(Stage.HOURS, hours_payload()))

    assert result.ok is False
    assert "Сейчас выполняется этап competences" in result.error


async def test_save_is_idempotent_after_the_stage_completed(tool_context):
    await start(tool_context)
    await save_stage(tool_context, Stage.COMPETENCES, competences_payload())

    tool_context.stage = Stage.COMPETENCES
    result = await execute_tool(tool_context, save_call(Stage.COMPETENCES, competences_payload()))

    assert result.ok is True
    assert "уже сохранён" in result.note


async def test_a_block_that_breaks_the_schema_is_refused(tool_context):
    await start(tool_context)

    result = await execute_tool(tool_context, save_call(Stage.COMPETENCES, summary_payload()))

    assert result.ok is False
    assert "не соответствует схеме" in result.error
    assert any("competencies" in line for line in result.details)
    assert tool_context.state is not None
    assert tool_context.state.competencies == []


async def test_hours_that_do_not_match_the_requested_volume_fail_the_draft_validator(tool_context):
    await start(tool_context)
    await save_stage(tool_context, Stage.COMPETENCES, competences_payload())
    payload = hours_payload()
    payload["curriculum"]["schedule"] = {"days": 1, "hours_per_day": 10}
    await save_stage(tool_context, Stage.HOURS, payload)
    await save_stage(tool_context, Stage.SOURCES, {"sources": []})
    await save_stage(tool_context, Stage.SUMMARY, summary_payload())

    assert tool_context.state is not None
    with pytest.raises(ValidationError, match="does not fit the requested volume"):
        tool_context.state.to_draft()


async def test_the_full_pipeline_reaches_pending_approval(tool_context):
    await drive_to_approval(tool_context)

    assert tool_context.state is not None
    assert tool_context.state.status is ProgramStatus.PENDING_APPROVAL
    assert tool_context.state.current_stage is None
    assert tool_context.state.to_draft().title == DRAFT_TITLE


# --------------------------------------------------------------------------------------
# get: a complete state exposes the draft, a partial one does not
# --------------------------------------------------------------------------------------


async def test_get_program_draft_reports_no_programme_before_begin(tool_context):
    result = await execute_tool(tool_context, get_call())

    assert isinstance(result, GetProgramDraftResult)
    assert result.ok is True
    assert result.status is ProgramStatus.DRAFT
    assert result.draft is None


async def test_get_program_draft_returns_none_while_the_draft_is_partial(tool_context):
    await start(tool_context)

    result = await execute_tool(tool_context, get_call())

    assert result.status is ProgramStatus.GENERATING
    assert result.state is not None
    assert result.draft is None


async def test_get_program_draft_returns_the_complete_draft(tool_context):
    await drive_to_approval(tool_context)

    result = await execute_tool(tool_context, get_call())

    assert result.status is ProgramStatus.PENDING_APPROVAL
    assert result.draft is not None
    assert result.draft.title == DRAFT_TITLE


async def test_editing_the_draft_a_call_returned_leaves_the_state_alone(tool_context):
    await drive_to_approval(tool_context)

    result = await execute_tool(tool_context, get_call())

    assert isinstance(result, GetProgramDraftResult)
    assert result.draft is not None
    result.draft.summary.basis_source_ids.append("src-99")
    assert tool_context.state is not None
    assert tool_context.state.to_draft().summary.basis_source_ids == []


# --------------------------------------------------------------------------------------
# approval: only a complete draft, and only after an explicit confirmation
# --------------------------------------------------------------------------------------


async def test_approval_is_refused_before_the_draft_is_complete(tool_context):
    await start(tool_context)

    result = await execute_tool(tool_context, approve_call())

    assert result.ok is False
    assert "ещё не готов" in result.error


async def test_approval_freezes_the_complete_draft(tool_context):
    await drive_to_approval(tool_context)

    result = await execute_tool(tool_context, approve_call())

    assert result.ok is True
    assert result.status is ProgramStatus.APPROVED
    assert tool_context.approved is True
    assert tool_context.state is not None
    assert tool_context.state.status is ProgramStatus.APPROVED
    assert tool_context.state.approved_at is not None


async def test_a_second_approval_is_idempotent(tool_context):
    await drive_to_approval(tool_context)
    await execute_tool(tool_context, approve_call())

    result = await execute_tool(tool_context, approve_call())

    assert result.ok is True
    assert result.status is ProgramStatus.APPROVED
    assert "уже утверждён" in result.note


# --------------------------------------------------------------------------------------
# sources: citations are validated against the catalog and the run's provenance
# --------------------------------------------------------------------------------------


async def test_search_sources_reports_an_empty_catalog(tool_context):
    """Materials and search belong to the RAG zone (#11); nothing is found until it lands."""
    result = await execute_tool(tool_context, search_call("образование"))

    assert isinstance(result, SearchSourcesResult)
    assert result.ok is True
    assert result.fragments == []
    assert "ничего не найдено" in result.note
    assert tool_context.known_source_ids == set()


async def test_search_sources_remembers_what_it_returned(tool_context, monkeypatch):
    source = Source(id=CATALOG_SOURCE_ID, kind=SourceKind.NORMATIVE_ACT, title=CATALOG_TITLE)

    async def fake_search(query, *, kinds=None, limit=5):
        return [tools.SourceFragment(fragment_id="frag-1", source=source, section="1", text="фрагмент")]

    monkeypatch.setattr(tools, "search", fake_search)

    result = await execute_tool(tool_context, search_call("профстандарт"))

    assert result.ok is True
    assert result.fragments
    assert tool_context.known_source_ids == {CATALOG_SOURCE_ID}


async def test_save_rejects_a_source_id_the_catalog_does_not_know(tool_context):
    await drive_to_sources(tool_context)

    result = await execute_tool(
        tool_context,
        save_call(Stage.SOURCES, {"sources": [{"id": "src-01", "kind": "normative_act", "title": "ФГОС"}]}),
    )

    assert result.ok is False
    assert "Неизвестные источники" in result.error
    assert "src-01" in result.error


async def test_save_rejects_a_known_source_the_run_never_searched(tool_context, monkeypatch):
    _patch_catalog(monkeypatch, SourceKind.NORMATIVE_ACT, "ФГОС")
    await drive_to_sources(tool_context)

    result = await execute_tool(
        tool_context,
        save_call(Stage.SOURCES, {"sources": [{"id": CATALOG_SOURCE_ID, "kind": "normative_act", "title": "ФГОС"}]}),
    )

    assert result.ok is False
    assert "Не подтверждённые поиском" in result.error


async def test_save_keeps_the_catalog_record_of_a_source(tool_context, monkeypatch):
    _patch_catalog(monkeypatch, SourceKind.NORMATIVE_ACT, "Настоящий ФГОС")
    await drive_to_sources(tool_context)
    tool_context.known_source_ids.add(CATALOG_SOURCE_ID)

    await save_stage(
        tool_context,
        Stage.SOURCES,
        {"sources": [{"id": CATALOG_SOURCE_ID, "kind": "normative_act", "title": "Выдуманное название"}]},
    )

    assert tool_context.state is not None
    assert [source.title for source in tool_context.state.sources] == ["Настоящий ФГОС"]


async def test_save_rejects_a_standard_that_is_not_a_professional_standard(tool_context, monkeypatch):
    """The field says where the competence comes from, so the kind of the record must match it."""
    _patch_catalog(monkeypatch, SourceKind.NORMATIVE_ACT, "ФГОС")
    await start(tool_context)
    tool_context.known_source_ids.add(CATALOG_SOURCE_ID)
    payload = competences_payload()
    payload["competencies"][0]["standard_source_id"] = CATALOG_SOURCE_ID

    result = await execute_tool(tool_context, save_call(Stage.COMPETENCES, payload))

    assert result.ok is False
    assert "Не профессиональные стандарты" in result.error
    assert CATALOG_SOURCE_ID in result.error


async def test_save_accepts_a_professional_standard_as_the_basis(tool_context, monkeypatch):
    _patch_catalog(monkeypatch, SourceKind.PROFESSIONAL_STANDARD, CATALOG_TITLE)
    await start(tool_context)
    tool_context.known_source_ids.add(CATALOG_SOURCE_ID)
    payload = competences_payload()
    payload["competencies"][0]["standard_source_id"] = CATALOG_SOURCE_ID

    result = await execute_tool(tool_context, save_call(Stage.COMPETENCES, payload))

    assert result.ok is True


# --------------------------------------------------------------------------------------
# execute_tool: a broken call is an answer, not an exception
# --------------------------------------------------------------------------------------


async def test_execute_tool_reports_an_unknown_function(tool_context):
    result = await execute_tool(tool_context, FunctionCall(name="drop_database", arguments="{}"))

    assert result.ok is False
    assert "Неизвестная функция" in result.error


async def test_execute_tool_reports_malformed_arguments(tool_context):
    result = await execute_tool(tool_context, FunctionCall(name="search_sources", arguments="не json"))

    assert result.ok is False
    assert "Некорректные аргументы" in result.error


async def test_execute_tool_reports_arguments_that_break_the_schema(tool_context):
    result = await execute_tool(tool_context, search_call(""))

    assert result.ok is False
    assert "Некорректные аргументы" in result.error
    assert any("query" in line for line in result.details)


async def test_the_draft_title_is_not_written_to_the_logs(tool_context):
    """The title is user content; a trace needs the session and the source count."""
    with capture_logs() as logs:
        await start(tool_context)
        await save_stage(tool_context, Stage.COMPETENCES, competences_payload())

    assert DRAFT_TITLE not in str(logs)
