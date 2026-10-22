"""The methodist's two decisions about a programme.

The model reaches them through its tools and the client through the HTTP
endpoints, so the rules that decide whether a decision is allowed live here
once: the state knows what a decision does to the programme, and this module
knows when it may happen. A rule written twice is a rule that will drift, and
the two surfaces have to refuse the same programmes for the same reasons.
"""

from __future__ import annotations

import uuid

from app.agentic.errors import AlreadyApproved, DraftNotReady, ProgrammeFailed, WaitingForAnswers
from app.agentic.models import ProgramStatus, Stage
from app.agentic.state import ProgramState


def approve(state: ProgramState, *, by: uuid.UUID | None = None) -> ProgramState:
    """Approve the skeleton: the content may be written from it.

    The approval is the methodist's own decision, so the record names whoever
    made it. A programme that is already complete has nothing left to approve,
    and a draft that was never shown cannot be confirmed.
    """
    if state.status is ProgramStatus.APPROVED:
        raise AlreadyApproved()
    if not state.awaiting_approval:
        raise DraftNotReady()
    return state.approved(by=by)


def revise(state: ProgramState, stage: Stage, notes: str, *, by: uuid.UUID | None = None) -> ProgramState:
    """Ask for one part of the programme to be redone.

    A run that died cannot be revised: there is nothing left to redo. A run
    that waits for the methodist's answers has not written the part that would
    be redone yet, so the answer comes first.
    """
    if state.failed:
        raise ProgrammeFailed()
    if state.status is ProgramStatus.AWAITING_ANSWERS:
        raise WaitingForAnswers()
    return state.request_revision(stage, notes, by=by)
