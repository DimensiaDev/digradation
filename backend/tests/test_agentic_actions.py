"""Tests for the methodist's decisions in ``app.agentic.actions``.

The rules live in one module because the model reaches them through a tool and
the client through an HTTP endpoint: both have to refuse the same programmes
for the same reasons, and a rule written twice is a rule that will drift.
"""

from __future__ import annotations

import uuid

import pytest

from app.agentic.actions import approve, revise
from app.agentic.errors import AlreadyApproved, DraftNotReady, ProgrammeFailed, WaitingForAnswers
from app.agentic.models import ProgramStatus, Stage
from app.agentic.state import ProgramState

SESSION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")


def state(**overrides) -> ProgramState:
    """A run whose skeleton is rendered and waits for the methodist."""
    fields: dict = {
        "session_id": SESSION_ID,
        "run_id": uuid.uuid4(),
        "status": ProgramStatus.PENDING_APPROVAL,
        "finalized": True,
        "requested_hours": 72,
    }
    fields.update(overrides)
    return ProgramState(**fields)


def test_the_approval_releases_the_content() -> None:
    approved = approve(state())

    assert approved.status is ProgramStatus.GENERATING
    assert approved.current_stage is Stage.ASSESSMENT


def test_the_approval_names_the_person_who_took_it() -> None:
    methodist = uuid.uuid4()

    approved = approve(state(), by=methodist)

    assert approved.approved_by == methodist
    assert approved.approved_at is not None


def test_a_draft_that_was_never_shown_cannot_be_approved() -> None:
    with pytest.raises(DraftNotReady):
        approve(state(finalized=False))


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


def test_a_programme_waiting_for_answers_cannot_be_revised() -> None:
    with pytest.raises(WaitingForAnswers):
        revise(state(status=ProgramStatus.AWAITING_ANSWERS), Stage.HOURS, "замени тему 2.3")


def test_a_failed_programme_cannot_be_revised() -> None:
    with pytest.raises(ProgrammeFailed):
        revise(state(failed=True), Stage.HOURS, "замени тему 2.3")
