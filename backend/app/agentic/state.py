"""The state of a programme being built in a chat.

The first task defined the document schema (:mod:`app.agentic.models`): the
blocks a methodist reviews and the ``EducationalProgram`` they grow into. This
module wraps that schema in the envelope the sequential cycle needs — the same
``Competence``, ``Curriculum``, ``ProgramSummary`` and ``Source`` models, plus
where the run currently stands.

The envelope is what ``chat_sessions.program_state`` holds and what a worker
mirrors to Redis while a stage works on it. One stage writes one block and moves
the envelope to the next stage; :meth:`ProgramState.to_draft` is the gate that
opens only when every block is present and the assembled draft passes the
cross-block checks no single stage could make.

The state is a value: every transition returns a new instance, so a worker can
checkpoint one state without a later stage mutating the one already committed.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field

from app.agentic.models import (
    Competence,
    Curriculum,
    ProgramDraft,
    ProgramKind,
    ProgramStatus,
    ProgramSummary,
    Source,
    Stage,
    StageStatus,
    StageStatuses,
)

#: Stages that produce a block of the draft, in the order they run. ``assessment``
#: and ``materials`` belong to the full document (#5) and stay pending until then.
STAGE_ORDER: tuple[Stage, ...] = (Stage.COMPETENCES, Stage.HOURS, Stage.SOURCES, Stage.SUMMARY)

#: How many times a stage may be enqueued before the run is given up on. The
#: counter moves when a stage is put on the queue, not when a worker picks it up,
#: so a lost message and a dead worker both spend the same budget.
MAX_STAGE_ATTEMPTS = 3

#: Answers that confirm a rendered draft. Whole phrases only: "не подтверждаю" is
#: a different sentence, and a substring match would approve what it rejects.
_APPROVAL_PHRASES = frozenset(
    {
        "подтверждаю",
        "подтверждаю черновик",
        "подтверждаю программу",
        "утверждаю",
        "утверждаю программу",
        "да, подтверждаю",
        "согласен",
        "согласна",
    }
)

_PUNCTUATION = " \t\r\n.!?,;:—–-«»\"'"


class IncompleteDraft(ValueError):
    """The committed blocks cannot produce a valid draft.

    ``problems`` is what the user is told when the run ends: retrying the same
    blocks would produce the same refusal, so the caller marks the run failed
    instead of spending the stage budget on it.
    """

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


class CompetencesBlock(BaseModel):
    """What the competences stage saves: the programme identity and its outcomes."""

    kind: ProgramKind = Field(description="Тип программы: повышение квалификации или общеразвивающая")
    title: str = Field(min_length=1, max_length=500, description="Название программы")
    competencies: list[Competence] = Field(min_length=1, description="Компетенции с результатами освоения")


class HoursBlock(BaseModel):
    """What the hours stage saves: the curriculum with its hour split."""

    curriculum: Curriculum = Field(description="Учебный план: модули, темы, часы и график")


class SourcesBlock(BaseModel):
    """What the sources stage saves: the documents the draft cites."""

    sources: list[Source] = Field(default_factory=list, description="Источники из результатов search_sources")


class SummaryBlock(BaseModel):
    """What the summary stage saves: the programme passport."""

    summary: ProgramSummary = Field(description="Паспорт программы: цель, аудитория, форма обучения")


class ProgramState(BaseModel):
    """One programme run: the draft blocks plus where the pipeline stopped."""

    model_config = ConfigDict(frozen=True)

    session_id: uuid.UUID
    run_id: uuid.UUID
    status: ProgramStatus = ProgramStatus.DRAFT
    current_stage: Stage = Stage.COMPETENCES
    stages: StageStatuses = Field(default_factory=StageStatuses)
    #: Enqueues per stage, keyed by ``Stage.value``; the failure budget reads it.
    attempts: dict[str, int] = Field(default_factory=dict)
    requested_hours: float | None = Field(default=None, gt=0, description="Заявленный объём программы в часах")
    requirements: str | None = Field(default=None, description="Требования методиста из вступительного сообщения")

    kind: ProgramKind | None = None
    title: str | None = None
    competencies: list[Competence] = Field(default_factory=list)
    curriculum: Curriculum | None = None
    sources: list[Source] = Field(default_factory=list)
    summary: ProgramSummary | None = None

    #: True once the draft has been rendered into the chat and may be approved.
    finalized: bool = False
    #: Terminal failure: a retry would produce the same result.
    failed: bool = False
    error: str | None = None
    version: int = 0
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def terminal(self) -> bool:
        """No task should touch this run again."""
        return self.failed or self.status in (ProgramStatus.APPROVED, ProgramStatus.VERIFIED)

    @property
    def awaiting_approval(self) -> bool:
        """The draft is rendered and the methodist has not answered yet."""
        return self.status is ProgramStatus.PENDING_APPROVAL and self.finalized and not self.failed

    def stage_status(self, stage: Stage) -> StageStatus:
        """Status of one stage of this run."""
        return getattr(self.stages, stage.value)

    def enqueued(self, stage: Stage) -> Self:
        """Put a stage on the queue and spend one attempt of its budget."""
        attempts = {**self.attempts, stage.value: self.attempts.get(stage.value, 0) + 1}
        stages = self.stages.model_copy(update={stage.value: StageStatus.PENDING})
        return self._replace(
            status=ProgramStatus.GENERATING,
            current_stage=stage,
            stages=stages,
            attempts=attempts,
        )

    def started(self, stage: Stage) -> Self:
        """Mark the stage as running; its attempt was counted when it was enqueued."""
        stages = self.stages.model_copy(update={stage.value: StageStatus.IN_PROGRESS})
        return self._replace(status=ProgramStatus.GENERATING, current_stage=stage, stages=stages)

    def completed(self, stage: Stage) -> Self:
        """Mark the stage done and point the state at its successor."""
        stages = self.stages.model_copy(update={stage.value: StageStatus.COMPLETED})
        successor = self._successor(stage)
        status = ProgramStatus.GENERATING if successor is not None else ProgramStatus.PENDING_APPROVAL
        return self._replace(stages=stages, current_stage=successor or stage, status=status)

    def failed_stage(self, stage: Stage, error: str) -> Self:
        """Record a stage failure; the run ends once the stage's budget is spent."""
        stages = self.stages.model_copy(update={stage.value: StageStatus.FAILED})
        spent = self.attempts.get(stage.value, 0) >= MAX_STAGE_ATTEMPTS
        return self._replace(stages=stages, failed=spent, error=error)

    def failed_run(self, error: str) -> Self:
        """End the run for a reason no retry can fix."""
        return self._replace(failed=True, error=error)

    def mark_finalized(self) -> Self:
        """The draft has been rendered into the chat and may be approved."""
        return self._replace(finalized=True)

    def approved(self) -> Self:
        """The methodist confirmed the rendered draft."""
        return self._replace(status=ProgramStatus.APPROVED)

    def to_draft(self) -> ProgramDraft:
        """Assemble the draft, or explain why the committed blocks cannot be one.

        Completeness is checked first, so the message names the missing stage's
        block rather than a field of some half-built model. The assembled draft
        then has to pass the cross-block checks: duplicate identifiers, outcomes
        a topic refers to but nobody declared, hour totals that disagree with the
        requested volume or with the schedule, and citations that are not among
        the draft's sources. Each of them is invisible to a single stage and
        obvious to the document as a whole.
        """
        kind, title, curriculum, summary = self.kind, self.title, self.curriculum, self.summary
        problems: list[str] = []
        if kind is None:
            problems.append("не определён тип программы")
        if not title:
            problems.append("не определено название программы")
        if not self.competencies:
            problems.append("не сформированы компетенции")
        if curriculum is None:
            problems.append("не сформирован учебный план")
        if summary is None:
            problems.append("не сформирован паспорт программы")
        if problems:
            raise IncompleteDraft(problems)

        draft = ProgramDraft(
            kind=kind,
            title=title,
            summary=summary,
            competencies=self.competencies,
            curriculum=curriculum,
            sources=self.sources,
        )
        problems = self._consistency_problems(draft)
        if problems:
            raise IncompleteDraft(problems)
        return draft

    def _consistency_problems(self, draft: ProgramDraft) -> list[str]:
        """The checks that need the assembled document rather than one block."""
        problems = [
            f"компетенция {item} объявлена дважды"
            for item in _duplicates(competence.id for competence in draft.competencies)
        ]

        outcomes = [
            outcome.id
            for competence in draft.competencies
            for outcome in (*competence.experience, *competence.skills, *competence.knowledge)
        ]
        problems += [f"результат обучения {item} объявлен дважды" for item in _duplicates(outcomes)]
        declared = set(outcomes)

        for module in draft.curriculum.modules:
            for topic in module.topics:
                missing = [outcome_id for outcome_id in topic.outcome_ids if outcome_id not in declared]
                if missing:
                    problems.append(
                        f"тема {topic.id} ссылается на необъявленные результаты обучения: {', '.join(missing)}"
                    )

        total = draft.curriculum.total_hours
        if self.requested_hours is not None and not math.isclose(total, self.requested_hours, abs_tol=1e-6):
            problems.append(
                f"сумма часов тем {total:g} не совпадает с заявленным объёмом программы {self.requested_hours:g}"
            )
        schedule = draft.curriculum.schedule
        if schedule.days is not None and schedule.hours_per_day is not None:
            capacity = schedule.days * schedule.hours_per_day
            if capacity + 1e-6 < total:
                problems.append(
                    f"расписание {schedule.days}×{schedule.hours_per_day:g} = {capacity:g} ч "
                    f"меньше объёма программы {total:g} ч"
                )

        source_ids = {source.id for source in draft.sources}
        unknown = [source_id for source_id in draft.summary.basis_source_ids if source_id not in source_ids]
        unknown += [
            competence.standard_source_id
            for competence in draft.competencies
            if competence.standard_source_id is not None and competence.standard_source_id not in source_ids
        ]
        problems += [f"источник {source_id} упомянут, но не входит в список источников" for source_id in unknown]
        return problems

    def _successor(self, stage: Stage) -> Stage | None:
        """The stage that follows, or ``None`` once the draft is complete."""
        if stage not in STAGE_ORDER:
            return None
        index = STAGE_ORDER.index(stage)
        return STAGE_ORDER[index + 1] if index + 1 < len(STAGE_ORDER) else None

    def _replace(self, **changes: Any) -> Self:
        """A new value with the given fields changed and the version advanced."""
        changes.setdefault("version", self.version + 1)
        changes.setdefault("updated_at", datetime.now(UTC))
        return self.model_copy(update=changes)


def new_state(session_id: uuid.UUID, *, requested_hours: float | None, requirements: str | None) -> ProgramState:
    """Start a run: the competence stage is already on the queue."""
    return ProgramState(
        session_id=session_id,
        run_id=uuid.uuid4(),
        status=ProgramStatus.GENERATING,
        current_stage=Stage.COMPETENCES,
        attempts={Stage.COMPETENCES.value: 1},
        requested_hours=requested_hours,
        requirements=requirements,
    )


def is_approval_message(text: str) -> bool:
    """True when the message is exactly one of the confirmation phrases."""
    normalised = " ".join(text.lower().strip(_PUNCTUATION).split())
    return normalised in _APPROVAL_PHRASES


def _duplicates(items: Any) -> list[str]:
    """Values that appear more than once, each reported once, in first-seen order."""
    seen: set[str] = set()
    repeated: list[str] = []
    for item in items:
        if item in seen and item not in repeated:
            repeated.append(item)
        seen.add(item)
    return repeated
