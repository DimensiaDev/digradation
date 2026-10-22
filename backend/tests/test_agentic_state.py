"""Tests for the programme run state in ``app.agentic.state``.

The blocks come from the documented example document, so a valid draft here is
the same one the schema tests validate: the run state wraps real data rather than
a fixture invented for these tests.
"""

from __future__ import annotations

import json
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.agentic.errors import AnswersDoNotMatch, IncompleteDraft
from app.agentic.models import ProgramStatus, Stage, StageStatus, StageStatuses
from app.agentic.state import (
    SKELETON_ORDER,
    STAGE_ORDER,
    AssessmentBlock,
    ClarificationAnswer,
    ProgramExports,
    ProgramState,
    SourcesBlock,
    apply_answers,
    new_state,
)

EXAMPLE_PATH = Path(__file__).resolve().parents[1] / "examples" / "document_state_example.json"

SESSION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
RUN_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")

Blocks = dict[str, Any]


@pytest.fixture()
def blocks() -> Blocks:
    """The draft blocks of the example document, as plain JSON."""
    raw = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
    return {
        "kind": raw["meta"]["kind"],
        "title": raw["meta"]["title"],
        "competencies": raw["competencies"],
        "curriculum": raw["curriculum"],
        "sources": raw["sources"],
        "summary": raw["summary"],
    }


def make_state(blocks: Blocks, **overrides: Any) -> ProgramState:
    """A run that finished every stage and awaits approval."""
    fields: dict[str, Any] = {
        "session_id": SESSION_ID,
        "run_id": RUN_ID,
        "status": ProgramStatus.PENDING_APPROVAL,
        "current_stage": Stage.SUMMARY,
        "requested_hours": 72,
        "finalized": True,
        **blocks,
        **overrides,
    }
    return ProgramState(**fields)


def test_new_state_queues_the_first_stage() -> None:
    state = new_state(SESSION_ID, requested_hours=72, requirements="Нужен курс для аналитиков")

    assert state.status is ProgramStatus.GENERATING
    assert state.current_stage is Stage.COMPETENCES
    assert state.attempts == {Stage.COMPETENCES.value: 1}
    assert state.stage_status(Stage.COMPETENCES) is StageStatus.PENDING
    assert state.version == 0
    assert not state.finalized and not state.failed


def test_enqueued_spends_an_attempt_and_returns_the_stage_to_pending() -> None:
    state = new_state(SESSION_ID, requested_hours=None, requirements=None).started(Stage.COMPETENCES)
    state = state.completed(Stage.COMPETENCES)

    after = state.enqueued(Stage.HOURS)

    assert after.current_stage is Stage.HOURS
    assert after.stage_status(Stage.HOURS) is StageStatus.PENDING
    assert after.attempts[Stage.HOURS.value] == 1
    assert after.version == state.version + 1


def test_started_marks_the_stage_running_and_keeps_the_attempt_count() -> None:
    state = new_state(SESSION_ID, requested_hours=None, requirements=None)

    started = state.started(Stage.COMPETENCES)

    assert started.stage_status(Stage.COMPETENCES) is StageStatus.IN_PROGRESS
    assert started.attempts == state.attempts
    assert started.version == state.version + 1


def test_completed_moves_the_run_to_the_next_stage() -> None:
    state = new_state(SESSION_ID, requested_hours=None, requirements=None)

    completed = state.completed(Stage.COMPETENCES)

    assert completed.current_stage is Stage.HOURS
    assert completed.status is ProgramStatus.GENERATING
    assert completed.stage_status(Stage.COMPETENCES) is StageStatus.COMPLETED


def test_the_skeleton_leaves_the_run_awaiting_approval() -> None:
    """The skeleton is what the methodist approves, so the run waits there."""
    state = new_state(SESSION_ID, requested_hours=None, requirements=None)

    for stage in SKELETON_ORDER:
        state = state.started(stage).completed(stage)

    assert state.status is ProgramStatus.PENDING_APPROVAL
    assert state.current_stage is Stage.ASSESSMENT
    assert all(state.stage_status(stage) is StageStatus.COMPLETED for stage in SKELETON_ORDER)


def test_the_content_leaves_the_run_approved() -> None:
    """Once the content is written the programme is complete."""
    state = new_state(SESSION_ID, requested_hours=None, requirements=None)

    for stage in STAGE_ORDER:
        state = state.started(stage).completed(stage)

    assert state.status is ProgramStatus.APPROVED
    assert state.terminal
    assert all(state.stage_status(stage) is StageStatus.COMPLETED for stage in STAGE_ORDER)


def test_the_stages_run_in_order() -> None:
    """Every stage hands over to the next one, the skeleton first."""
    state = new_state(SESSION_ID, requested_hours=None, requirements=None)

    for stage in STAGE_ORDER:
        state = state.started(stage)
        assert state.current_stage is stage
        state = state.completed(stage)

    assert state.current_stage is Stage.MATERIALS


def test_a_stage_outside_the_order_has_no_successor() -> None:
    """A stage the pipeline does not run has no successor."""
    state = new_state(SESSION_ID, requested_hours=None, requirements=None)

    assert state._successor(Stage.ASSESSMENT) is Stage.MATERIALS
    assert state._successor(Stage.MATERIALS) is None


# ---------------------------------------------------------------------------
# the report the methodist reads
# ---------------------------------------------------------------------------


def test_the_report_covers_the_hours_the_coverage_the_sources_and_the_sections(blocks: Blocks) -> None:
    """One place where the checks of the programme are reported."""
    state = make_state(blocks)

    report = state.report()

    assert [check.name for check in report.checks] == ["часы", "покрытие", "источники", "разделы"]
    hours = report.checks[0]
    assert hours.ok is True
    assert hours.details["всего"] == 72
    assert hours.details["темы"] == 66
    assert hours.details["итоговая аттестация"] == 6
    assert report.checks[1].details["результатов обучения"] == 8
    assert report.checks[1].details["раскрыто в темах"] == 8


def test_the_report_names_what_is_missing(blocks: Blocks) -> None:
    blocks["curriculum"]["modules"][0]["hours"] = 99

    report = make_state(blocks).report()

    assert report.ok is False
    assert "модуль m-1 заявлен на 99 ч" in " ".join(report.checks[0].problems)


def test_the_report_names_the_sources_nobody_confirmed(blocks: Blocks) -> None:
    """A source the catalog does not hold is a finding, not a silent gap."""
    state = make_state(blocks).with_sources(SourcesBlock(sources=blocks["sources"]))

    report = state.report()

    sources = report.checks[2]
    assert sources.name == "источники"
    assert sources.details["всего источников"] == 6
    assert sources.details["не подтверждено"] == 4
    assert sources.details["подтверждено"] == 2
    assert "act-273-fz" in " ".join(sources.problems)
    assert "profstd-06.001" not in " ".join(sources.problems)


def test_the_report_calls_an_empty_assessment_an_empty_section(blocks: Blocks) -> None:
    """A section that exists but holds nothing is not filled."""
    state = make_state(blocks)
    state = state.with_assessment(
        AssessmentBlock(
            assessment={
                "final": {"form": "экзамен", "hours": 0, "outcome_ids": ["ro-k-01"]},
            }
        )
    )

    report = state.report()

    sections = report.checks[3]
    assert sections.details["оценочные средства"] is False
    assert "оценочные средства" in " ".join(sections.problems)


def test_the_report_counts_the_means_of_checking(blocks: Blocks) -> None:
    """The coverage check counts how many means each outcome has."""
    state = make_state(blocks)
    state = state.with_assessment(
        AssessmentBlock(
            assessment={
                "questions": [
                    {
                        "id": "q-01-01",
                        "tool_id": "at-01",
                        "outcome_ids": ["ro-k-01"],
                        "text": "Вопрос",
                        "answers": ["Ответ"],
                    }
                ],
                "seminar_tasks": [],
                "tools": [],
                "final": {"form": "экзамен", "hours": 0, "outcome_ids": ["ro-k-01"]},
            }
        )
    )

    report = state.report()

    coverage = report.checks[1]
    assert coverage.name == "покрытие"
    assert coverage.details["проверяется двумя средствами"] == 0
    assert "нужно хотя бы два" in " ".join(coverage.problems)


def test_the_report_says_which_sections_are_empty(blocks: Blocks) -> None:
    state = make_state(blocks)

    report = state.report()

    sections = report.checks[3]
    assert sections.details["оценочные средства"] is False
    assert sections.details["материалы"] is False
    assert sections.ok is False


# ---------------------------------------------------------------------------
# the questions a run waits for
# ---------------------------------------------------------------------------


def test_a_run_with_questions_waits_for_them() -> None:
    """A run that asked questions does not generate yet."""
    state = new_state(SESSION_ID, requested_hours=None, requirements="Программа", questions=["Сколько часов?"])

    assert state.status is ProgramStatus.AWAITING_ANSWERS
    assert state.awaiting_answers
    assert state.attempts == {}
    assert [question.text for question in state.pending_questions] == ["Сколько часов?"]
    assert state.questions[0].id


def test_a_run_without_questions_starts_generating() -> None:
    state = new_state(SESSION_ID, requested_hours=None, requirements="Программа")

    assert state.status is ProgramStatus.GENERATING
    assert not state.awaiting_answers
    assert state.pending_questions == []
    assert state.attempts == {Stage.COMPETENCES: 1}


def test_the_answers_start_the_programme() -> None:
    state = new_state(SESSION_ID, requested_hours=None, requirements="Программа", questions=["Сколько часов?"])
    question = state.questions[0]

    answered = apply_answers(state, [ClarificationAnswer(question_id=question.id, text="72 часа")])

    assert answered.status is ProgramStatus.GENERATING
    assert answered.pending_questions == []
    assert [answer.text for answer in answered.answers] == ["72 часа"]
    assert answered.attempts == {Stage.COMPETENCES: 1}


def test_answers_to_questions_nobody_asked_are_refused() -> None:
    state = new_state(SESSION_ID, requested_hours=None, requirements="Программа", questions=["Сколько часов?"])

    with pytest.raises(AnswersDoNotMatch):
        apply_answers(state, [ClarificationAnswer(question_id="q-9", text="72 часа")])


def test_a_duplicated_answer_is_refused() -> None:
    state = new_state(SESSION_ID, requested_hours=None, requirements="Программа", questions=["Сколько часов?"])
    question = state.questions[0]

    with pytest.raises(AnswersDoNotMatch):
        apply_answers(
            state,
            [
                ClarificationAnswer(question_id=question.id, text="72 часа"),
                ClarificationAnswer(question_id=question.id, text="36 часов"),
            ],
        )


def test_answers_to_a_run_that_is_not_waiting_are_refused() -> None:
    state = new_state(SESSION_ID, requested_hours=None, requirements="Программа")

    with pytest.raises(AnswersDoNotMatch):
        apply_answers(state, [ClarificationAnswer(question_id="q-1", text="72 часа")])


def test_a_failure_below_the_budget_keeps_the_run_alive() -> None:
    state = new_state(SESSION_ID, requested_hours=None, requirements=None)

    failed = state.failed_stage(Stage.COMPETENCES, "провайдер не ответил")

    assert not failed.failed
    assert failed.error == "провайдер не ответил"
    assert failed.stage_status(Stage.COMPETENCES) is StageStatus.FAILED


def test_a_completed_stage_clears_the_error_of_its_failed_attempt() -> None:
    state = new_state(SESSION_ID, requested_hours=None, requirements=None)
    failed = state.failed_stage(Stage.COMPETENCES, "провайдер не ответил")

    done = failed.started(Stage.COMPETENCES).completed(Stage.COMPETENCES)

    assert done.error is None


def test_the_last_attempt_failing_ends_the_run() -> None:
    state = new_state(SESSION_ID, requested_hours=None, requirements=None)
    state = state.enqueued(Stage.COMPETENCES).enqueued(Stage.COMPETENCES)
    assert state.attempts[Stage.COMPETENCES.value] == 3

    failed = state.failed_stage(Stage.COMPETENCES, "провайдер не ответил")

    assert failed.failed
    assert failed.terminal
    assert not failed.awaiting_approval


def test_state_is_immutable_and_transitions_do_not_touch_the_source() -> None:
    state = new_state(SESSION_ID, requested_hours=None, requirements=None)

    started = state.started(Stage.COMPETENCES)

    assert state.stage_status(Stage.COMPETENCES) is StageStatus.PENDING
    assert started.version == state.version + 1
    assert started.updated_at >= state.updated_at
    with pytest.raises(ValidationError):
        state.version = 5


def test_a_complete_run_produces_the_draft(blocks: Blocks) -> None:
    draft = make_state(blocks).to_draft()

    assert draft.title == blocks["title"]
    assert len(draft.competencies) == 2
    assert draft.curriculum.topic_hours == 66
    assert draft.curriculum.total_hours == 72
    assert len(draft.sources) == 6


def test_a_missing_block_names_the_stage_that_did_not_finish(blocks: Blocks) -> None:
    with pytest.raises(IncompleteDraft) as caught:
        make_state(blocks, summary=None).to_draft()

    assert "не сформирован паспорт программы" in str(caught.value)


def test_every_missing_block_is_reported_at_once(blocks: Blocks) -> None:
    with pytest.raises(IncompleteDraft) as caught:
        make_state(blocks, kind=None, title=None, competencies=[], curriculum=None, summary=None).to_draft()

    assert len(caught.value.problems) == 5


def test_a_duplicate_competence_id_is_refused(blocks: Blocks) -> None:
    blocks["competencies"][1]["id"] = blocks["competencies"][0]["id"]

    with pytest.raises(IncompleteDraft) as caught:
        make_state(blocks).to_draft()

    assert "объявлена дважды" in str(caught.value)


def test_a_duplicate_outcome_id_is_refused(blocks: Blocks) -> None:
    competence = blocks["competencies"][0]
    competence["knowledge"].append(deepcopy(competence["skills"][0]))

    with pytest.raises(IncompleteDraft) as caught:
        make_state(blocks).to_draft()

    assert "результат обучения" in str(caught.value)


def test_a_topic_may_not_cover_an_undeclared_outcome(blocks: Blocks) -> None:
    blocks["curriculum"]["modules"][0]["topics"][0]["outcome_ids"] = ["ro-k-99"]

    with pytest.raises(IncompleteDraft) as caught:
        make_state(blocks).to_draft()

    assert "ro-k-99" in str(caught.value)


def test_an_outcome_nobody_teaches_is_refused(blocks: Blocks) -> None:
    """A declared result that no topic reveals is not a result of the programme."""
    for module in blocks["curriculum"]["modules"]:
        for topic in module["topics"]:
            topic["outcome_ids"] = [item for item in topic["outcome_ids"] if item != "ro-s-02"]

    with pytest.raises(IncompleteDraft) as caught:
        make_state(blocks).to_draft()

    assert "результат обучения ro-s-02 не раскрыт ни в одной теме" in str(caught.value)


def test_the_curriculum_must_match_the_requested_volume(blocks: Blocks) -> None:
    with pytest.raises(IncompleteDraft) as caught:
        make_state(blocks, requested_hours=80).to_draft()

    assert "не совпадает с заявленным объёмом" in str(caught.value)


def test_a_module_must_add_up_to_its_topics(blocks: Blocks) -> None:
    blocks["curriculum"]["modules"][0]["hours"] = 99

    with pytest.raises(IncompleteDraft) as caught:
        make_state(blocks).to_draft()

    assert "модуль m-1 заявлен на 99 ч" in str(caught.value)


def test_the_final_attestation_is_part_of_the_volume(blocks: Blocks) -> None:
    blocks["curriculum"]["attestation_hours"] = 0

    with pytest.raises(IncompleteDraft) as caught:
        make_state(blocks).to_draft()

    assert "не совпадает с заявленным объёмом" in str(caught.value)


def test_a_daily_load_over_the_stated_limit_is_refused(blocks: Blocks) -> None:
    blocks["curriculum"]["schedule"] = {"days": 18, "hours_per_day": 6, "max_hours_per_day": 4}

    with pytest.raises(IncompleteDraft) as caught:
        make_state(blocks).to_draft()

    assert "превышает заявленный предел 4 ч в день" in str(caught.value)


def test_more_remote_hours_than_the_volume_is_refused(blocks: Blocks) -> None:
    blocks["curriculum"]["distance_hours"] = 100

    with pytest.raises(IncompleteDraft) as caught:
        make_state(blocks).to_draft()

    assert "дистанционных часов 100 больше" in str(caught.value)


def test_a_schedule_too_short_for_the_volume_is_refused(blocks: Blocks) -> None:
    blocks["curriculum"]["schedule"] = {"days": 2, "hours_per_day": 4}

    with pytest.raises(IncompleteDraft) as caught:
        make_state(blocks).to_draft()

    assert "меньше объёма программы" in str(caught.value)


def test_a_citation_missing_from_the_sources_is_refused(blocks: Blocks) -> None:
    blocks["summary"]["basis_source_ids"] = ["src-99"]

    with pytest.raises(IncompleteDraft) as caught:
        make_state(blocks).to_draft()

    assert "src-99" in str(caught.value)


def test_the_state_survives_a_json_round_trip(blocks: Blocks) -> None:
    state = make_state(blocks)

    restored = ProgramState.model_validate(json.loads(state.model_dump_json()))

    assert restored == state


def test_awaiting_approval_needs_a_rendered_draft(blocks: Blocks) -> None:
    state = make_state(blocks)
    assert state.awaiting_approval
    assert not state.terminal

    approved = state.approved()

    # The approval releases the content: the run keeps working rather than
    # becoming a finished programme.
    assert approved.status is ProgramStatus.GENERATING
    assert not approved.terminal
    assert not approved.awaiting_approval


def test_a_finished_programme_is_approved(blocks: Blocks) -> None:
    """The programme is complete only when every part of the content is."""
    state = make_state(blocks).completed(Stage.ASSESSMENT).completed(Stage.MATERIALS)

    assert state.status is ProgramStatus.APPROVED
    assert state.terminal


def test_an_unrendered_draft_is_not_awaiting_approval(blocks: Blocks) -> None:
    state = make_state(blocks, finalized=False)

    assert not state.awaiting_approval


def test_the_approval_records_who_took_it(blocks: Blocks) -> None:
    """The approval is the methodist's own decision, so it says who took it."""
    state = make_state(blocks)
    methodist = uuid.uuid4()

    approved = state.approved(by=methodist)

    assert approved.approved_by == methodist
    assert approved.approved_at is not None
    # The record is the approval's, and nothing else moves with it.
    assert state.approved_by is None
    assert state.approved_at is None


def test_a_revision_records_who_asked_for_it(blocks: Blocks) -> None:
    state = make_state(blocks)
    methodist = uuid.uuid4()

    revised = state.request_revision(Stage.HOURS, "замени тему 2.3", by=methodist)

    assert revised.revision is not None
    assert revised.revision.requested_by == methodist
    assert revised.revision.requested_at is not None


def all_stages_written() -> StageStatuses:
    """Every stage of the programme, written."""
    return StageStatuses(**{stage.value: StageStatus.COMPLETED for stage in STAGE_ORDER})


def test_a_revision_drops_the_exports_it_made_stale(blocks: Blocks) -> None:
    """The old document describes a programme that is no longer on the table."""
    state = make_state(blocks, stages=all_stages_written()).with_exports(
        ProgramExports(document_attachment_id=1, slides_attachment_id=2)
    )

    revised = state.request_revision(Stage.ASSESSMENT, "усложни тест")

    assert revised.exports is None


def test_a_revision_of_the_skeleton_withdraws_the_approval(blocks: Blocks) -> None:
    state = make_state(blocks, stages=all_stages_written()).approved(by=uuid.uuid4())
    assert state.skeleton_approved

    revised = state.request_revision(Stage.HOURS, "замени тему 2.3")

    assert revised.skeleton_approved is False
    assert revised.approved_by is None
    assert revised.approved_at is None


def test_a_revision_of_the_content_keeps_the_approval(blocks: Blocks) -> None:
    methodist = uuid.uuid4()
    state = make_state(blocks, stages=all_stages_written()).approved(by=methodist)

    revised = state.request_revision(Stage.ASSESSMENT, "усложни тест")

    assert revised.skeleton_approved is True
    assert revised.approved_by == methodist


def test_a_completed_revision_is_kept_as_the_last_one(blocks: Blocks) -> None:
    """The pending field is cleared when the work is done, and the record has to outlive it."""
    methodist = uuid.uuid4()
    revised = make_state(blocks, stages=all_stages_written()).request_revision(
        Stage.HOURS, "замени тему 2.3", by=methodist
    )

    done = revised.completed(Stage.HOURS).revision_completed(Stage.HOURS)

    assert done.revision is None
    assert done.last_revision is not None
    assert done.last_revision.stage is Stage.HOURS
    assert done.last_revision.requested_by == methodist
    assert done.last_revision.requested_at is not None


def test_a_redone_skeleton_waits_until_the_skeleton_is_whole(blocks: Blocks) -> None:
    """A revision of the competences resets the hours, and the draft is not shown yet."""
    revised = make_state(blocks, stages=all_stages_written()).request_revision(Stage.COMPETENCES, "уточни результаты")

    done = revised.completed(Stage.COMPETENCES).revision_completed(Stage.COMPETENCES)

    assert done.status is ProgramStatus.GENERATING
    assert done.current_stage is Stage.HOURS
    assert done.stage_status(Stage.HOURS) is StageStatus.PENDING


def test_a_redone_skeleton_returns_to_approval_when_it_is_whole(blocks: Blocks) -> None:
    revised = make_state(blocks, stages=all_stages_written()).request_revision(Stage.SUMMARY, "перепиши паспорт")

    done = revised.completed(Stage.SUMMARY).revision_completed(Stage.SUMMARY)

    assert done.status is ProgramStatus.PENDING_APPROVAL
    assert done.current_stage is Stage.SUMMARY
