"""Tests for the agent tool handlers in ``app.agentic.tools``.

A handler takes its arguments model and answers a result model; the arguments are
validated by the dispatcher, so a call that does not match the schema never
reaches a handler. Storing the draft is the worker's business, so no database is
involved here, and neither is the provider — the handlers are called through
:func:`execute_tool`, which is the boundary a ``FunctionCall`` arrives at.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from structlog.testing import capture_logs

from app.agentic import tools
from app.agentic.models import ProgramStatus, Source, SourceKind
from app.agentic.tools import (
    GetProgramDraftResult,
    SaveProgramDraftResult,
    SearchSourcesResult,
    ToolContext,
    execute_tool,
)
from app.providers.models import FunctionCall

DRAFT_TITLE = "Тестовая программа"
CATALOG_SOURCE_ID = "src-01"
CATALOG_TITLE = "Профстандарт 06.042"


@pytest.fixture()
def tool_context() -> ToolContext:
    """A context bound to the chat the run belongs to."""
    return ToolContext(session_id=uuid.uuid4())


def save_call(arguments: dict | None = None) -> FunctionCall:
    payload = arguments if arguments is not None else make_draft()
    return FunctionCall(name="save_program_draft", arguments=json.dumps(payload, ensure_ascii=False))


def search_call(query: str) -> FunctionCall:
    return FunctionCall(name="search_sources", arguments=json.dumps({"query": query}, ensure_ascii=False))


def get_call() -> FunctionCall:
    return FunctionCall(name="get_program_draft", arguments="{}")


def make_draft(**overrides: Any) -> dict:
    """A minimal schema-valid draft the tests can bend."""
    draft = {
        "kind": "general_development",
        "title": DRAFT_TITLE,
        "summary": {
            "goal": "Научить основам работы с данными",
            "target_audience": "Взрослые слушатели",
            "form": "очно-заочная",
            "issued_document": "сертификат",
        },
        "competencies": [
            {
                "id": "comp-01",
                "statement": "Способен применить знания",
                "knowledge": [{"id": "ro-k-01", "statement": "Знает основы"}],
            }
        ],
        "curriculum": {
            "modules": [
                {
                    "id": "m-01",
                    "title": "Модуль",
                    "topics": [{"id": "t-01.1", "title": "Тема", "hours": {"lecture": 2}}],
                }
            ],
            "schedule": {"days": 1, "hours_per_day": 2},
        },
        "sources": [],
    }
    draft.update(overrides)
    return draft


def _patch_catalog(monkeypatch: pytest.MonkeyPatch, kind: SourceKind, title: str) -> None:
    """Answer the catalog lookup the way the RAG index (#11) will, with one record."""
    monkeypatch.setattr(tools, "get_source", lambda source_id: Source(id=source_id, kind=kind, title=title))


async def test_save_program_draft_validates_the_draft_and_keeps_it(tool_context):
    result = await execute_tool(tool_context, save_call())

    assert isinstance(result, SaveProgramDraftResult)
    assert result.ok is True
    assert result.status is ProgramStatus.PENDING_APPROVAL
    assert tool_context.draft is not None
    assert tool_context.draft.title == DRAFT_TITLE


async def test_a_second_save_replaces_the_kept_draft(tool_context):
    await execute_tool(tool_context, save_call())

    result = await execute_tool(tool_context, save_call(make_draft(title="Переработанная программа")))

    assert result.ok is True
    assert tool_context.draft is not None
    assert tool_context.draft.title == "Переработанная программа"


async def test_a_draft_that_breaks_the_schema_never_reaches_the_handler(tool_context):
    """The draft schema is the arguments model of the tool, so the dispatcher refuses it."""
    result = await execute_tool(tool_context, save_call(make_draft(summary={})))

    assert result.ok is False
    assert "Некорректные аргументы" in result.error
    assert any("summary" in line for line in result.details)
    assert tool_context.draft is None


async def test_negative_topic_hours_break_the_schema(tool_context):
    """An allocation cannot be negative, so the schema refuses the whole draft."""
    draft = make_draft()
    draft["curriculum"]["modules"][0]["topics"][0]["hours"] = {"lecture": -2, "practice": -1}

    result = await execute_tool(tool_context, save_call(draft))

    assert result.ok is False
    assert "Некорректные аргументы" in result.error
    assert any("lecture" in line for line in result.details)
    assert tool_context.draft is None


async def test_a_zero_length_schedule_breaks_the_schema(tool_context):
    """A duration or a daily load of zero is not a schedule the methodist asked for."""
    draft = make_draft()
    draft["curriculum"]["schedule"] = {"days": 0, "hours_per_day": 0}

    result = await execute_tool(tool_context, save_call(draft))

    assert result.ok is False
    assert any("days" in line for line in result.details)
    assert any("hours_per_day" in line for line in result.details)


async def test_a_schedule_with_weeks_only_is_accepted(tool_context):
    """Only one of weeks, days and hours_per_day has to be filled."""
    draft = make_draft()
    draft["curriculum"]["schedule"] = {"weeks": 4}

    result = await execute_tool(tool_context, save_call(draft))

    assert result.ok is True


async def test_get_program_draft_reports_no_draft_before_the_first_save(tool_context):
    result = await execute_tool(tool_context, get_call())

    assert isinstance(result, GetProgramDraftResult)
    assert result.ok is True
    assert result.status is ProgramStatus.DRAFT
    assert result.draft is None


async def test_get_program_draft_returns_the_draft_the_run_saved(tool_context):
    await execute_tool(tool_context, save_call())

    result = await execute_tool(tool_context, get_call())

    assert result.status is ProgramStatus.PENDING_APPROVAL
    assert result.draft is not None
    assert result.draft.title == DRAFT_TITLE


async def test_editing_the_draft_a_call_returned_leaves_the_saved_one_alone(tool_context):
    """A frozen model still holds mutable lists, so the answer is a deep copy."""
    await execute_tool(tool_context, save_call())

    result = await execute_tool(tool_context, get_call())

    assert isinstance(result, GetProgramDraftResult)
    assert result.draft is not None
    assert result.draft is not tool_context.draft
    result.draft.summary.basis_source_ids.append("src-99")

    assert tool_context.draft is not None
    assert tool_context.draft.summary.basis_source_ids == []


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


async def test_save_rejects_a_source_id_the_catalog_does_not_know(tool_context):
    draft = make_draft(sources=[{"id": "src-01", "kind": "normative_act", "title": "ФГОС"}])

    result = await execute_tool(tool_context, save_call(draft))

    assert result.ok is False
    assert "Неизвестные источники" in result.error
    assert "src-01" in result.error
    assert tool_context.draft is None


async def test_save_rejects_a_standard_source_id_the_catalog_does_not_know(tool_context):
    draft = make_draft()
    draft["competencies"][0]["standard_source_id"] = "src-01"

    result = await execute_tool(tool_context, save_call(draft))

    assert result.ok is False
    assert "Неизвестные источники" in result.error
    assert "src-01" in result.error


async def test_save_rejects_a_standard_that_is_not_a_professional_standard(tool_context, monkeypatch):
    """The field says where the competence comes from, so the kind of the record must match it."""
    _patch_catalog(monkeypatch, SourceKind.NORMATIVE_ACT, "ФГОС")
    tool_context.known_source_ids.add(CATALOG_SOURCE_ID)
    draft = make_draft()
    draft["competencies"][0]["standard_source_id"] = CATALOG_SOURCE_ID

    result = await execute_tool(tool_context, save_call(draft))

    assert result.ok is False
    assert "Не профессиональные стандарты" in result.error
    assert CATALOG_SOURCE_ID in result.error
    assert tool_context.draft is None


async def test_save_keeps_the_catalog_record_of_a_standard(tool_context, monkeypatch):
    """The record the catalog holds wins, so an invented title cannot reach the draft."""
    _patch_catalog(monkeypatch, SourceKind.PROFESSIONAL_STANDARD, CATALOG_TITLE)
    tool_context.known_source_ids.add(CATALOG_SOURCE_ID)
    draft = make_draft(
        sources=[{"id": CATALOG_SOURCE_ID, "kind": "professional_standard", "title": "Выдуманное название"}]
    )
    draft["competencies"][0]["standard_source_id"] = CATALOG_SOURCE_ID

    result = await execute_tool(tool_context, save_call(draft))

    assert result.ok is True
    assert tool_context.draft is not None
    assert [source.title for source in tool_context.draft.sources] == [CATALOG_TITLE]


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
        result = await execute_tool(tool_context, save_call())

    assert result.ok is True
    saved = next(entry for entry in logs if entry["event"] == "program_draft_validated")
    assert DRAFT_TITLE not in str(saved)
