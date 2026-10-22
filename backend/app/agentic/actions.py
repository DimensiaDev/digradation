"""The methodist's two decisions about a programme.

The model reaches them through its tools and the client through the HTTP
endpoints, so the rules that decide whether a decision is allowed live here
once: the state knows what a decision does to the programme, and this module
knows when it may happen. A rule written twice is a rule that will drift, and
the two surfaces have to refuse the same programmes for the same reasons.

The rules are about the *programme*, not about the transport: a decision that
would write content the methodist has not approved, or that would be lost
because the programme moved on, is refused by both surfaces alike.
"""

from __future__ import annotations

import uuid

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
from app.agentic.models import ProgramStatus, Stage, StageStatus
from app.agentic.state import CONTENT_ORDER, SKELETON_ORDER, STAGE_ORDER, ProgramState


def approve(state: ProgramState, *, by: uuid.UUID | None = None) -> ProgramState:
    """Approve the skeleton: the content may be written from it.

    The approval is the methodist's own decision, so the record names whoever
    made it. A programme that is already complete has nothing left to approve,
    and a draft that was never shown cannot be confirmed.

    A skeleton with a hole in it is not approved either. A revision of the
    competences resets the hours and a revision of the sources resets the
    passport, so the approval would release the content of a programme whose
    skeleton is not whole.
    """
    if state.status is ProgramStatus.APPROVED:
        raise AlreadyApproved()
    if not state.awaiting_approval:
        raise DraftNotReady()
    if _missing_skeleton(state):
        raise DraftIncomplete()
    return state.approved(by=by)


def revise(state: ProgramState, stage: Stage, notes: str, *, by: uuid.UUID | None = None) -> ProgramState:
    """Ask for one part of the programme to be redone.

    A run that died cannot be revised: there is nothing left to redo. A run
    that waits for the methodist's answers has not written the part that would
    be redone yet, so the answer comes first.

    A change that is already being carried out is not replaced by another one:
    the queued stage would run with the notes of the second request and write
    a block that answers neither.

    A programme that is being written right now is not revised either. The
    worker that is writing it would save its own copy of the programme over
    the revision, and the change the methodist asked for would be lost.

    Only a block that exists may be redone, and only when what it is written
    from exists too: a stage that runs without its own foundation writes a
    block that points at something that is not there.

    The content is written from an approved skeleton and from nothing else, so
    a change of the content waits for the approval.
    """
    if state.failed:
        raise ProgrammeFailed()
    if state.status is ProgramStatus.AWAITING_ANSWERS:
        raise WaitingForAnswers()
    if state.revision is not None:
        raise RevisionPending()
    if _writing_now(state):
        raise StageBusy()
    if stage in CONTENT_ORDER and not state.skeleton_approved:
        raise SkeletonNotApproved()
    if not _written(state, stage):
        raise StageNotReady()
    return state.request_revision(stage, notes, by=by)


def _missing_skeleton(state: ProgramState) -> list[Stage]:
    """The skeleton stages that were not written yet."""
    return [stage for stage in SKELETON_ORDER if state.stage_status(stage) is not StageStatus.COMPLETED]


def _writing_now(state: ProgramState) -> bool:
    """Whether a worker is writing a stage of this programme right now."""
    return any(state.stage_status(stage) is StageStatus.IN_PROGRESS for stage in STAGE_ORDER)


def _written(state: ProgramState, stage: Stage) -> bool:
    """Whether the stage and everything it is written from are there.

    A stage runs on what came before it: the hours on the competences, the
    content on the hours. A stage whose foundation is missing would write a
    block that points at something that is not there.
    """
    index = STAGE_ORDER.index(stage)
    return all(state.stage_status(item) is StageStatus.COMPLETED for item in STAGE_ORDER[: index + 1])
