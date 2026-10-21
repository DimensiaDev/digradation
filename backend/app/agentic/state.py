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
    AssessmentPlan,
    Competence,
    Curriculum,
    Materials,
    ProgramDraft,
    ProgramKind,
    ProgramStatus,
    ProgramSummary,
    Source,
    Stage,
    StageStatus,
    StageStatuses,
)

#: The stages the methodist approves before the content is written: the goal, the
#: competences and the hours are the skeleton of the programme, and nothing is
#: generated from it until the methodist has said yes.
SKELETON_ORDER: tuple[Stage, ...] = (Stage.COMPETENCES, Stage.HOURS, Stage.SOURCES, Stage.SUMMARY)

#: The stages that fill the programme in once the skeleton is approved.
CONTENT_ORDER: tuple[Stage, ...] = (Stage.ASSESSMENT, Stage.MATERIALS)

#: The stage whose completion means the skeleton is ready for the methodist.
SKELETON_LAST = SKELETON_ORDER[-1]

#: Stages that produce a block of the document, in the order they run.
STAGE_ORDER: tuple[Stage, ...] = SKELETON_ORDER + CONTENT_ORDER

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


class AssessmentBlock(BaseModel):
    """What the assessment stage saves: the means of checking the outcomes."""

    assessment: AssessmentPlan = Field(description="Оценочные средства: тесты, задания, текущий контроль, аттестация")


class MaterialsBlock(BaseModel):
    """What the materials stage saves: the lecture plans and the slides."""

    materials: Materials = Field(description="Планы лекций и слайды")


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


class ProgramCheck(BaseModel):
    """One check of the programme and what it found."""

    name: str = Field(description="Что проверялось")
    ok: bool = Field(description="Проверка пройдена")
    problems: list[str] = Field(default_factory=list, description="Что не сошлось")
    details: dict[str, Any] = Field(default_factory=dict, description="Числа проверки")


class ProgramReport(BaseModel):
    """The checks the programme passed, for the methodist to read.

    The same rules the stages are held to, reported once: the hours, the
    coverage of the outcomes, the sources and the completeness of the sections.
    """

    ok: bool = Field(default=True, description="Все проверки пройдены")
    checks: list[ProgramCheck] = Field(default_factory=list, description="Проверки по порядку")


class ProgramDocument(BaseModel):
    """The whole programme: the draft the methodist approved and the content.

    The draft is the skeleton — the goal, the competences and the hours — and
    the content is what was written from it once it was approved. The export
    takes the document, so a document without a content is still a document.
    """

    draft: ProgramDraft
    assessment: AssessmentPlan | None = None
    materials: Materials | None = None


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
    #: The content of the programme: the means of checking the outcomes and the
    #: lecture material. They are written after the methodist has approved the
    #: skeleton, and they are not part of the draft they approve.
    assessment: AssessmentPlan | None = None
    materials: Materials | None = None

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
        """Mark the stage done and point the state at its successor.

        The skeleton ends with a programme that waits for the methodist, and
        the content ends with a programme that is complete.
        """
        stages = self.stages.model_copy(update={stage.value: StageStatus.COMPLETED})
        successor = self._successor(stage)
        if stage == SKELETON_LAST:
            # The skeleton is ready. The content is written from it only after
            # the methodist has approved it, so the run waits here.
            status = ProgramStatus.PENDING_APPROVAL
        elif successor is not None:
            status = ProgramStatus.GENERATING
        else:
            status = ProgramStatus.APPROVED
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

    def with_assessment(self, block: AssessmentBlock) -> Self:
        """Save the means of checking the outcomes."""
        return self._replace(assessment=block.assessment)

    def with_materials(self, block: MaterialsBlock) -> Self:
        """Save the lecture plans and the slides."""
        return self._replace(materials=block.materials)

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
        outcomes = self.declared_outcomes()
        standards = [item.standard_source_id for item in draft.competencies if item.standard_source_id]
        problems = [
            f"компетенция {item} объявлена дважды"
            for item in _duplicates(competence.id for competence in draft.competencies)
        ]
        problems += [f"результат обучения {item} объявлен дважды" for item in _duplicates(outcomes)]
        problems += self.topic_problems(draft.curriculum, set(outcomes))
        problems += self.uncovered_outcome_problems(draft.curriculum, set(outcomes))
        problems += self.curriculum_problems(draft.curriculum)
        problems += self.citation_problems([*draft.summary.basis_source_ids, *standards])
        if self.assessment is not None:
            problems += self.assessment_problems(self.assessment, draft.curriculum)
        return problems

    def declared_outcomes(self) -> list[str]:
        """Every learning outcome the competences declare."""
        return [
            outcome.id
            for competence in self.competencies
            for outcome in (*competence.experience, *competence.skills, *competence.knowledge)
        ]

    def to_document(self) -> ProgramDocument:
        """The whole programme: the approved draft and the content written from it."""
        return ProgramDocument(
            draft=self.to_draft(),
            assessment=self.assessment,
            materials=self.materials,
        )

    def report(self) -> ProgramReport:
        """The checks of the programme, for the methodist to read.

        The same rules the stages are held to, reported once and in one place:
        the hours, the coverage of the outcomes, the sources and the sections
        that are filled. It works on a half-finished run — the checks that
        cannot be made yet are simply not reported.
        """
        checks = [
            self._hours_check(),
            self._coverage_check(),
            self._sources_check(),
            self._sections_check(),
        ]
        return ProgramReport(ok=all(check.ok for check in checks), checks=checks)

    def _hours_check(self) -> ProgramCheck:
        """The hours add up to the volume that was asked for."""
        if self.curriculum is None:
            return ProgramCheck(name="часы", ok=False, problems=["учебный план ещё не собран"])
        curriculum = self.curriculum
        problems = self.curriculum_problems(curriculum)
        return ProgramCheck(
            name="часы",
            ok=not problems,
            problems=problems,
            details={
                "заявлено": self.requested_hours,
                "всего": curriculum.total_hours,
                "темы": curriculum.topic_hours,
                "итоговая аттестация": curriculum.attestation_hours,
                "аудиторных": curriculum.contact_hours,
                "самостоятельной работы": curriculum.self_study_hours,
            },
        )

    def _coverage_check(self) -> ProgramCheck:
        """Every outcome is taught by a topic and checked by two means."""
        problems: list[str] = []
        outcomes = self.declared_outcomes()
        covered = 0
        checked_by_two = 0
        if self.curriculum is not None:
            problems += self.uncovered_outcome_problems(self.curriculum, set(outcomes))
            taught = {
                outcome_id
                for module in self.curriculum.modules
                for topic in module.topics
                for outcome_id in topic.outcome_ids
            }
            covered = sum(1 for outcome in outcomes if outcome in taught)
        if self.assessment is not None and self.curriculum is not None:
            problems += [
                problem
                for problem in self.assessment_problems(self.assessment, self.curriculum)
                if "средствами" in problem
            ]
            means = self._means_per_outcome()
            checked_by_two = sum(1 for count in means.values() if count >= 2)
        return ProgramCheck(
            name="покрытие",
            ok=not problems,
            problems=problems,
            details={
                "результатов обучения": len(outcomes),
                "раскрыто в темах": covered,
                "проверяется двумя средствами": checked_by_two,
            },
        )

    def _means_per_outcome(self) -> dict[str, int]:
        """How many means of checking each outcome has."""
        counts: dict[str, int] = dict.fromkeys(self.declared_outcomes(), 0)
        assessment = self.assessment
        if assessment is None:
            return counts
        for outcome_ids in (
            [question.outcome_ids for question in assessment.questions]
            + [task.outcome_ids for task in assessment.seminar_tasks]
            + [tool.outcome_ids for tool in assessment.tools]
        ):
            for outcome_id in outcome_ids:
                if outcome_id in counts:
                    counts[outcome_id] += 1
        return counts

    def _sources_check(self) -> ProgramCheck:
        """The documents the programme cites exist, and the rest are marked."""
        cited = {item.standard_source_id for item in self.competencies if item.standard_source_id}
        if self.summary is not None:
            cited |= set(self.summary.basis_source_ids)
        problems = self.citation_problems(cited)
        return ProgramCheck(
            name="источники",
            ok=not problems,
            problems=problems,
            details={
                "всего источников": len(self.sources),
                "подтверждено": len(self.sources),
                "не подтверждено": len(problems),
            },
        )

    def _sections_check(self) -> ProgramCheck:
        """Every section of the programme is filled."""
        sections = {
            "компетенции": bool(self.competencies),
            "учебный план": self.curriculum is not None,
            "паспорт": self.summary is not None,
            "источники": bool(self.sources),
            "оценочные средства": self.assessment is not None,
            "материалы": self.materials is not None,
        }
        problems = [f"раздел «{name}» не заполнен" for name, filled in sections.items() if not filled]
        return ProgramCheck(
            name="разделы",
            ok=not problems,
            problems=problems,
            details=dict(sections),
        )

    def assessment_problems(self, assessment: AssessmentPlan, curriculum: Curriculum) -> list[str]:
        """The assessment has to check the programme, and only the programme.

        Every means of checking names outcomes that exist and a form of control
        that exists, every outcome of the programme is checked by at least two
        means, and the final attestation takes the hours the plan set aside
        for it.
        """
        problems: list[str] = []
        declared = set(self.declared_outcomes())
        topics = {topic.id for module in curriculum.modules for topic in module.topics}
        tools = {tool.id for tool in assessment.tools}
        checked: dict[str, int] = dict.fromkeys(declared, 0)

        def note(outcome_ids: list[str], what: str) -> None:
            unknown = [item for item in outcome_ids if item not in declared]
            if unknown:
                problems.append(f"{what} ссылается на необъявленные результаты обучения: {', '.join(unknown)}")
            for outcome_id in outcome_ids:
                if outcome_id in checked:
                    checked[outcome_id] += 1

        for question in assessment.questions:
            if question.tool_id not in tools:
                problems.append(f"вопрос {question.id} ссылается на несуществующую форму контроля {question.tool_id}")
            note(question.outcome_ids, f"вопрос {question.id}")
        for task in assessment.seminar_tasks:
            if task.tool_id not in tools:
                problems.append(f"задание {task.id} ссылается на несуществующую форму контроля {task.tool_id}")
            if task.topic_id not in topics:
                problems.append(f"задание {task.id} ссылается на несуществующую тему {task.topic_id}")
            note(task.outcome_ids, f"задание {task.id}")
        for tool in assessment.tools:
            missing = [item for item in tool.topic_ids if item not in topics]
            if missing:
                problems.append(f"форма контроля {tool.id} ссылается на несуществующие темы: {', '.join(missing)}")
            note(tool.outcome_ids, f"форма контроля {tool.id}")
        # The final attestation is the programme's conclusion, not one of the
        # means: its outcomes have to exist, but they do not count towards the
        # two means every outcome has to be checked by.
        unknown = [item for item in assessment.final.outcome_ids if item not in declared]
        if unknown:
            problems.append(f"итоговая аттестация ссылается на необъявленные результаты обучения: {', '.join(unknown)}")

        problems += [
            f"результат обучения {outcome_id} проверяется только {count} средствами — нужно хотя бы два"
            for outcome_id, count in sorted(checked.items())
            if count < 2
        ]
        if not math.isclose(assessment.final.hours, curriculum.attestation_hours, abs_tol=1e-6):
            problems.append(
                f"итоговая аттестация занимает {assessment.final.hours:g} ч, "
                f"а в учебном плане на неё отведено {curriculum.attestation_hours:g} ч"
            )
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

    def uncovered_outcome_problems(self, curriculum: Curriculum, declared: set[str]) -> list[str]:
        """Declared outcomes that no topic of the plan reveals.

        The other direction of the same rule: a topic may not name an outcome
        that does not exist, and an outcome nobody teaches is not a result of
        the programme. Public so the hours stage can refuse the plan in the
        reply that produced it.
        """
        covered = {
            outcome_id for module in curriculum.modules for topic in module.topics for outcome_id in topic.outcome_ids
        }
        return [
            f"результат обучения {outcome_id} не раскрыт ни в одной теме" for outcome_id in sorted(declared - covered)
        ]

    def curriculum_problems(self, curriculum: Curriculum) -> list[str]:
        """The hour totals against the requested volume and the schedule."""
        problems: list[str] = []
        total = curriculum.total_hours
        if self.requested_hours is not None and not math.isclose(total, self.requested_hours, abs_tol=1e-6):
            problems.append(
                f"объём программы {total:g} ч (темы {curriculum.topic_hours:g} ч "
                f"+ итоговая аттестация {curriculum.attestation_hours:g} ч) "
                f"не совпадает с заявленным объёмом {self.requested_hours:g} ч"
            )
        problems += self.module_hours_problems(curriculum)
        problems += self.schedule_problems(curriculum)
        problems += self.delivery_problems(curriculum)
        return problems

    def module_hours_problems(self, curriculum: Curriculum) -> list[str]:
        """A module states its own volume, and it has to add up."""
        return [
            f"модуль {module.id} заявлен на {module.hours:g} ч, а его темы дают {module.topic_hours:g} ч"
            for module in curriculum.modules
            if not math.isclose(module.hours, module.topic_hours, abs_tol=1e-6)
        ]

    def schedule_problems(self, curriculum: Curriculum) -> list[str]:
        """The schedule has to hold the volume it is given."""
        problems: list[str] = []
        total = curriculum.total_hours
        schedule = curriculum.schedule
        if schedule.days is not None and schedule.hours_per_day is not None:
            capacity = schedule.days * schedule.hours_per_day
            if capacity + 1e-6 < total:
                problems.append(
                    f"расписание {schedule.days}×{schedule.hours_per_day:g} = {capacity:g} ч "
                    f"меньше объёма программы {total:g} ч"
                )
        if (
            schedule.max_hours_per_day is not None
            and schedule.hours_per_day is not None
            and schedule.hours_per_day > schedule.max_hours_per_day + 1e-6
        ):
            problems.append(
                f"дневная нагрузка {schedule.hours_per_day:g} ч превышает "
                f"заявленный предел {schedule.max_hours_per_day:g} ч в день"
            )
        return problems

    def delivery_problems(self, curriculum: Curriculum) -> list[str]:
        """The remote share of the hours has to be a share of the volume."""
        if curriculum.distance_hours is None:
            return []
        total = curriculum.total_hours
        if curriculum.distance_hours > total + 1e-6:
            return [f"дистанционных часов {curriculum.distance_hours:g} больше, " f"чем объём программы {total:g} ч"]
        return []

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
