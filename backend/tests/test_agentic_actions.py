"""Tests for the methodist's decisions in ``app.agentic.actions``.

The rules live in one module because the model reaches them through a tool and
the client through an HTTP endpoint: both have to refuse the same programmes
for the same reasons, and a rule written twice is a rule that will drift.
"""

from __future__ import annotations

import uuid

import pytest

from app.agentic.actions import approve, revise
from app.agentic.errors import (
    AlreadyApproved,
    DraftIncomplete,
    DraftNotReady,
    ProgrammeFailed,
    RevisionPending,
    SkeletonNotApproved,
    StageBusy,
    StageNotReady,
    WaitingForAnswers,
)
from app.agentic.models import ProgramStatus, Stage, StageStatus, StageStatuses
from app.agentic.state import ProgramState

SESSION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")


def skeleton_written() -> StageStatuses:
    """The four stages of the skeleton, all of them written."""
    return StageStatuses(
        competences=StageStatus.COMPLETED,
        hours=StageStatus.COMPLETED,
        sources=StageStatus.COMPLETED,
        summary=StageStatus.COMPLETED,
    )


def state(**overrides) -> ProgramState:
    """A run whose skeleton is written and rendered, waiting for the methodist."""
    fields: dict = {
        "session_id": SESSION_ID,
        "run_id": uuid.uuid4(),
        "status": ProgramStatus.PENDING_APPROVAL,
        "stages": skeleton_written(),
        "finalized": True,
        "requested_hours": 72,
    }
    fields.update(overrides)
    return ProgramState(**fields)


def test_the_approval_releases_the_content() -> None:
    approved = approve(state())

    assert approved.status is ProgramStatus.GENERATING
    assert approved.current_stage is Stage.ASSESSMENT
    assert approved.skeleton_approved is True


def test_the_approval_names_the_person_who_took_it() -> None:
    methodist = uuid.uuid4()

    approved = approve(state(), by=methodist)

    assert approved.approved_by == methodist
    assert approved.approved_at is not None


def test_a_draft_that_was_never_shown_cannot_be_approved() -> None:
    with pytest.raises(DraftNotReady):
        approve(state(finalized=False))


def test_a_skeleton_with_a_hole_in_it_cannot_be_approved() -> None:
    """A revision of the competences resets the hours, and the draft is not whole."""
    stages = skeleton_written().model_copy(update={"hours": StageStatus.PENDING})

    with pytest.raises(DraftIncomplete):
        approve(state(stages=stages))


def test_a_finished_programme_cannot_be_approved_again() -> None:
    with pytest.raises(AlreadyApproved):
        approve(state(status=ProgramStatus.APPROVED))


def test_a_revision_points_at_the_named_stage() -> None:
    revised = revise(state(), Stage.HOURS, "замени тему 2.3")

    assert revised.current_stage is Stage.HOURS
    assert revised.status is ProgramStatus.GENERATING
    assert revised.revision is not None
    assert revised.revision.stage is Stage.HOURS
    assert revised.revision.notes == "замени тему 2.3"


def test_a_revision_withdraws_the_approval_of_the_skeleton() -> None:
    """The content is written from an approved skeleton, and this is not one any more."""
    approved = approve(state())
    assert approved.skeleton_approved is True

    revised = revise(approved, Stage.HOURS, "замени тему 2.3")

    assert revised.skeleton_approved is False
    assert revised.approved_by is None
    assert revised.approved_at is None


def test_a_programme_waiting_for_answers_cannot_be_revised() -> None:
    with pytest.raises(WaitingForAnswers):
        revise(state(status=ProgramStatus.AWAITING_ANSWERS), Stage.HOURS, "замени тему 2.3")


def test_a_failed_programme_cannot_be_revised() -> None:
    with pytest.raises(ProgrammeFailed):
        revise(state(failed=True), Stage.HOURS, "замени тему 2.3")


def test_a_revision_that_is_being_carried_out_is_not_replaced() -> None:
    """A second change would run the first stage with the second one's notes."""
    pending = state().request_revision(Stage.HOURS, "замени тему 2.3")

    with pytest.raises(RevisionPending):
        revise(pending, Stage.SUMMARY, "перепиши паспорт")


def test_a_programme_that_is_being_written_is_not_revised() -> None:
    """The worker that is writing it would save its own copy over the revision."""
    stages = skeleton_written().model_copy(update={"hours": StageStatus.IN_PROGRESS})

    with pytest.raises(StageBusy):
        revise(state(stages=stages), Stage.HOURS, "замени тему 2.3")


def test_a_stage_that_was_never_written_cannot_be_revised() -> None:
    stages = skeleton_written().model_copy(update={"hours": StageStatus.PENDING})

    with pytest.raises(StageNotReady):
        revise(state(stages=stages), Stage.HOURS, "замени тему 2.3")


def test_a_stage_whose_foundation_is_missing_cannot_be_revised() -> None:
    stages = skeleton_written().model_copy(update={"competences": StageStatus.PENDING})

    with pytest.raises(StageNotReady):
        revise(state(stages=stages), Stage.HOURS, "замени тему 2.3")


def test_the_content_is_not_revised_before_the_skeleton_is_approved() -> None:
    """The content is written from an approved skeleton and from nothing else."""
    stages = skeleton_written().model_copy(update={"assessment": StageStatus.COMPLETED})

    with pytest.raises(SkeletonNotApproved):
        revise(state(stages=stages), Stage.ASSESSMENT, "усложни тест")


def test_the_content_is_revised_once_the_skeleton_is_approved() -> None:
    stages = skeleton_written().model_copy(update={"assessment": StageStatus.COMPLETED})
    approved = approve(state(stages=stages))

    revised = revise(approved, Stage.ASSESSMENT, "усложни тест")

    assert revised.revision is not None
    assert revised.revision.stage is Stage.ASSESSMENT
