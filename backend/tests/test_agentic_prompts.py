"""Tests for the stage prompts in ``app.agentic.prompts``.

A stage prompt is the only thing the model knows about the run, and it is rebuilt
from the state every time. These tests pin what it must carry — the methodist's
task, the volume, the committed blocks — and what it must not: the bookkeeping of
the envelope is not the model's business.
"""

from __future__ import annotations

import uuid

from app.agentic.models import ProgramKind, ProgramSummary, Source, SourceKind, Stage
from app.agentic.prompts import STAGE_TASKS, saved_blocks, stage_brief, stage_messages
from app.agentic.state import CompetencesBlock, ProgramState, SourcesBlock, SummaryBlock, new_state

COMPETENCE = {
    "id": "comp-01",
    "statement": "Способен анализировать данные",
    "knowledge": [{"id": "ro-k-01", "statement": "Знает основы статистики"}],
}


def run_at(stage: Stage) -> ProgramState:
    """A run that committed the competences and stands at ``stage``."""
    state = new_state(uuid.uuid4(), requested_hours=72, requirements="Курс по анализу данных")
    block = CompetencesBlock(
        kind=ProgramKind.GENERAL_DEVELOPMENT,
        title="Анализ данных",
        competencies=[COMPETENCE],
    )
    state = state.with_competences(block, [])
    while state.current_stage is not stage:
        state = state.completed(state.current_stage)
    return state


def test_a_stage_is_a_system_rule_and_a_brief() -> None:
    messages = stage_messages(run_at(Stage.COMPETENCES))

    assert [message.role for message in messages] == ["system", "user"]
    assert "save_program_draft" in messages[0].content[0].text


def test_the_brief_carries_the_task_and_the_volume() -> None:
    brief = stage_brief(run_at(Stage.HOURS))

    assert "Курс по анализу данных" in brief
    assert "72" in brief


def test_the_brief_carries_the_blocks_the_earlier_stages_committed() -> None:
    brief = stage_brief(run_at(Stage.HOURS))

    assert "Анализ данных" in brief
    assert "Способен анализировать данные" in brief
    assert "ro-k-01" in brief


def test_the_brief_carries_the_methodists_change() -> None:
    """The stage that is redone is told what the methodist wants changed."""
    state = run_at(Stage.HOURS).request_revision(Stage.HOURS, "замени тему 2.3")

    brief = stage_brief(state)

    assert "замени тему 2.3" in brief
    assert "согласованность" in brief


def test_the_brief_names_the_job_of_the_stage() -> None:
    for stage, task in STAGE_TASKS.items():
        assert task in stage_brief(run_at(stage))


def test_the_brief_keeps_the_run_bookkeeping_out() -> None:
    state = run_at(Stage.HOURS)

    brief = stage_brief(state)

    assert "attempts" not in brief
    assert str(state.run_id) not in brief
    assert str(state.session_id) not in brief


def test_a_stage_only_sees_the_blocks_that_exist() -> None:
    blocks = saved_blocks(run_at(Stage.HOURS))

    assert set(blocks) == {"вид программы", "название", "компетенции"}


def test_an_empty_run_says_that_nothing_is_saved() -> None:
    state = new_state(uuid.uuid4(), requested_hours=None, requirements=None)

    brief = stage_brief(state)

    assert "пока ничего" in brief
    assert "не задано" in brief


def test_an_empty_catalog_tells_the_model_to_leave_citations_empty() -> None:
    """The catalog is empty until #11, so an invented citation would dead-end the stage."""
    system = stage_messages(run_at(Stage.COMPETENCES))[0].content[0].text
    assert "оставляй ссылки пустыми" in system

    assert "оставь standard_source_id пустым" in stage_brief(run_at(Stage.COMPETENCES))
    assert "оставь список пустым" in stage_brief(run_at(Stage.SUMMARY))


def test_the_brief_carries_the_sources_and_the_passport() -> None:
    """The two blocks the later stages commit are visible to the model."""
    state = run_at(Stage.SUMMARY)
    source = Source(id="src-01", kind=SourceKind.PROFESSIONAL_STANDARD, title="Профстандарт 06.042")
    summary = ProgramSummary(
        goal="Научить анализу данных",
        target_audience="Аналитики",
        form="очно-заочная",
        issued_document="удостоверение",
    )
    state = state.with_sources(SourcesBlock(sources=[source]))
    state = state.with_summary(SummaryBlock(summary=summary), [source])

    blocks = saved_blocks(state)

    assert blocks["источники"] == [source.model_dump(mode="json")]
    assert blocks["паспорт"]["goal"] == "Научить анализу данных"
