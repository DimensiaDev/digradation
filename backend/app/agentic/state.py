"""The state of a programme being built in a chat.

The first task of the cycle established the document schema
(:mod:`app.agentic.models`). This module wraps that schema in the envelope the
sequential pipeline needs: the same ``Competence``, ``Curriculum``,
``ProgramSummary`` and ``Source`` models, plus where the run currently stands.

The envelope is what is stored in ``chat_sessions.program_state`` as JSONB and
mirrored to Redis while a task works on it. A stage writes one block of the
document at a time and moves the envelope to the next stage; :meth:`ProgramState.to_draft`
is the gate that only opens once every block is complete and the draft passes the
cross-block validation of :class:`~app.agentic.models.ProgramDraft`.

The state is deliberately a value: every transition returns a new instance, so a
worker can checkpoint a state without worrying that a later stage mutated the one
already committed.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from pydantic import Field

from app.agentic.models import (
    Competence,
    Curriculum,
    ProgramDraft,
    ProgramKind,
    ProgramStatus,
    ProgramSummary,
    SchemaModel,
    Source,
    Stage,
    StageStatus,
    StageStatuses,
)

#: Stages issue #3 owns, in the order the worker walks them. ``assessment`` and
#: ``materials`` belong to the full-package stage (#5) and stay pending.
STAGE_ORDER: tuple[Stage, ...] = (
    Stage.COMPETENCES,
    Stage.HOURS,
    Stage.SOURCES,
    Stage.SUMMARY,
)

#: How many times the pipeline retries a stage before giving up. A task that
#: dies mid-stage leaves its attempts counter on the state, so the recovery cron
#: can tell "try again" from "this stage is broken".
MAX_STAGE_ATTEMPTS = 3

#: A stage is considered stalled when no checkpoint has touched it for this long.
STALLED_AFTER_SECONDS = 600


class PipelineMeta(SchemaModel):
    """Identity and version of one run of the pipeline."""

    session_id: uuid.UUID
    run_id: str
    version: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime


class ProgramState(SchemaModel):
    """The document being built and the place of the run in the stage chain.

    ``status`` is the lifecycle of the whole programme, while ``stages`` is the
    per-block progress and ``current_stage`` is what the worker should run next.
    The document fields are optional because they are filled one stage at a time;
    :meth:`to_draft` refuses to produce a ``ProgramDraft`` until all of them are
    present, so a half-built state can never be mistaken for a reviewable draft.
    """

    meta: PipelineMeta
    status: ProgramStatus = ProgramStatus.GENERATING
    current_stage: Stage | None = None
    stages: StageStatuses = Field(default_factory=StageStatuses)
    requested_hours: float = Field(gt=0, description="Объём программы в академических часах")
    requirements: str = Field(min_length=1, max_length=8000, description="Исходные требования методиста")
    kind: ProgramKind | None = None
    title: str | None = Field(default=None, max_length=500)
    summary: ProgramSummary | None = None
    competencies: list[Competence] = Field(default_factory=list)
    curriculum: Curriculum | None = None
    sources: list[Source] = Field(default_factory=list)
    #: Source ids the run has actually shown the model. Persisted between stages
    #: so a retry or a later stage can still cite what an earlier search found.
    known_source_ids: list[str] = Field(default_factory=list)
    attempts: dict[str, int] = Field(default_factory=dict)
    last_error: str | None = None
    failed: bool = False
    approved_at: datetime | None = None
    finalized: bool = False

    def stage_status(self, stage: Stage) -> StageStatus:
        return getattr(self.stages, stage.value)

    def next_stage(self) -> Stage | None:
        """The first stage that is not completed, in the pipeline order."""
        for stage in STAGE_ORDER:
            if self.stage_status(stage) is not StageStatus.COMPLETED:
                return stage
        return None

    def to_draft(self) -> ProgramDraft:
        """Build the reviewable draft, refusing to do so before every block is in.

        This is the gate between "the pipeline is still filling the document" and
        "the methodist has something to confirm": it also runs the cross-block
        validators of :class:`ProgramDraft`, so a state that reached the approval
        point with a dangling outcome or mismatched hours cannot be approved.
        """
        missing = [stage.value for stage in STAGE_ORDER if self.stage_status(stage) is not StageStatus.COMPLETED]
        missing.extend(name for name in ("kind", "title", "summary", "curriculum") if getattr(self, name) is None)
        if not self.competencies:
            missing.append("competencies")
        if missing:
            raise ValueError("draft is incomplete: " + ", ".join(missing))
        return ProgramDraft(
            kind=self.kind,
            title=self.title,
            requested_hours=self.requested_hours,
            summary=self.summary,
            competencies=self.competencies,
            curriculum=self.curriculum,
            sources=self.sources,
        )


class CompetencesBlock(SchemaModel):
    """The competence block a stage prompt asks the model to fill."""

    competencies: list[Competence] = Field(min_length=1)


class HoursBlock(SchemaModel):
    """The curriculum block a stage prompt asks the model to fill."""

    curriculum: Curriculum


class SourcesBlock(SchemaModel):
    """The source list a stage prompt asks the model to fill."""

    sources: list[Source] = Field(default_factory=list)


class SummaryBlock(SchemaModel):
    """The programme passport and title a stage prompt asks the model to fill."""

    kind: ProgramKind
    title: str = Field(min_length=1, max_length=500)
    summary: ProgramSummary


STAGE_BLOCKS: dict[Stage, type[SchemaModel]] = {
    Stage.COMPETENCES: CompetencesBlock,
    Stage.HOURS: HoursBlock,
    Stage.SOURCES: SourcesBlock,
    Stage.SUMMARY: SummaryBlock,
}


def _now() -> datetime:
    return datetime.now(UTC)


def _bump(state: ProgramState, **updates) -> ProgramState:
    """A copy of ``state`` with a new version and timestamp."""
    meta = state.meta.model_copy(update={"version": state.meta.version + 1, "updated_at": _now()})
    return state.model_copy(update={"meta": meta, **updates})


def new_state(
    session_id: uuid.UUID,
    *,
    requested_hours: float,
    requirements: str,
    title: str | None = None,
    run_id: str | None = None,
) -> ProgramState:
    """Start a run: the first stage is ready and every other stage is pending."""
    now = _now()
    stages = StageStatuses().model_copy(update={STAGE_ORDER[0].value: StageStatus.IN_PROGRESS})
    return ProgramState(
        meta=PipelineMeta(
            session_id=session_id,
            run_id=run_id or uuid.uuid4().hex,
            created_at=now,
            updated_at=now,
        ),
        requested_hours=requested_hours,
        requirements=requirements,
        title=title,
        current_stage=STAGE_ORDER[0],
        stages=stages,
    )


def mark_stage_started(state: ProgramState, stage: Stage) -> ProgramState:
    """Record that a worker picked the stage up and bump its attempt counter."""
    if state.stage_status(stage) is StageStatus.COMPLETED:
        return state
    attempts = dict(state.attempts)
    attempts[stage.value] = attempts.get(stage.value, 0) + 1
    stages = state.stages.model_copy(update={stage.value: StageStatus.IN_PROGRESS})
    return _bump(
        state,
        current_stage=stage,
        stages=stages,
        attempts=attempts,
        last_error=None,
        failed=False,
    )


def mark_stage_failed(state: ProgramState, stage: Stage, error: str) -> ProgramState:
    """Record a failed attempt without losing the blocks an earlier stage wrote.

    Until the attempt budget is spent the stage stays ``in_progress``: the
    recovery cron will hand it to a fresh worker, and a state that had already
    committed its earlier blocks keeps them. At the budget the stage becomes
    ``failed`` and ``failed`` is set on the envelope, which is what stops the
    recovery cron from retrying a stage that is actually broken.
    """
    if state.stage_status(stage) is StageStatus.COMPLETED:
        return state
    attempts = dict(state.attempts)
    attempt = attempts.get(stage.value, 0)
    if attempt == 0:
        # A worker can die before ``mark_stage_started`` reaches the row. Counting
        # that as the first attempt keeps a stage that always fails at start-up
        # from being retried forever by the recovery cron.
        attempt = 1
        attempts[stage.value] = attempt
    spent = attempt >= MAX_STAGE_ATTEMPTS
    stages = state.stages.model_copy(update={stage.value: StageStatus.FAILED if spent else StageStatus.IN_PROGRESS})
    return _bump(state, stages=stages, last_error=error[:2000], failed=spent)


def mark_run_failed(state: ProgramState, error: str) -> ProgramState:
    """Stop a run whose committed blocks cannot produce a valid draft.

    ``mark_stage_failed`` cannot do this, because the stage that produced the
    bad block is already ``completed``. The failure is terminal on purpose: the
    blocks are deterministic and a retry would fail the same way, so the
    recovery cron must leave the state alone and the methodist starts over.
    """
    if state.failed:
        return state
    return _bump(state, failed=True, last_error=error[:2000])


def apply_block(state: ProgramState, stage: Stage, block: SchemaModel) -> ProgramState:
    """Write one validated block into the document and advance the pipeline.

    The guard is the stage itself: a block for a stage that is not the current
    one is a bug in the caller, not a model mistake, so it raises instead of
    returning a result. Re-applying a completed stage is idempotent, which is
    what makes a retried task safe.
    """
    if state.stage_status(stage) is StageStatus.COMPLETED:
        return state
    if state.current_stage is not stage:
        raise ValueError(f"stage {stage.value} is not the current stage")

    updates: dict = {}
    match stage:
        case Stage.COMPETENCES:
            updates["competencies"] = block.competencies  # type: ignore[attr-defined]
        case Stage.HOURS:
            updates["curriculum"] = block.curriculum  # type: ignore[attr-defined]
        case Stage.SOURCES:
            updates["sources"] = block.sources  # type: ignore[attr-defined]
        case Stage.SUMMARY:
            updates["kind"] = block.kind  # type: ignore[attr-defined]
            updates["title"] = block.title  # type: ignore[attr-defined]
            updates["summary"] = block.summary  # type: ignore[attr-defined]
        case _:  # pragma: no cover - the enum has no other members
            raise ValueError(f"unsupported stage: {stage}")

    stages = state.stages.model_copy(update={stage.value: StageStatus.COMPLETED})
    following = next((item for item in STAGE_ORDER if getattr(stages, item.value) is not StageStatus.COMPLETED), None)
    if following is None:
        return _bump(
            state,
            **updates,
            stages=stages,
            current_stage=None,
            status=ProgramStatus.PENDING_APPROVAL,
            last_error=None,
            failed=False,
        )
    stages = stages.model_copy(update={following.value: StageStatus.IN_PROGRESS})
    return _bump(
        state,
        **updates,
        stages=stages,
        current_stage=following,
        status=ProgramStatus.GENERATING,
        last_error=None,
        failed=False,
    )


def set_known_sources(state: ProgramState, known: set[str]) -> ProgramState:
    """Persist the provenance of the run without bumping the document version.

    Knowing a source is not a document change, so this does not add a version:
    the id list is metadata for the handlers, and the next real block save will
    carry it to the database anyway.
    """
    return state.model_copy(update={"known_source_ids": sorted(known)})


def approve_state(state: ProgramState) -> ProgramState:
    """Freeze a complete draft after the methodist confirmed it."""
    if state.status is ProgramStatus.APPROVED:
        return state
    if state.status is not ProgramStatus.PENDING_APPROVAL:
        raise ValueError("the draft is not ready for approval")
    state.to_draft()
    return _bump(
        state,
        status=ProgramStatus.APPROVED,
        approved_at=_now(),
        current_stage=None,
    )


def mark_finalized(state: ProgramState) -> ProgramState:
    """Record that the rendered draft was written to the chat.

    Kept separate from ``approve_state``: finalisation is a worker action that
    can be retried, while approval is the methodist's decision and must never be
    re-derived from a retry.
    """
    return _bump(state, finalized=True)


#: Phrases that count as an explicit confirmation. Kept as whole-phrase matches so
#: "не подтверждаю" is not mistaken for an approval.
APPROVAL_PHRASES: frozenset[str] = frozenset(
    {
        "подтверждаю",
        "подтверждаю черновик",
        "черновик подтверждаю",
        "утверждаю",
        "утверждаю черновик",
        "черновик утверждаю",
        "согласен",
        "согласна",
        "да, подтверждаю",
        "да подтверждаю",
    }
)


def is_approval_message(text: str) -> bool:
    """Whether the methodist confirmed the draft in plain words."""
    normalized = re.sub(r"[\s.!?]+", " ", text.strip().casefold()).strip()
    return normalized in APPROVAL_PHRASES
