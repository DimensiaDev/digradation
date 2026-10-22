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

from app.agentic import sources
from app.agentic.errors import AnswersDoNotMatch, IncompleteDraft
from app.agentic.models import (
    ApprovalInfo,
    AssessmentPlan,
    Competence,
    Curriculum,
    Materials,
    ProgramDraft,
    ProgramFormat,
    ProgramKind,
    ProgramStatus,
    ProgramSummary,
    Source,
    SourceKind,
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

#: What a revision of a stage invalidates: the block that is dropped and the
#: stage that writes it. A block that is written from another one may not
#: outlive it — the curriculum names the outcomes the competences declare,
#: the content is written from the hours, and the passport rests on the
#: sources.
REVISION_INVALIDATES: dict[Stage, tuple[tuple[str, Stage], ...]] = {
    Stage.COMPETENCES: (("curriculum", Stage.HOURS), ("assessment", Stage.ASSESSMENT), ("materials", Stage.MATERIALS)),
    Stage.HOURS: (("assessment", Stage.ASSESSMENT), ("materials", Stage.MATERIALS)),
    Stage.SOURCES: (("summary", Stage.SUMMARY),),
    Stage.SUMMARY: (),
    Stage.ASSESSMENT: (),
    Stage.MATERIALS: (),
}

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
    format: ProgramFormat = Field(
        default=ProgramFormat.COURSE,
        description="Формат: обычный курс или профессиональная программа в форме стажировки",
    )
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


class Revision(BaseModel):
    """A change the methodist asked for in one part of the programme.

    The revision names the stage to redo and what to change in it, so the
    stage can be run again with the methodist's notes instead of the whole
    programme being rebuilt. It also names who asked for it: a change is a
    decision of a person, and the record is what makes it one.
    """

    stage: Stage = Field(description="Блок программы, который нужно переделать")
    notes: str = Field(min_length=1, max_length=2000, description="Что именно изменить")
    requested_by: uuid.UUID | None = Field(default=None, description="Кто попросил переделку")
    requested_at: datetime | None = Field(default=None, description="Когда попросили переделку")


class ProgramCheck(BaseModel):
    """One check of the programme and what it found."""

    name: str = Field(description="Что проверялось")
    ok: bool = Field(description="Проверка пройдена")
    problems: list[str] = Field(default_factory=list, description="Что не сошлось")
    details: dict[str, Any] = Field(default_factory=dict, description="Числа проверки")


class ProgramExports(BaseModel):
    """The files the programme was exported to.

    The exports are attachments of the chat, so the client fetches them with
    the attachment route it already uses for a file the methodist uploaded.
    """

    document_attachment_id: int | None = Field(default=None, description="Вложение с документом программы")
    slides_attachment_id: int | None = Field(default=None, description="Вложение со слайдами программы")
    json_attachment_id: int | None = Field(default=None, description="Вложение с данными программы в JSON")


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

    The approval and the report travel with it: the approval names who signed
    the order and the report is the self-check the methodist reads before
    signing.
    """

    draft: ProgramDraft
    assessment: AssessmentPlan | None = None
    materials: Materials | None = None
    approval: ApprovalInfo = Field(default_factory=ApprovalInfo, description="Реквизиты приказа и подписант")
    report: ProgramReport | None = Field(default=None, description="Отчёт автопроверки")


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
    #: The change the methodist asked for, waiting to be carried out.
    revision: Revision | None = None
    #: The last change the methodist asked for, kept after it was carried out:
    #: the pending field is cleared when the work is done, and the record of
    #: who asked for it has to outlive the request.
    last_revision: Revision | None = None
    #: The files the programme was exported to, once it was complete.
    exports: ProgramExports | None = None
    #: Who approved the skeleton and when. The approval is the methodist's own
    #: decision, so the record names the person who made it.
    approved_by: uuid.UUID | None = None
    approved_at: datetime | None = None
    #: Whether the methodist has approved the skeleton that is on the table.
    #: The content is written from an approved skeleton and from nothing else,
    #: so this is what tells a revision of the content from a revision of the
    #: skeleton.
    skeleton_approved: bool = False

    #: Формат программы: обычный курс или профессиональная стажировка.
    format: ProgramFormat = ProgramFormat.COURSE
    #: Реквизиты приказа и подписант, которые методист заполняет вручную.
    approval: ApprovalInfo = Field(default_factory=ApprovalInfo)
    #: Ограничения из запроса методиста, если он их назвал.
    expected_test_questions: int | None = Field(
        default=None, gt=0, description="Сколько вопросов в тесте просил методист"
    )
    min_practice_share: float | None = Field(
        default=None, ge=0, le=1, description="Минимальная доля практических часов из общего объёма"
    )
    #: Расхождение между пожеланием заказчика и реальным результатом.
    expectations: str | None = Field(
        default=None, max_length=4000, description="Что заказчик попросил сверх того, что даёт объём"
    )
    #: Несогласованности в приложенном образце, найденные при разборе.
    sample_issues: list[str] = Field(default_factory=list, description="Ошибки в образце программы")
    #: Тайминги прогона: когда он начался и когда закончился, сколько шла каждая стадия.
    started_at: datetime | None = Field(default=None, description="Когда прогон начался")
    finished_at: datetime | None = Field(default=None, description="Когда прогон завершился")
    stage_durations: dict[Stage, float] = Field(
        default_factory=dict, description="Секунды по каждой стадии, когда она завершилась"
    )

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
        """Mark the stage as running; its attempt was counted when it was enqueued.

        The moment the run first starts is kept with it: a programme that was
        never started has no ``started_at``, and a retried stage does not reset it.
        """
        stages = self.stages.model_copy(update={stage.value: StageStatus.IN_PROGRESS})
        return self._replace(
            status=ProgramStatus.GENERATING,
            current_stage=stage,
            stages=stages,
            started_at=self.started_at or datetime.now(UTC),
        )

    def completed(self, stage: Stage) -> Self:
        """Mark the stage done and point the state at its successor.

        The skeleton ends with a programme that waits for the methodist, and
        the content ends with a programme that is complete. The reason a
        previous attempt gave is cleared with it: a run that recovered is not
        a run that failed.

        The duration of the stage is measured from the moment it was started:
        ``updated_at`` is the last write, and ``started`` is the write that
        made the stage in progress, so the difference is the stage's own time.
        """
        stages = self.stages.model_copy(update={stage.value: StageStatus.COMPLETED})
        successor = self._successor(stage)
        if stage == SKELETON_LAST:
            status = ProgramStatus.PENDING_APPROVAL
        elif successor is not None:
            status = ProgramStatus.GENERATING
        else:
            status = ProgramStatus.APPROVED
        now = datetime.now(UTC)
        duration = max(0.0, (now - self.updated_at).total_seconds())
        durations = {**self.stage_durations, stage: self.stage_durations.get(stage, 0.0) + duration}
        finished_at = self.finished_at if self.finished_at else (now if status is ProgramStatus.APPROVED else None)
        return self._replace(
            stages=stages,
            current_stage=successor or stage,
            status=status,
            error=None,
            stage_durations=durations,
            finished_at=finished_at,
        )

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

    def approved(self, by: uuid.UUID | None = None) -> Self:
        """The methodist approved the skeleton: the content may be written.

        The run keeps working rather than becoming approved: ``approved`` is
        the state of a finished programme, and the content is not written yet.
        The run points at the first part of the content that is still missing.

        ``by`` is the person who made the decision. It is recorded with the
        moment of the approval, because the approval is the one step of the
        pipeline nobody but the methodist may take.
        """
        pending = next(
            (item for item in CONTENT_ORDER if self.stage_status(item) is not StageStatus.COMPLETED),
            None,
        )
        if pending is None:
            # Nothing of the content is missing: the skeleton that was revised
            # was the only thing left, and the programme is complete again.
            return self._replace(
                status=ProgramStatus.APPROVED,
                approved_by=by,
                approved_at=datetime.now(UTC),
                skeleton_approved=True,
            )
        return self._replace(
            status=ProgramStatus.GENERATING,
            current_stage=pending,
            approved_by=by,
            approved_at=datetime.now(UTC),
            skeleton_approved=True,
        )

    def request_revision(self, stage: Stage, notes: str, by: uuid.UUID | None = None) -> Self:
        """Ask for one part of the programme to be redone.

        The stage goes back to pending, the run starts working again, and the
        draft is rendered once more when the stage is done. A revision does
        not spend an attempt: a change the methodist asked for is not a
        failed attempt, and the budget is there for the agent's own failures.

        The parts that are written from the revised one are dropped, because a
        block that stays while its foundation moves is a block that points at
        something that is no longer there: the curriculum names the outcomes
        the competences declare, the content is written from the hours, and
        the passport rests on the sources. What was dropped is written again.

        The exports go with the block they were written from: they describe a
        programme that is no longer the one on the table, and a client that
        fetches them would download the old document and the old slides.

        A revised skeleton is not approved any more, so the approval that
        released the content is withdrawn with it.
        """
        stages = self.stages.model_copy(update={stage.value: StageStatus.PENDING})
        changes: dict[str, Any] = {}
        attempts = dict(self.attempts)
        attempts[stage] = 0
        for block, written_by in REVISION_INVALIDATES.get(stage, ()):
            changes[block] = None
            stages = stages.model_copy(update={written_by.value: StageStatus.PENDING})
            attempts[written_by] = 0
        withdrawn: dict[str, Any] = {}
        if stage in SKELETON_ORDER:
            # The skeleton is being rewritten, so the methodist has to see it
            # again, and the approval that released the content is withdrawn.
            withdrawn = {"approved_by": None, "approved_at": None, "skeleton_approved": False}
        return self._replace(
            revision=Revision(stage=stage, notes=notes, requested_by=by, requested_at=datetime.now(UTC)),
            stages=stages,
            attempts=attempts,
            status=ProgramStatus.GENERATING,
            current_stage=stage,
            finalized=False,
            exports=None,
            **changes,
            **withdrawn,
        )

    def with_exports(self, exports: ProgramExports) -> Self:
        """The files the programme was exported to."""
        return self._replace(exports=exports)

    def revision_completed(self, stage: Stage) -> Self:
        """The redone stage is done; the programme is back where it was.

        A revision reruns one stage and drops the parts that were written
        from it. A redone skeleton waits for the methodist again; a redone part
        of the content leaves the programme complete, and the exports are
        written again.

        The skeleton is only shown to the methodist when it is whole. A
        revision of the competences resets the hours, and a revision of the
        sources resets the passport, so the draft the methodist approves has to
        be the one that follows those stages again — otherwise the approval
        releases the content of a programme whose skeleton has a hole in it.

        The completed revision is kept as the last one, because the pending
        field is cleared when the work is done and the record of who asked
        for it has to outlive the request.
        """
        stages = self.stages.model_copy(update={stage.value: StageStatus.COMPLETED})
        pending = [item for item in STAGE_ORDER if getattr(stages, item.value) is not StageStatus.COMPLETED]
        if stage in CONTENT_ORDER:
            # The content was redone: the programme is complete when nothing of
            # it is missing, and its exports are written again.
            status = ProgramStatus.APPROVED if not pending else ProgramStatus.GENERATING
            current = pending[0] if pending else stage
        else:
            # The skeleton was redone. The parts that were written from it are
            # written again first, and the methodist sees the whole draft.
            skeleton_pending = [
                item for item in SKELETON_ORDER if getattr(stages, item.value) is not StageStatus.COMPLETED
            ]
            status = ProgramStatus.PENDING_APPROVAL if not skeleton_pending else ProgramStatus.GENERATING
            current = skeleton_pending[0] if skeleton_pending else stage
        return self._replace(
            stages=stages,
            status=status,
            current_stage=current,
            revision=None,
            last_revision=self.revision,
        )

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
            started_at=self.started_at or datetime.now(UTC),
        )

    def with_competences(self, block: CompetencesBlock, sources: list[Source]) -> Self:
        """Save the competence block and the standards its competencies cite."""
        return self._replace(
            kind=block.kind,
            format=block.format,
            title=block.title,
            competencies=block.competencies,
            sources=self._merged_sources(sources),
        )

    def with_curriculum(self, block: HoursBlock) -> Self:
        return self._replace(curriculum=block.curriculum)

    def with_sources(self, block: SourcesBlock) -> Self:
        """Add the stage's sources to the ones the earlier stages already cite."""
        return self._replace(sources=self._merged_sources([_confirmed(source) for source in block.sources]))

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
            format=self.format,
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
        problems += self._issued_document_problems(draft.summary)
        if self.assessment is not None:
            problems += self.assessment_problems(self.assessment, draft.curriculum)
        return problems

    def _issued_document_problems(self, summary: ProgramSummary) -> list[str]:
        """The document the programme issues, against the programme's kind.

        A professional programme issues an удостоверение and a general one a
        сертификат; a document that says the other thing would be signed and
        handed out, so it is refused where the draft is assembled.
        """
        issued = summary.issued_document.lower()
        problems: list[str] = []
        if self.kind is ProgramKind.PROFESSIONAL_DEVELOPMENT and "удостоверение" not in issued:
            problems.append(
                f"повышение квалификации выдаёт удостоверение, " f"а в паспорте указано «{summary.issued_document}»"
            )
        if self.kind is ProgramKind.GENERAL_DEVELOPMENT and "сертификат" not in issued:
            problems.append(
                f"общеразвивающая программа выдаёт сертификат, " f"а в паспорте указано «{summary.issued_document}»"
            )
        return problems

    def declared_outcomes(self) -> list[str]:
        """Every learning outcome the competences declare."""
        return [
            outcome.id
            for competence in self.competencies
            for outcome in (*competence.experience, *competence.skills, *competence.knowledge)
        ]

    def to_document(self) -> ProgramDocument:
        """The whole programme: the approved draft and the content written from it.

        The approval and the report travel with it: the first names who signed
        the order and the second is the self-check the methodist reads before
        signing.
        """
        return ProgramDocument(
            draft=self.to_draft(),
            assessment=self.assessment,
            materials=self.materials,
            approval=self.approval,
            report=self.report(),
        )

    def report(self) -> ProgramReport:
        """The checks of the programme, for the methodist to read.

        The same rules the stages are held to, reported once and in one place:
        the hours, the coverage of the outcomes, the sources, the sections that
        are filled, the limits the methodist named and the materials that cover
        at least one module. It works on a half-finished run — the checks that
        cannot be made yet are simply not reported.
        """
        checks = [
            self._hours_check(),
            self._coverage_check(),
            self._sources_check(),
            self._sections_check(),
            self._request_constraints_check(),
            self._materials_check(),
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
            problems += self.assessment_coverage_problems(self.assessment, self.curriculum)
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
        unconfirmed = [source for source in self.sources if not source.confirmed]
        problems += [f"источник {source.id} не подтверждён каталогом — его нужно проверить" for source in unconfirmed]
        return ProgramCheck(
            name="источники",
            ok=not problems,
            problems=problems,
            details={
                "всего источников": len(self.sources),
                "подтверждено": len(self.sources) - len(unconfirmed),
                "не подтверждено": len(unconfirmed),
            },
        )

    def _sections_check(self) -> ProgramCheck:
        """Every section of the programme is filled.

        A section counts as filled when it holds something, not when it exists:
        an assessment without a question and a set of materials without a
        slide are empty sections that a report would otherwise call filled.

        A professional programme additionally needs a professional standard a
        competence rests on, and an internship needs the staff that runs it:
        the standard is what makes the programme professional and the
        supervisor is what makes an internship an internship.
        """
        assessment = self.assessment
        materials = self.materials
        sections = {
            "компетенции": bool(self.competencies),
            "учебный план": self.curriculum is not None and bool(self.curriculum.modules),
            "паспорт": self.summary is not None and bool(self.summary.goal),
            "источники": bool(self.sources),
            "оценочные средства": assessment is not None
            and bool(assessment.questions or assessment.seminar_tasks or assessment.tools),
            "материалы": materials is not None and bool(materials.lecture_plans or materials.slide_decks),
        }
        if self.kind is ProgramKind.PROFESSIONAL_DEVELOPMENT:
            sections["профессиональный стандарт"] = any(
                competence.standard_source_id for competence in self.competencies
            )
        if self.format is ProgramFormat.INTERNSHIP:
            sections["кадровое обеспечение"] = materials is not None and bool(materials.staffing)
        problems = [f"раздел «{name}» не заполнен" for name, filled in sections.items() if not filled]
        return ProgramCheck(
            name="разделы",
            ok=not problems,
            problems=problems,
            details=dict(sections),
        )

    def _request_constraints_check(self) -> ProgramCheck:
        """The limits the methodist named when the programme was started.

        A test that was asked for with ten questions and comes with five, or a
        programme that asked for half its hours as practice and has a quarter,
        is a programme that does not do what it was asked to. The check reports
        both, so a methodist sees what to ask for and a reviewer sees what was
        promised and what was written.
        """
        problems: list[str] = []
        details: dict[str, Any] = {}

        if self.expected_test_questions is not None:
            actual = len(self.assessment.questions) if self.assessment is not None else 0
            details["вопросов в тесте"] = actual
            details["заявлено вопросов"] = self.expected_test_questions
            if actual != self.expected_test_questions:
                problems.append(f"в тесте {actual} вопросов, а методист просил {self.expected_test_questions}")

        if self.min_practice_share is not None and self.curriculum is not None:
            total = self.curriculum.total_hours
            practice = sum(topic.hours.practice for module in self.curriculum.modules for topic in module.topics)
            share = practice / total if total else 0.0
            details["доля практики"] = round(share, 2)
            details["заявлено минимум"] = self.min_practice_share
            if share + 1e-6 < self.min_practice_share:
                problems.append(f"доля практики {share:.0%} меньше заявленных {self.min_practice_share:.0%}")

        return ProgramCheck(name="требования", ok=not problems, problems=problems, details=details)

    def _materials_check(self) -> ProgramCheck:
        """At least one module is fully covered by lecture plans and slides.

        The criterion asks for the slides of at least one module, not one deck
        somewhere in the programme: every lecture of the module needs both its
        plan and its deck, or the module is not complete.
        """
        if self.curriculum is None or self.materials is None:
            return ProgramCheck(
                name="материалы",
                ok=False,
                problems=["учебный план или материалы ещё не собраны"],
            )
        plans = {plan.topic_id for plan in self.materials.lecture_plans}
        decks = {deck.topic_id for deck in self.materials.slide_decks}
        covered: list[str] = []
        for module in self.curriculum.modules:
            lecture_topics = [topic.id for topic in module.topics if topic.hours.lecture > 0]
            if not lecture_topics:
                continue
            if all(topic_id in plans and topic_id in decks for topic_id in lecture_topics):
                covered.append(module.id)
        modules_with_lectures = sum(
            1 for module in self.curriculum.modules if any(topic.hours.lecture > 0 for topic in module.topics)
        )
        details = {"модулей покрыто": len(covered), "модулей с лекциями": modules_with_lectures}
        if not covered and modules_with_lectures > 0:
            return ProgramCheck(
                name="материалы",
                ok=False,
                problems=["ни один модуль не покрыт планами лекций и слайдами полностью"],
                details=details,
            )
        return ProgramCheck(name="материалы", ok=True, problems=[], details=details)

    def assessment_problems(self, assessment: AssessmentPlan, curriculum: Curriculum) -> list[str]:
        """The assessment has to check the programme, and only the programme.

        Every means of checking names outcomes that exist and a form of control
        that exists, every outcome of the programme is checked by at least two
        means, and the final attestation takes the hours the plan set aside
        for it.
        """
        problems = self.assessment_coverage_problems(assessment, curriculum)
        problems += self.assessment_binding_problems(assessment, curriculum)
        return problems

    def assessment_binding_problems(self, assessment: AssessmentPlan, curriculum: Curriculum) -> list[str]:
        """The means of checking that point at something the programme has not.

        A question, a seminar task and a form of control have to name outcomes
        the competences declare and topics and forms of control the programme
        actually has. The final attestation has to name declared outcomes too,
        but it is the programme's conclusion and not one of the means, so it
        does not count towards the two.
        """
        problems: list[str] = []
        declared = set(self.declared_outcomes())
        topics = {topic.id for module in curriculum.modules for topic in module.topics}
        tools = {tool.id for tool in assessment.tools}

        def unknown(outcome_ids: list[str], what: str) -> None:
            missing = [item for item in outcome_ids if item not in declared]
            if missing:
                problems.append(f"{what} ссылается на необъявленные результаты обучения: {', '.join(missing)}")

        for question in assessment.questions:
            if question.tool_id not in tools:
                problems.append(f"вопрос {question.id} ссылается на несуществующую форму контроля {question.tool_id}")
            unknown(question.outcome_ids, f"вопрос {question.id}")
        for task in assessment.seminar_tasks:
            if task.tool_id not in tools:
                problems.append(f"задание {task.id} ссылается на несуществующую форму контроля {task.tool_id}")
            if task.topic_id not in topics:
                problems.append(f"задание {task.id} ссылается на несуществующую тему {task.topic_id}")
            unknown(task.outcome_ids, f"задание {task.id}")
        for tool in assessment.tools:
            missing = [item for item in tool.topic_ids if item not in topics]
            if missing:
                problems.append(f"форма контроля {tool.id} ссылается на несуществующие темы: {', '.join(missing)}")
            unknown(tool.outcome_ids, f"форма контроля {tool.id}")
        unknown(assessment.final.outcome_ids, "итоговая аттестация")
        return problems

    def assessment_coverage_problems(self, assessment: AssessmentPlan, curriculum: Curriculum) -> list[str]:
        """The outcomes that fewer than two means of checking look at.

        The means are the questions, the seminar tasks and the forms of
        control. The final attestation is the programme's conclusion and not
        one of them.
        """
        declared = set(self.declared_outcomes())
        checked: dict[str, int] = dict.fromkeys(declared, 0)
        for outcome_ids in (
            [question.outcome_ids for question in assessment.questions]
            + [task.outcome_ids for task in assessment.seminar_tasks]
            + [tool.outcome_ids for tool in assessment.tools]
        ):
            for outcome_id in outcome_ids:
                if outcome_id in checked:
                    checked[outcome_id] += 1
        problems = [
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


def _confirmed(source: Source) -> Source:
    """The catalog decides whether a source is confirmed, not the model.

    A document the catalog holds is cited with the catalog's record. Any other
    keeps what the agent wrote about it and is marked as unconfirmed, because
    nobody verified the document and the methodist has to see that.
    """
    record = sources.get_source(source.id)
    if record is None:
        return source.model_copy(update={"confirmed": False})
    # The record is rebuilt rather than copied, because a copy skips validation
    # and the literature mark only makes sense for literature.
    return Source(
        id=record.id,
        kind=record.kind,
        title=record.title,
        identifier=record.identifier,
        url=source.url or record.url,
        primary=source.primary if record.kind is SourceKind.LITERATURE else None,
        confirmed=True,
        trust=record.trust,
        origin=record.origin,
    )


def new_state(
    session_id: uuid.UUID,
    *,
    requested_hours: float | None,
    requirements: str | None,
    questions: Iterable[str] = (),
    kind: ProgramKind | None = None,
    format: ProgramFormat = ProgramFormat.COURSE,
    expected_test_questions: int | None = None,
    min_practice_share: float | None = None,
) -> ProgramState:
    """Start a run: either generating, or waiting for the methodist's answers.

    Questions are the ones the model could not answer from the methodist's
    message. A run that has them does not generate yet: it waits, and the first
    stage is enqueued only once the answers arrive. A run without them starts
    at once, with the competence stage already on the queue.

    ``kind`` and ``format`` may come from the intake turn; a run that was not
    told them leaves both to the competence stage, which writes them from what
    the model chose. The limits from the request are kept with the run, so a
    later stage can be held to them.
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
            kind=kind,
            format=format,
            expected_test_questions=expected_test_questions,
            min_practice_share=min_practice_share,
        )
    return ProgramState(
        session_id=session_id,
        run_id=uuid.uuid4(),
        status=ProgramStatus.GENERATING,
        current_stage=Stage.COMPETENCES,
        attempts={Stage.COMPETENCES: 1},
        requested_hours=requested_hours,
        requirements=requirements,
        kind=kind,
        format=format,
        expected_test_questions=expected_test_questions,
        min_practice_share=min_practice_share,
        started_at=datetime.now(UTC),
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
