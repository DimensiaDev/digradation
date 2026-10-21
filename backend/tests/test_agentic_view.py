"""Tests for the programme's view in ``app.agentic.view``.

The rules the view applies are the programme's: a run that died reads as
failed, the document is the blocks that are already saved, and the questions
are the ones nobody answered yet.
"""

from __future__ import annotations

import uuid

from app.agentic.models import ProgramStatus, Stage
from app.agentic.state import ClarificationAnswer, ClarificationQuestion, new_state
from app.agentic.view import programme_blocks, programme_questions, programme_state, programme_status
from app.schemas.chat import ProgramViewStatus

SESSION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")


def a_run(**changes):
    """A run in the state the test needs."""
    state = new_state(SESSION_ID, requested_hours=72, requirements="Программа по аналитике")
    return state.model_copy(update=changes) if changes else state


def test_a_live_run_reports_its_own_status() -> None:
    assert programme_status(a_run()) is ProgramViewStatus.GENERATING
    assert programme_status(a_run(status=ProgramStatus.PENDING_APPROVAL)) is ProgramViewStatus.PENDING_APPROVAL


def test_a_run_that_died_reads_as_failed() -> None:
    """A run keeps the status it had when it died, so the failure is folded in."""
    state = a_run(failed=True, error="этап не завершился")

    assert programme_status(state) is ProgramViewStatus.FAILED


def test_a_chat_without_a_run_has_no_status() -> None:
    assert programme_status(None) is ProgramViewStatus.NONE


def test_the_document_is_the_blocks_that_are_saved() -> None:
    state = a_run().model_copy(
        update={"kind": "professional_development", "title": "Анализ данных", "competencies": []}
    )

    blocks = programme_blocks(state)

    assert blocks.title == "Анализ данных"
    assert blocks.kind is not None
    assert blocks.curriculum is None
    assert blocks.summary is None
    assert blocks.sources == []


def test_the_questions_are_the_open_ones() -> None:
    state = new_state(SESSION_ID, requested_hours=None, requirements="Программа", questions=["Сколько часов?"])
    question = state.questions[0]

    assert [q.text for q in programme_questions(state)] == ["Сколько часов?"]

    answered = state.with_answers([ClarificationAnswer(question_id=question.id, text="72 часа")])

    assert programme_questions(answered) == []
    assert programme_questions(None) == []


def test_a_run_that_answers_nothing_still_reports_its_stage() -> None:
    state = a_run()
    state = state.model_copy(update={"current_stage": Stage.HOURS})

    view = programme_state(state, running=True)

    assert view.status is ProgramViewStatus.GENERATING
    assert view.current_stage is Stage.HOURS
    assert view.running is True
    assert view.version == state.version
    assert view.draft is not None
    assert view.questions == []


def test_a_chat_without_a_run_reports_an_empty_view() -> None:
    view = programme_state(None)

    assert view.status is ProgramViewStatus.NONE
    assert view.current_stage is None
    assert view.running is False
    assert view.updated_at is None
    assert view.questions == []
    assert view.draft is not None
    assert view.draft.title is None
    assert view.draft.competencies == []


def test_a_question_keeps_its_id_and_its_text() -> None:
    question = ClarificationQuestion(id="q-1", text="Для кого программа?")

    assert question.id == "q-1"
    assert question.text == "Для кого программа?"
