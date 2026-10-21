"""Tests for the draft renderer in ``app.agentic.render``.

The rendered message is what the methodist approves, so the tests read it the way
they would read the document: the sections are there, the hours add up, a source
is cited by title rather than by id, and the message ends with the phrase the chat
worker matches as a confirmation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.agentic.models import ProgramDraft, Schedule
from app.agentic.render import CONFIRMATION_PROMPT, render_draft

EXAMPLE_PATH = Path(__file__).resolve().parents[1] / "examples" / "document_state_example.json"

Program = dict[str, Any]


def build(raw: Program) -> ProgramDraft:
    """The draft part of the example document."""
    return ProgramDraft(
        kind=raw["meta"]["kind"],
        title=raw["meta"]["title"],
        summary=raw["summary"],
        competencies=raw["competencies"],
        curriculum=raw["curriculum"],
        sources=raw["sources"],
    )


@pytest.fixture()
def draft() -> ProgramDraft:
    return build(json.loads(EXAMPLE_PATH.read_text(encoding="utf-8")))


def test_the_programme_passport_is_rendered(draft: ProgramDraft) -> None:
    text = render_draft(draft)

    assert text.startswith("# Анализ данных на Python")
    assert "повышение квалификации" in text
    assert draft.summary.goal in text
    assert draft.summary.target_audience in text


def test_every_competence_is_rendered_with_its_outcomes(draft: ProgramDraft) -> None:
    text = render_draft(draft)

    assert "### Компетенция 1" in text
    assert draft.competencies[0].statement in text
    assert draft.competencies[0].knowledge[0].statement in text
    assert draft.competencies[0].knowledge[0].id in text


def test_the_curriculum_is_rendered_with_its_hour_totals(draft: ProgramDraft) -> None:
    text = render_draft(draft)

    assert draft.curriculum.modules[0].title in text
    assert "**Итого:** 66 ч" in text
    assert "аудиторных 38 ч" in text
    assert "самостоятельной работы 28 ч" in text


def test_the_schedule_is_rendered_as_one_sentence(draft: ProgramDraft) -> None:
    text = render_draft(draft)

    assert "**График:** 4 нед., 18 дн., 4 ч в день;" in text
    assert draft.curriculum.schedule.note.rstrip(".") in text


def test_a_source_is_rendered_by_title_not_by_id(draft: ProgramDraft) -> None:
    text = render_draft(draft)

    for source in draft.sources:
        assert source.title in text


def test_the_documents_the_programme_is_based_on_are_named(draft: ProgramDraft) -> None:
    text = render_draft(draft)

    assert "**Разработана на основе:**" in text
    for source_id in draft.summary.basis_source_ids:
        source = next(source for source in draft.sources if source.id == source_id)
        assert source.title in text


def test_a_draft_without_sources_says_so(draft: ProgramDraft) -> None:
    empty = draft.model_copy(
        update={"sources": [], "summary": draft.summary.model_copy(update={"basis_source_ids": []})}
    )

    text = render_draft(empty)

    assert "Источники не подобраны." in text


def test_the_message_ends_with_the_confirmation_request(draft: ProgramDraft) -> None:
    assert render_draft(draft).rstrip().endswith(CONFIRMATION_PROMPT)


def test_a_topic_without_an_hour_split_renders_without_empty_parentheses() -> None:
    raw = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
    topic = raw["curriculum"]["modules"][0]["topics"][0]
    topic["hours"] = {"lecture": 0, "practice": 0, "lab": 0, "self_study": 0}

    text = render_draft(build(raw))

    assert f"{topic['title']} ({topic['id']}) — 0 ч" in text
    assert "()" not in text


def test_a_schedule_with_only_weeks_renders_what_it_has(draft: ProgramDraft) -> None:
    curriculum = draft.curriculum.model_copy(update={"schedule": Schedule(weeks=4)})

    text = render_draft(draft.model_copy(update={"curriculum": curriculum}))

    assert "**График:** 4 нед." in text


def test_a_passport_without_tasks_hides_the_task_list(draft: ProgramDraft) -> None:
    summary = draft.summary.model_copy(update={"tasks": []})

    text = render_draft(draft.model_copy(update={"summary": summary}))

    assert "**Задачи:**" not in text


def test_basis_ids_that_do_not_match_a_source_are_dropped(draft: ProgramDraft) -> None:
    summary = draft.summary.model_copy(update={"basis_source_ids": ["src-99"]})

    text = render_draft(draft.model_copy(update={"summary": summary}))

    assert "Разработана на основе" not in text
