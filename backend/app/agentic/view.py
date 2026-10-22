"""The programme as a client sees it.

The rules here are the programme's, not the transport's: a run that died reads
as failed, the document is the blocks that are already saved, and the questions
are the ones nobody answered yet. The shapes the API answers with are the
API's, so this module builds them — one place decides what a client sees and
the route only reports it.
"""

from __future__ import annotations

from app.agentic.models import ProgramStatus, StageStatuses
from app.agentic.state import ProgramState
from app.schemas.chat import (
    ClarificationQuestionSchema,
    ProgramBlocksSchema,
    ProgramExportsSchema,
    ProgramReportSchema,
    ProgramRevisionSchema,
    ProgramStateSchema,
    ProgramViewStatus,
)


def programme_status(state: ProgramState | None) -> ProgramViewStatus:
    """Where a run stands, as the client sees it.

    A run that died keeps the status it had when it died, so the failure is
    folded in here rather than in the envelope. A chat that holds no run at all
    has no status to report.
    """
    if state is None:
        return ProgramViewStatus.NONE
    if state.failed:
        return ProgramViewStatus.FAILED
    return ProgramViewStatus(state.status.value)


def programme_blocks(state: ProgramState) -> ProgramBlocksSchema:
    """The document as it stands: the blocks that are already saved."""
    return ProgramBlocksSchema(
        kind=state.kind,
        title=state.title,
        competencies=state.competencies,
        curriculum=state.curriculum,
        sources=state.sources,
        summary=state.summary,
    )


def programme_questions(state: ProgramState | None) -> list[ClarificationQuestionSchema]:
    """The questions the programme is waiting for, in the order they were asked."""
    if state is None:
        return []
    return [
        ClarificationQuestionSchema.model_validate(question, from_attributes=True)
        for question in state.pending_questions
    ]


def programme_state(state: ProgramState | None, *, running: bool = False) -> ProgramStateSchema:
    """The whole view of a run, or of a chat that holds none.

    `running` is a live fact rather than part of the state: it is true while a
    worker holds the current stage's lock, so a client can tell a programme
    that is being worked on from one that stalled. It is not a completion
    signal — a stage waiting in the queue holds no lock, and the finalisation
    has none either. `busy` is the one to poll on: it is true for as long as
    the programme is being worked on, the finalisation included.
    """
    if state is None:
        return ProgramStateSchema(
            status=ProgramViewStatus.NONE,
            stages=StageStatuses(),
            running=False,
            busy=False,
            updated_at=None,
            version=0,
            draft=ProgramBlocksSchema(),
        )
    return ProgramStateSchema(
        status=programme_status(state),
        current_stage=state.current_stage,
        stages=state.stages,
        running=running,
        busy=state.status is ProgramStatus.GENERATING,
        error=state.error,
        updated_at=state.updated_at,
        version=state.version,
        questions=programme_questions(state),
        draft=programme_blocks(state),
        report=ProgramReportSchema.model_validate(state.report(), from_attributes=True),
        exports=ProgramExportsSchema.model_validate(state.exports, from_attributes=True) if state.exports else None,
        revision=ProgramRevisionSchema.model_validate(state.revision, from_attributes=True) if state.revision else None,
        last_revision=(
            ProgramRevisionSchema.model_validate(state.last_revision, from_attributes=True)
            if state.last_revision
            else None
        ),
        approved_by=state.approved_by,
        approved_at=state.approved_at,
    )
