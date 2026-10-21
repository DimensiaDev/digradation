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

from app.agentic.errors import IncompleteDraft
from app.agentic.models import ProgramStatus, Stage, StageStatus
from app.agentic.state import STAGE_ORDER, ProgramState, new_state

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
        "requested_hours": 66,
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


def test_the_last_stage_leaves_the_run_awaiting_approval() -> None:
    state = new_state(SESSION_ID, requested_hours=None, requirements=None)

    for stage in STAGE_ORDER:
        state = state.started(stage).completed(stage)

    assert state.status is ProgramStatus.PENDING_APPROVAL
    assert state.current_stage is Stage.SUMMARY
    assert all(state.stage_status(stage) is StageStatus.COMPLETED for stage in STAGE_ORDER)


def test_a_failure_below_the_budget_keeps_the_run_alive() -> None:
    state = new_state(SESSION_ID, requested_hours=None, requirements=None)

    failed = state.failed_stage(Stage.COMPETENCES, "провайдер не ответил")

    assert not failed.failed
    assert failed.error == "провайдер не ответил"
    assert failed.stage_status(Stage.COMPETENCES) is StageStatus.FAILED


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
    assert draft.curriculum.total_hours == 66
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


def test_the_curriculum_must_match_the_requested_volume(blocks: Blocks) -> None:
    with pytest.raises(IncompleteDraft) as caught:
        make_state(blocks, requested_hours=72).to_draft()

    assert "не совпадает с заявленным объёмом программы" in str(caught.value)


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

    assert approved.status is ProgramStatus.APPROVED
    assert approved.terminal
    assert not approved.awaiting_approval


def test_an_unrendered_draft_is_not_awaiting_approval(blocks: Blocks) -> None:
    state = make_state(blocks, finalized=False)

    assert not state.awaiting_approval
