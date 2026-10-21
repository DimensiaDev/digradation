"""The run state of a programme being built in a chat.

The envelope holds the blocks the first task defined in :mod:`app.agentic.models`
plus where the sequential pipeline stands. One stage writes one block and moves
the envelope to the next; :meth:`ProgramState.to_draft` is the gate that opens
only when every block is present and the document passes the cross-block checks.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field

from app.agentic.errors import AnswersDoNotMatch, IncompleteDraft
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
#: and ``materials`` belong to the full document (#5) and stay pending.
STAGE_ORDER: tuple[Stage, ...] = (Stage.COMPETENCES, Stage.HOURS, Stage.SOURCES, Stage.SUMMARY)

#: Enqueues a stage may spend before the run is given up on. The counter moves
#: when a stage is put on the queue, not when a worker picks it up.
MAX_STAGE_ATTEMPTS = 3

#: How long a question the methodist is asked may be.
MAX_QUESTION_LENGTH = 1000

#: How long an answer to a question may be.
MAX_ANSWER_LENGTH = 4000


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


class ClarificationQuestion(BaseModel):
    """One question the methodist has to answer before the programme can start.

    The id is assigned by the backend, not by the model: the client sends the
    answers back by id, so the id has to be stable and has to be one the
    backend issued.
    """

    id: str = Field(description="Идентификатор вопроса, по нему приходят ответы")
    text: str = Field(min_length=1, max_length=MAX_QUESTION_LENGTH, description="Текст вопроса методисту")


class ClarificationAnswer(BaseModel):
    """The methodist's answer to one question."""

    question_id: str = Field(description="Идентификатор вопроса, на который дан ответ")
    text: str = Field(min_length=1, max_length=MAX_ANSWER_LENGTH, description="Текст ответа")


class ProgramState(BaseModel):
    """One programme run: the draft blocks plus where the pipeline stopped."""

    model_config = ConfigDict(frozen=True)

    session_id: uuid.UUID
    run_id: uuid.UUID
    status: ProgramStatus = ProgramStatus.DRAFT
    current_stage: Stage = Stage.COMPETENCES
    stages: StageStatuses = Field(default_factory=StageStatuses)
    attempts: dict[Stage, int] = Field(default_factory=dict)
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
    failed: bool = False
    error: str | None = None
    version: int = 0
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    #: Questions the run has asked. They stay on the envelope after they are
    #: answered, so the stages can read the answers next to the questions.
    questions: list[ClarificationQuestion] = Field(default_factory=list)
    #: The answers, kept next to the questions they belong to.
    answers: list[ClarificationAnswer] = Field(default_factory=list)

    @property
    def terminal(self) -> bool:
        """No task should touch this run again."""
        return self.failed or self.status in (ProgramStatus.APPROVED, ProgramStatus.VERIFIED)

    @property
    def awaiting_approval(self) -> bool:
        """The draft is rendered and the methodist has not answered yet."""
        return self.status is ProgramStatus.PENDING_APPROVAL and self.finalized and not self.failed

    @property
    def awaiting_answers(self) -> bool:
        """The run is on hold until the methodist answers the open questions."""
        return self.status is ProgramStatus.AWAITING_ANSWERS

    @property
    def pending_questions(self) -> list[ClarificationQuestion]:
        """The questions nobody has answered yet."""
        answered = {answer.question_id for answer in self.answers}
        return [question for question in self.questions if question.id not in answered]

    def stage_status(self, stage: Stage) -> StageStatus:
        return getattr(self.stages, stage.value)

    def enqueued(self, stage: Stage) -> Self:
        """Put a stage on the queue and spend one attempt."""
        attempts = {**self.attempts, stage: self.attempts.get(stage, 0) + 1}
        stages = self.stages.model_copy(update={stage.value: StageStatus.PENDING})
        return self._replace(status=ProgramStatus.GENERATING, current_stage=stage, stages=stages, attempts=attempts)

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
        spent = self.attempts.get(stage, 0) >= MAX_STAGE_ATTEMPTS
        return self._replace(stages=stages, failed=spent, error=error)

    def failed_run(self, error: str) -> Self:
        """End the run for a reason no retry can fix."""
        return self._replace(failed=True, error=error)

    def mark_finalized(self) -> Self:
        return self._replace(finalized=True)

    def touched(self) -> Self:
        """Advance the version and the clock without changing the stage.

        Recovery uses it before re-enqueueing a finalize that writes no state of
        its own, so the save is accepted and the retries stay spaced out.
        """
        return self._replace()

    def approved(self) -> Self:
        return self._replace(status=ProgramStatus.APPROVED)

    def with_answers(self, answers: list[ClarificationAnswer]) -> Self:
        """Take the methodist's answers and let the programme start.

        The competence stage is enqueued from here, so its attempt is spent
        now, exactly as it would be if the run had started without questions.
        """
        return self._replace(
            answers=answers,
            status=ProgramStatus.GENERATING,
            current_stage=Stage.COMPETENCES,
            attempts={**self.attempts, Stage.COMPETENCES: self.attempts.get(Stage.COMPETENCES, 0) + 1},
        )

    def with_competences(self, block: CompetencesBlock, sources: list[Source]) -> Self:
        """Save the competence block and the standards its competencies cite."""
        return self._replace(
            kind=block.kind,
            title=block.title,
            competencies=block.competencies,
            sources=self._merged_sources(sources),
        )

    def with_curriculum(self, block: HoursBlock) -> Self:
        return self._replace(curriculum=block.curriculum)

    def with_sources(self, block: SourcesBlock) -> Self:
        """Add the stage's sources to the ones the earlier stages already cite."""
        return self._replace(sources=self._merged_sources(block.sources))

    def with_summary(self, block: SummaryBlock, sources: list[Source]) -> Self:
        """Save the passport and the documents it is based on."""
        return self._replace(summary=block.summary, sources=self._merged_sources(sources))

    def _merged_sources(self, sources: list[Source]) -> list[Source]:
        """Union of the cited sources: first position, latest record."""
        merged = {source.id: source for source in self.sources}
        merged.update({source.id: source for source in sources})
        return list(merged.values())

    def to_draft(self) -> ProgramDraft:
        """Assemble the draft, or explain why the committed blocks cannot be one."""
        problems = [
            problem
            for value, problem in (
                (self.kind, "не определён тип программы"),
                (self.title, "не определено название программы"),
                (self.competencies, "не сформированы компетенции"),
                (self.curriculum, "не сформирован учебный план"),
                (self.summary, "не сформирован паспорт программы"),
            )
            if not value
        ]
        if problems:
            raise IncompleteDraft(problems)

        draft = ProgramDraft(
            kind=self.kind,
            title=self.title,
            summary=self.summary,
            competencies=self.competencies,
            curriculum=self.curriculum,
            sources=self.sources,
        )
        problems = self._consistency_problems(draft)
        if problems:
            raise IncompleteDraft(problems)
        return draft

    def _consistency_problems(self, draft: ProgramDraft) -> list[str]:
        """The checks that need the assembled document rather than one block."""
        outcomes = [
            outcome.id
            for competence in draft.competencies
            for outcome in (*competence.experience, *competence.skills, *competence.knowledge)
        ]
        standards = [item.standard_source_id for item in draft.competencies if item.standard_source_id]
        problems = [
            f"компетенция {item} объявлена дважды"
            for item in _duplicates(competence.id for competence in draft.competencies)
        ]
        problems += [f"результат обучения {item} объявлен дважды" for item in _duplicates(outcomes)]
        problems += self.topic_problems(draft.curriculum, set(outcomes))
        problems += self.curriculum_problems(draft.curriculum)
        problems += self.citation_problems([*draft.summary.basis_source_ids, *standards])
        return problems

    def topic_problems(self, curriculum: Curriculum, declared: set[str]) -> list[str]:
        """Topics that refer to outcomes no competence declares.

        Public so the hours stage can refuse a wrong topic id in the reply that
        produced it, where :meth:`to_draft` could only discard the whole run.
        """
        problems: list[str] = []
        for module in curriculum.modules:
            for topic in module.topics:
                missing = [outcome_id for outcome_id in topic.outcome_ids if outcome_id not in declared]
                if missing:
                    problems.append(
                        f"тема {topic.id} ссылается на необъявленные результаты обучения: {', '.join(missing)}"
                    )
        return problems

    def curriculum_problems(self, curriculum: Curriculum) -> list[str]:
        """The hour totals against the requested volume and the schedule."""
        problems: list[str] = []
        total = curriculum.total_hours
        if self.requested_hours is not None and not math.isclose(total, self.requested_hours, abs_tol=1e-6):
            problems.append(
                f"сумма часов тем {total:g} не совпадает с заявленным объёмом программы {self.requested_hours:g}"
            )
        schedule = curriculum.schedule
        if schedule.days is not None and schedule.hours_per_day is not None:
            capacity = schedule.days * schedule.hours_per_day
            if capacity + 1e-6 < total:
                problems.append(
                    f"расписание {schedule.days}×{schedule.hours_per_day:g} = {capacity:g} ч "
                    f"меньше объёма программы {total:g} ч"
                )
        return problems

    def citation_problems(self, source_ids: Iterable[str]) -> list[str]:
        """Ids the run cites that are not among the sources it committed."""
        known = {source.id for source in self.sources}
        return [
            f"источник {source_id} упомянут, но не входит в список источников"
            for source_id in source_ids
            if source_id not in known
        ]

    def _successor(self, stage: Stage) -> Stage | None:
        if stage not in STAGE_ORDER:
            return None
        index = STAGE_ORDER.index(stage)
        return STAGE_ORDER[index + 1] if index + 1 < len(STAGE_ORDER) else None

    def _replace(self, **changes: Any) -> Self:
        changes.setdefault("version", self.version + 1)
        changes.setdefault("updated_at", datetime.now(UTC))
        return self.model_copy(update=changes)


def new_state(
    session_id: uuid.UUID,
    *,
    requested_hours: float | None,
    requirements: str | None,
    questions: Iterable[str] = (),
) -> ProgramState:
    """Start a run: either generating, or waiting for the methodist's answers.

    Questions are the ones the model could not answer from the methodist's
    message. A run that has them does not generate yet: it waits, and the first
    stage is enqueued only once the answers arrive. A run without them starts
    at once, with the competence stage already on the queue.
    """
    pending = [ClarificationQuestion(id=uuid.uuid4().hex, text=text.strip()) for text in questions if text.strip()]
    if pending:
        return ProgramState(
            session_id=session_id,
            run_id=uuid.uuid4(),
            status=ProgramStatus.AWAITING_ANSWERS,
            current_stage=Stage.COMPETENCES,
            attempts={},
            requested_hours=requested_hours,
            requirements=requirements,
            questions=pending,
        )
    return ProgramState(
        session_id=session_id,
        run_id=uuid.uuid4(),
        status=ProgramStatus.GENERATING,
        current_stage=Stage.COMPETENCES,
        attempts={Stage.COMPETENCES: 1},
        requested_hours=requested_hours,
        requirements=requirements,
    )


def apply_answers(state: ProgramState, answers: Sequence[ClarificationAnswer]) -> ProgramState:
    """Record the methodist's answers and start the programme.

    The answers are checked against the questions the run is actually waiting
    for: every open question needs exactly one answer, and an answer to a
    question nobody asked is refused. A client cannot talk the run into a state
    it was not in.
    """
    if not state.awaiting_answers:
        raise AnswersDoNotMatch("программа не ждёт ответов")
    given = {answer.question_id: answer for answer in answers}
    if len(given) != len(answers):
        raise AnswersDoNotMatch("ответ на один вопрос продублирован")
    expected = {question.id for question in state.pending_questions}
    if set(given) != expected:
        raise AnswersDoNotMatch("ответы не совпадают с заданными вопросами")
    return state.with_answers(list(given.values()))


def _duplicates(items: Iterable[str]) -> list[str]:
    """Values that appear more than once, each reported once, in first-seen order."""
    seen: set[str] = set()
    repeated: list[str] = []
    for item in items:
        if item in seen and item not in repeated:
            repeated.append(item)
        seen.add(item)
    return repeated
