"""Tests for the agent tool handlers in ``app.agentic.tools``.

A handler takes its arguments model and answers a result model; the arguments are
validated by the dispatcher, so a call that does not match the stage's block never
reaches a handler. The handlers never write to the database: they move the run
state on the context, and the worker checkpoints it — which is why no database is
involved here.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from app.agentic import tools
from app.agentic.models import ProgramStatus, Source, SourceKind, Stage
from app.agentic.state import ProgramState, new_state
from app.agentic.tools import (
    ApproveProgramDraftResult,
    BeginProgramResult,
    SaveProgramDraftResult,
    ToolContext,
    call_executor,
    execute_tool,
    stage_tools,
)
from app.providers.models import FunctionCall, ToolCall

DRAFT_TITLE = "Тестовая программа"
STANDARD_ID = "src-01"
STANDARD_TITLE = "Профстандарт 06.042"


@pytest.fixture()
def context() -> ToolContext:
    """A run at its first stage, with nothing committed yet."""
    session_id = uuid.uuid4()
    return ToolContext(
        session_id=session_id,
        state=new_state(session_id, requested_hours=2, requirements="Курс для аналитиков"),
    )


def call(name: str, arguments: Any) -> FunctionCall:
    return FunctionCall(name=name, arguments=json.dumps(arguments, ensure_ascii=False))


def save(arguments: Any) -> FunctionCall:
    return call(tools.SAVE_PROGRAM_DRAFT, arguments)


def competences_block(**overrides: Any) -> dict:
    block: dict[str, Any] = {
        "kind": "general_development",
        "title": DRAFT_TITLE,
        "competencies": [
            {
                "id": "comp-01",
                "statement": "Способен применить знания",
                "knowledge": [{"id": "ro-k-01", "statement": "Знает основы"}],
            }
        ],
    }
    block.update(overrides)
    return block


def hours_block(**overrides: Any) -> dict:
    block: dict[str, Any] = {
        "curriculum": {
            "modules": [
                {
                    "id": "m-01",
                    "title": "Модуль",
                    "topics": [{"id": "t-01.1", "title": "Тема", "hours": {"lecture": 2}}],
                }
            ],
            "schedule": {"days": 1, "hours_per_day": 2},
        }
    }
    block.update(overrides)
    return block


def sources_block(*ids: str) -> dict:
    return {"sources": [{"id": source_id, "kind": "professional_standard", "title": "Источник"} for source_id in ids]}


def summary_block(*basis: str) -> dict:
    return {
        "summary": {
            "goal": "Научить основам работы с данными",
            "target_audience": "Взрослые слушатели",
            "form": "очно-заочная",
            "issued_document": "сертификат",
            "basis_source_ids": list(basis),
        }
    }


def at_stage(state: ProgramState, stage: Stage) -> ProgramState:
    """The state of a run that has completed everything before ``stage``."""
    while state.current_stage is not stage:
        state = state.completed(state.current_stage)
    return state


def catalog(monkeypatch: pytest.MonkeyPatch, kind: SourceKind = SourceKind.PROFESSIONAL_STANDARD) -> None:
    """Answer catalog lookups with one record, as the RAG index will."""

    def get_source(source_id: str) -> Source | None:
        if source_id != STANDARD_ID:
            return None
        return Source(id=source_id, kind=kind, title=STANDARD_TITLE)

    monkeypatch.setattr(tools, "get_source", get_source)


# --------------------------------------------------------------------------------------
# what is offered
# --------------------------------------------------------------------------------------


def test_the_intake_offers_starting_and_approving() -> None:
    assert [tool.function.name for tool in tools.INTAKE_TOOLS] == ["begin_program", "approve_program_draft"]


def test_a_citing_stage_offers_the_search_and_its_own_block() -> None:
    names = [tool.function.name for tool in stage_tools(Stage.SOURCES)]

    assert names == ["search_sources", tools.SAVE_PROGRAM_DRAFT]


def test_the_hours_stage_does_not_offer_the_search() -> None:
    assert [tool.function.name for tool in stage_tools(Stage.HOURS)] == [tools.SAVE_PROGRAM_DRAFT]


def test_the_save_specification_carries_only_the_stage_block() -> None:
    parameters = tools.STAGE_SPECS[Stage.HOURS].parameters

    assert parameters is not None
    assert set(parameters["properties"]) == {"curriculum"}
    assert "$defs" not in json.dumps(parameters)


def test_every_stage_has_a_block_and_a_specification() -> None:
    assert set(tools.STAGE_BLOCK) >= set(tools.STAGE_SPECS)
    assert all(spec.name == tools.SAVE_PROGRAM_DRAFT for spec in tools.STAGE_SPECS.values())


# --------------------------------------------------------------------------------------
# search
# --------------------------------------------------------------------------------------


async def test_search_reports_an_empty_catalog(context: ToolContext) -> None:
    """The catalog is empty until RAG (#11) lands, and the model is told so."""
    result = await execute_tool(context, call("search_sources", {"query": "образование"}))

    assert result.ok is True
    assert "ничего не найдено" in (result.note or "")
    assert context.known_source_ids == set()


async def test_search_remembers_the_documents_it_showed(context: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    async def search(query: str, *, kinds: Any = None, limit: int = 5):
        source = Source(id=STANDARD_ID, kind=SourceKind.PROFESSIONAL_STANDARD, title="П")
        return [tools.SourceFragment(fragment_id="frag-01", source=source, section="1", text="Текст")]

    monkeypatch.setattr(tools, "search", search)

    result = await execute_tool(context, call("search_sources", {"query": "аналитик"}))

    assert result.ok is True
    assert context.known_source_ids == {STANDARD_ID}


# --------------------------------------------------------------------------------------
# starting a run
# --------------------------------------------------------------------------------------


async def test_begin_program_starts_a_run_with_the_task_inside(context: ToolContext) -> None:
    fresh = ToolContext(session_id=context.session_id)

    result = await execute_tool(
        fresh, call("begin_program", {"requirements": "Курс по статистике", "requested_hours": 72})
    )

    assert isinstance(result, BeginProgramResult)
    assert result.ok is True
    assert fresh.dirty is True
    assert fresh.state is not None
    assert fresh.state.status is ProgramStatus.GENERATING
    assert fresh.state.current_stage is Stage.COMPETENCES
    assert fresh.state.requested_hours == 72
    assert fresh.state.requirements == "Курс по статистике"


async def test_begin_program_refuses_to_replace_a_live_run(context: ToolContext) -> None:
    result = await execute_tool(context, call("begin_program", {"requirements": "Другая программа"}))

    assert result.ok is False
    assert "уже есть программа" in (result.error or "")
    assert context.state is not None
    assert context.state.requirements == "Курс для аналитиков"


async def test_begin_program_restarts_a_failed_run(context: ToolContext) -> None:
    failed = context.state.failed_run("провайдер недоступен")
    context.state = failed

    result = await execute_tool(context, call("begin_program", {"requirements": "Ещё раз", "requested_hours": 16}))

    assert result.ok is True
    assert context.state is not None
    assert context.state.run_id != failed.run_id
    assert context.state.failed is False


async def test_begin_program_needs_a_task(context: ToolContext) -> None:
    result = await execute_tool(context, call("begin_program", {"requirements": ""}))

    assert result.ok is False
    assert "Некорректные аргументы" in (result.error or "")


# --------------------------------------------------------------------------------------
# saving a stage block
# --------------------------------------------------------------------------------------


async def test_saving_the_competences_keeps_the_block_on_the_state(context: ToolContext) -> None:
    result = await execute_tool(context, save(competences_block()))

    assert isinstance(result, SaveProgramDraftResult)
    assert result.ok is True
    assert context.dirty is True
    assert context.state is not None
    assert context.state.title == DRAFT_TITLE
    assert context.state.competencies[0].id == "comp-01"
    assert context.state.current_stage is Stage.COMPETENCES


async def test_a_later_save_replaces_an_earlier_block(context: ToolContext) -> None:
    await execute_tool(context, save(competences_block()))

    result = await execute_tool(context, save(competences_block(title="Переработанная программа")))

    assert result.ok is True
    assert context.state is not None
    assert context.state.title == "Переработанная программа"


async def test_a_block_that_breaks_the_schema_never_reaches_the_handler(context: ToolContext) -> None:
    broken = competences_block()
    broken["competencies"][0]["knowledge"] = []

    result = await execute_tool(context, save(broken))

    assert result.ok is False
    assert "Некорректные аргументы" in (result.error or "")
    assert context.state is not None
    assert context.state.competencies == []


async def test_negative_topic_hours_break_the_schema(context: ToolContext) -> None:
    context.state = at_stage(context.state, Stage.HOURS)
    broken = hours_block()
    broken["curriculum"]["modules"][0]["topics"][0]["hours"] = {"lecture": -2, "practice": -1}

    result = await execute_tool(context, save(broken))

    assert result.ok is False
    assert any("lecture" in line for line in (result.details or []))


async def test_a_zero_length_schedule_breaks_the_schema(context: ToolContext) -> None:
    context.state = at_stage(context.state, Stage.HOURS)
    broken = hours_block()
    broken["curriculum"]["schedule"] = {"days": 0, "hours_per_day": 0}

    result = await execute_tool(context, save(broken))

    assert result.ok is False
    assert any("days" in line for line in (result.details or []))
    assert any("hours_per_day" in line for line in (result.details or []))


async def test_a_schedule_with_weeks_only_is_accepted(context: ToolContext) -> None:
    context.state = at_stage(context.state, Stage.HOURS)
    block = hours_block()
    block["curriculum"]["schedule"] = {"weeks": 4}

    result = await execute_tool(context, save(block))

    assert result.ok is True


async def test_the_arguments_follow_the_stage_the_run_is_on(context: ToolContext) -> None:
    """The same tool takes the curriculum once the run reached the hours stage."""
    context.state = at_stage(context.state, Stage.HOURS)

    wrong = await execute_tool(context, save(competences_block()))
    right = await execute_tool(context, save(hours_block()))

    assert wrong.ok is False
    assert right.ok is True
    assert context.state is not None
    assert context.state.curriculum is not None


async def test_saving_without_a_programme_is_refused() -> None:
    context = ToolContext(session_id=uuid.uuid4())

    result = await execute_tool(context, save(competences_block()))

    assert result.ok is False
    assert "не начата" in (result.error or "")


# --------------------------------------------------------------------------------------
# citations
# --------------------------------------------------------------------------------------


async def test_a_competence_cannot_cite_a_document_the_run_has_not_seen(
    context: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog(monkeypatch)
    block = competences_block()
    block["competencies"][0]["standard_source_id"] = STANDARD_ID

    result = await execute_tool(context, save(block))

    assert result.ok is False
    assert "Не подтверждённые поиском источники" in (result.error or "")
    assert context.state is not None
    assert context.state.competencies == []


async def test_a_seen_standard_is_kept_with_the_catalog_record(
    context: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog(monkeypatch)
    context.known_source_ids.add(STANDARD_ID)
    block = competences_block()
    block["competencies"][0]["standard_source_id"] = STANDARD_ID

    result = await execute_tool(context, save(block))

    assert result.ok is True
    assert context.state is not None
    assert context.state.competencies[0].standard_source_id == STANDARD_ID
    assert [source.title for source in context.state.sources] == [STANDARD_TITLE]


async def test_a_standard_must_be_a_professional_standard(
    context: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog(monkeypatch, kind=SourceKind.NORMATIVE_ACT)
    context.known_source_ids.add(STANDARD_ID)
    block = competences_block()
    block["competencies"][0]["standard_source_id"] = STANDARD_ID

    result = await execute_tool(context, save(block))

    assert result.ok is False
    assert "Не профессиональные стандарты" in (result.error or "")


async def test_an_unknown_source_is_refused(context: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog(monkeypatch)
    context.state = at_stage(context.state, Stage.SOURCES)

    result = await execute_tool(context, save(sources_block("src-99")))

    assert result.ok is False
    assert "Неизвестные источники: src-99" in (result.error or "")


async def test_the_sources_stage_adds_to_what_earlier_stages_cited(
    context: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog(monkeypatch)
    context.known_source_ids.add(STANDARD_ID)
    standard = competences_block()
    standard["competencies"][0]["standard_source_id"] = STANDARD_ID
    await execute_tool(context, save(standard))
    context.state = at_stage(context.state, Stage.SOURCES)
    context.known_source_ids.add("src-07")
    monkeypatch.setattr(
        tools, "get_source", lambda source_id: Source(id=source_id, kind=SourceKind.LITERATURE, title=source_id)
    )

    result = await execute_tool(context, save(sources_block("src-07")))

    assert result.ok is True
    assert context.state is not None
    assert [source.id for source in context.state.sources] == [STANDARD_ID, "src-07"]


async def test_the_summary_may_only_cite_documents_the_run_already_has(
    context: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog(monkeypatch)
    context.state = at_stage(context.state, Stage.SUMMARY)

    result = await execute_tool(context, save(summary_block("src-04")))

    assert result.ok is False
    assert "Неизвестные источники: src-04" in (result.error or "")


async def test_the_summary_citation_joins_the_source_list(
    context: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog(monkeypatch)
    context.known_source_ids.add(STANDARD_ID)
    context.state = at_stage(context.state, Stage.SUMMARY)

    result = await execute_tool(context, save(summary_block(STANDARD_ID)))

    assert result.ok is True
    assert context.state is not None
    assert context.state.summary is not None
    assert [source.id for source in context.state.sources] == [STANDARD_ID]


# --------------------------------------------------------------------------------------
# approving
# --------------------------------------------------------------------------------------


async def test_approval_needs_a_rendered_draft(context: ToolContext) -> None:
    result = await execute_tool(context, call("approve_program_draft", {}))

    assert isinstance(result, ApproveProgramDraftResult)
    assert result.ok is False
    assert "не готов" in (result.error or "")


async def test_no_programme_means_nothing_to_approve() -> None:
    context = ToolContext(session_id=uuid.uuid4())

    result = await execute_tool(context, call("approve_program_draft", {}))

    assert result.ok is False
    assert "нет программы" in (result.error or "")


async def test_an_approved_run_is_not_approved_twice(context: ToolContext) -> None:
    context.state = context.state.mark_finalized().approved()

    result = await execute_tool(context, call("approve_program_draft", {}))

    assert result.ok is False
    assert "уже утверждена" in (result.error or "")


async def test_a_rendered_draft_can_be_approved(context: ToolContext) -> None:
    context.state = at_stage(context.state, Stage.SUMMARY).completed(Stage.SUMMARY).mark_finalized()

    result = await execute_tool(context, call("approve_program_draft", {}))

    assert result.ok is True
    assert context.dirty is True
    assert context.state is not None
    assert context.state.status is ProgramStatus.APPROVED


# --------------------------------------------------------------------------------------
# the dispatcher boundary
# --------------------------------------------------------------------------------------


async def test_an_unknown_tool_is_refused(context: ToolContext) -> None:
    result = await execute_tool(context, call("delete_everything", {}))

    assert result.ok is False
    assert "Неизвестная функция" in (result.error or "")


async def test_malformed_arguments_are_refused(context: ToolContext) -> None:
    result = await execute_tool(context, FunctionCall(name="begin_program", arguments="{"))

    assert result.ok is False
    assert "Некорректные аргументы" in (result.error or "")


async def test_a_tool_the_caller_did_not_offer_is_not_run(context: ToolContext) -> None:
    executor = call_executor(context, tools.INTAKE_TOOLS)
    tool_call = ToolCall(function=call(tools.SAVE_PROGRAM_DRAFT, competences_block()))

    result = await executor(tool_call)

    assert result.ok is False
    assert "недоступна" in (result.error or "")
    assert context.state is not None
    assert context.state.competencies == []


async def test_an_offered_tool_is_run(context: ToolContext) -> None:
    executor = call_executor(context, stage_tools(Stage.COMPETENCES))
    tool_call = ToolCall(function=call(tools.SAVE_PROGRAM_DRAFT, competences_block()))

    result = await executor(tool_call)

    assert result.ok is True
    assert context.state is not None
    assert context.state.title == DRAFT_TITLE


def at_hours(context: ToolContext) -> None:
    """Commit the competences and stand the run at the hours stage."""
    context.state = at_stage(context.state, Stage.HOURS)


async def test_the_hours_block_is_refused_when_it_misses_the_volume(context: ToolContext) -> None:
    """The model can fix the total in the same reply; finalising can only discard it."""
    await execute_tool(context, save(competences_block()))
    at_hours(context)
    block = hours_block()
    block["curriculum"]["modules"][0]["topics"][0]["hours"] = {"lecture": 1}

    result = await execute_tool(context, save(block))

    assert result.ok is False
    assert any("не совпадает" in line for line in (result.details or []))
    assert context.state is not None
    assert context.state.curriculum is None


async def test_the_hours_block_is_refused_when_the_schedule_cannot_hold_it(context: ToolContext) -> None:
    await execute_tool(context, save(competences_block()))
    at_hours(context)
    block = hours_block()
    block["curriculum"]["schedule"] = {"days": 1, "hours_per_day": 1}

    result = await execute_tool(context, save(block))

    assert result.ok is False
    assert any("меньше объёма" in line for line in (result.details or []))


async def test_a_topic_that_names_an_undeclared_outcome_is_refused(context: ToolContext) -> None:
    await execute_tool(context, save(competences_block()))
    at_hours(context)
    block = hours_block()
    block["curriculum"]["modules"][0]["topics"][0]["outcome_ids"] = ["ro-k-09"]

    result = await execute_tool(context, save(block))

    assert result.ok is False
    assert any("ro-k-09" in line for line in (result.details or []))
