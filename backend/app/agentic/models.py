"""Pydantic schema of the АЦР programme document.

The schema mirrors the blocks of the final document: summary, competences and
outcomes, curriculum, assessment, materials, sources, conditions and staffing.
Generation progress is tracked per stage.
"""

from __future__ import annotations

import uuid
from datetime import date
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


class SchemaModel(BaseModel):
    """Base model of the schema: immutable instances."""

    model_config = ConfigDict(frozen=True)


# Stable ids of the schema blocks. Cross-block references reuse the aliases:
# ToolId is the id of an AssessmentTool, referenced by TestQuestion.tool_id and
# SeminarTask.tool_id; TopicId is likewise referenced by LecturePlan, SlideDeck
# and SeminarTask.
CompetenceId = Annotated[str, StringConstraints(pattern=r"^comp-\d{1,3}$")]
OutcomeId = Annotated[str, StringConstraints(pattern=r"^ro-[kse]-\d{1,3}$")]
ModuleId = Annotated[str, StringConstraints(pattern=r"^m-\d{1,3}$")]
TopicId = Annotated[str, StringConstraints(pattern=r"^t-\d{1,3}\.\d{1,3}$")]
ToolId = Annotated[str, StringConstraints(pattern=r"^at-\d{1,3}$")]
QuestionId = Annotated[str, StringConstraints(pattern=r"^q-\d{1,3}-\d{1,3}$")]
SeminarTaskId = Annotated[str, StringConstraints(pattern=r"^st-\d{1,3}$")]
LecturePlanId = Annotated[str, StringConstraints(pattern=r"^lp-\d{1,3}$")]
SlideDeckId = Annotated[str, StringConstraints(pattern=r"^sd-\d{1,3}$")]
SourceId = Annotated[str, StringConstraints(pattern=r"^src-\d{1,3}$")]
StaffSlotId = Annotated[str, StringConstraints(pattern=r"^ts-\d{1,3}$")]


class ProgramKind(StrEnum):
    """Programme template."""

    PROFESSIONAL_DEVELOPMENT = "professional_development"
    GENERAL_DEVELOPMENT = "general_development"


class ProgramStatus(StrEnum):
    """Lifecycle of the document; per-stage progress lives in StageStatuses."""

    DRAFT = "draft"
    GENERATING = "generating"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    VERIFIED = "verified"


class SourceKind(StrEnum):
    """Kind of a source cited by the programme."""

    NORMATIVE_ACT = "normative_act"
    PROFESSIONAL_STANDARD = "professional_standard"
    LITERATURE = "literature"
    INTERNET_RESOURCE = "internet_resource"
    REFERENCE = "reference"


class AssessmentKind(StrEnum):
    """Kind of an assessment instrument."""

    TEST = "test"
    SEMINAR_TASK = "seminar_task"
    INTERVIEW = "interview"
    CREDIT = "credit"
    EXAM = "exam"
    PROJECT_DEFENCE = "project_defence"
    PORTFOLIO = "portfolio"


class Stage(StrEnum):
    """Generation pipeline stage, shown in the UI."""

    COMPETENCES = "competences"
    HOURS = "hours"
    ASSESSMENT = "assessment"
    MATERIALS = "materials"
    SOURCES = "sources"
    SUMMARY = "summary"


class StageStatus(StrEnum):
    """Status of a single generation stage."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class Hours(SchemaModel):
    """Hours of one topic, split by activity type."""

    lecture: float = Field(default=0, description="Лекции (ЛК)")
    practice: float = Field(default=0, description="Практические занятия (ПР)")
    lab: float = Field(default=0, description="Лабораторные работы (ЛБ)")
    self_study: float = Field(default=0, description="Самостоятельная работа (СРС)")


class Topic(SchemaModel):
    """Curriculum topic: hours, control form and covered outcomes."""

    id: TopicId
    title: str = Field(min_length=1, max_length=500)
    hours: Hours
    control_form: str | None = Field(default=None, max_length=500, description="Форма текущего контроля по теме")
    outcome_ids: list[OutcomeId] = Field(default_factory=list, description="Learning outcomes the topic covers")


class Module(SchemaModel):
    """Section of the curriculum."""

    id: ModuleId
    title: str = Field(min_length=1, max_length=500)
    topics: list[Topic] = Field(min_length=1)


class Schedule(SchemaModel):
    """Curriculum schedule: duration and daily load."""

    weeks: int | None = Field(default=None, description="Duration in weeks")
    days: int | None = Field(default=None, description="Duration in days")
    hours_per_day: float | None = Field(default=None, description="Daily load in hours")
    note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _require_duration_or_load(self) -> Self:
        """At least one of weeks, days or hours_per_day must be filled."""
        if self.weeks is None and self.days is None and self.hours_per_day is None:
            raise ValueError("schedule: weeks, days or hours_per_day must be filled")
        return self


class Curriculum(SchemaModel):
    """Curriculum plan: the module tree, the schedule and the hour sums."""

    modules: list[Module] = Field(min_length=1)
    schedule: Schedule

    @property
    def total_hours(self) -> float:
        """Total hours of all modules; the final attestation is not included."""
        return sum(
            topic.hours.lecture + topic.hours.practice + topic.hours.lab + topic.hours.self_study
            for module in self.modules
            for topic in module.topics
        )

    @property
    def contact_hours(self) -> float:
        """Classroom hours: lectures, practice and lab work."""
        return sum(
            topic.hours.lecture + topic.hours.practice + topic.hours.lab
            for module in self.modules
            for topic in module.topics
        )

    @property
    def self_study_hours(self) -> float:
        """Self-study hours of all modules."""
        return sum(topic.hours.self_study for module in self.modules for topic in module.topics)


class Outcome(SchemaModel):
    """A single learning outcome (знание, умение or практический опыт)."""

    id: OutcomeId
    statement: str = Field(min_length=1, max_length=2000)


class Competence(SchemaModel):
    """Professional competence with the outcomes that reveal it."""

    id: CompetenceId
    code: str | None = Field(
        default=None, max_length=50, description="Labour-function code from the professional standard, e.g. C/14.6"
    )
    statement: str = Field(min_length=1, max_length=2000)
    activity: str | None = Field(default=None, max_length=2000, description="Виды деятельности из профстандарта")
    standard_source_id: SourceId | None = Field(
        default=None, description="Professional standard source the competence comes from"
    )
    experience: list[Outcome] = Field(default_factory=list, description="Практический опыт")
    skills: list[Outcome] = Field(default_factory=list, description="Умения")
    knowledge: list[Outcome] = Field(default_factory=list, description="Знания")

    @model_validator(mode="after")
    def _require_an_outcome(self) -> Self:
        """A competence without any outcome says nothing about the graduate."""
        if not (self.experience or self.skills or self.knowledge):
            raise ValueError(f"competence {self.id}: no knowledge, skills or experience declared")
        return self


class AssessmentTool(SchemaModel):
    """Current-control assessment instrument: test, interview, practice."""

    id: ToolId
    kind: AssessmentKind
    title: str = Field(min_length=1, max_length=500)
    topic_ids: list[TopicId] = Field(default_factory=list, description="Topics whose content the tool checks")
    outcome_ids: list[OutcomeId] = Field(min_length=1, description="Outcomes the tool checks")
    criteria: list[str] = Field(default_factory=list, description="What a passing answer or artefact looks like")
    materials: list[str] = Field(default_factory=list, description="Ready materials: questions, cases, tasks")


class TestQuestion(SchemaModel):
    """One question of a test with the reference answer key."""

    id: QuestionId
    tool_id: ToolId = Field(description="Assessment tool the question belongs to")
    outcome_ids: list[OutcomeId] = Field(min_length=1)
    text: str = Field(min_length=1, max_length=2000)
    options: list[str] = Field(default_factory=list, description="Answer options; empty for an open question")
    answers: list[str] = Field(min_length=1, description="Reference answer; exactly one for an open question")

    @model_validator(mode="after")
    def _check_the_key(self) -> Self:
        """An open question keeps one answer; a closed one keeps answers among the options."""
        if not self.options:
            if len(self.answers) != 1:
                raise ValueError(f"question {self.id}: an open question needs exactly one reference answer")
            return self
        if len(self.options) < 2:
            raise ValueError(f"question {self.id}: a question with options needs at least two of them")
        stray = [answer for answer in self.answers if answer not in self.options]
        if stray:
            raise ValueError(f"question {self.id}: answer(s) {', '.join(stray)} are not among the options")
        return self


class SeminarTask(SchemaModel):
    """Practical assignment of a seminar, bound to one topic."""

    id: SeminarTaskId
    topic_id: TopicId
    tool_id: ToolId | None = Field(default=None, description="Assessment tool the task is checked with")
    title: str = Field(min_length=1, max_length=500)
    statement: str = Field(min_length=1, max_length=4000)
    outcome_ids: list[OutcomeId] = Field(min_length=1, description="Outcomes the task reveals")


class FinalAttestation(SchemaModel):
    """Final attestation of the programme."""

    form: str = Field(min_length=1, max_length=500, description="Form: defence, exam, test")
    hours: float = Field(description="Hours allocated to the final attestation")
    outcome_ids: list[OutcomeId] = Field(min_length=1)
    criteria: list[str] = Field(default_factory=list)
    materials: list[str] = Field(default_factory=list)


class AssessmentPlan(SchemaModel):
    """Current control instruments, their questions and tasks, and the final attestation."""

    tools: list[AssessmentTool] = Field(default_factory=list)
    final: FinalAttestation
    questions: list[TestQuestion] = Field(default_factory=list)
    seminar_tasks: list[SeminarTask] = Field(default_factory=list)


class LecturePlan(SchemaModel):
    """Lecture outline of one topic."""

    id: LecturePlanId
    topic_id: TopicId
    title: str = Field(min_length=1, max_length=500)
    plan: list[str] = Field(min_length=1, description="План лекции as bullet points")
    summary: str | None = Field(default=None, max_length=4000, description="Short annotation of the lecture")


class Slide(SchemaModel):
    """One slide of a deck."""

    title: str = Field(min_length=1, max_length=500)
    bullets: list[str] = Field(default_factory=list)


class SlideDeck(SchemaModel):
    """Slide deck of one topic."""

    id: SlideDeckId
    topic_id: TopicId
    title: str = Field(min_length=1, max_length=500)
    slides: list[Slide] = Field(min_length=1)


class Materials(SchemaModel):
    """Teaching materials prepared for the programme."""

    lecture_plans: list[LecturePlan] = Field(default_factory=list)
    slide_decks: list[SlideDeck] = Field(default_factory=list)


class Source(SchemaModel):
    """Document the programme cites or builds upon."""

    id: SourceId
    kind: SourceKind
    title: str = Field(min_length=1, max_length=2000)
    identifier: str | None = Field(
        default=None, max_length=1000, description="Number or code: 273-ФЗ, приказ № 499, 06.042"
    )
    url: str | None = Field(default=None, max_length=2000, description="URL of an internet resource")
    primary: bool | None = Field(default=None, description="Literature only: True is основная, False is дополнительная")

    @model_validator(mode="after")
    def _require_literature_for_primary(self) -> Self:
        """Основная/дополнительная marks make sense for literature only."""
        if self.primary is not None and self.kind is not SourceKind.LITERATURE:
            raise ValueError(f"source {self.id}: primary is set for literature only")
        return self


class Conditions(SchemaModel):
    """Conditions of programme delivery: equipment, materials, staff."""

    equipment: list[str] = Field(default_factory=list)
    teaching_materials: list[str] = Field(default_factory=list)
    staffing_requirements: list[str] = Field(default_factory=list)


class StaffSlot(SchemaModel):
    """Staff position of the programme."""

    id: StaffSlotId
    position: str = Field(min_length=1, max_length=500, description="Должность, e.g. «Руководитель стажировки»")
    module_id: ModuleId | None = Field(default=None, description="Module the position is bound to")
    fio: str | None = Field(default=None, max_length=500, description="Full name; left empty in the document")
    degree: str | None = Field(default=None, max_length=500, description="Учёная степень или звание")


class ApprovalInfo(SchemaModel):
    """Order details, filled in manually on approval."""

    order_number: str | None = Field(default=None, max_length=100)
    order_date: date | None = None
    signatory: str | None = Field(default=None, max_length=500, description="Who approves the programme")


class ProgramSummary(SchemaModel):
    """Summary text of the programme: goal, audience, form and basis."""

    goal: str = Field(min_length=1, max_length=4000)
    tasks: list[str] = Field(default_factory=list, description="Задачи программы")
    target_audience: str = Field(min_length=1, max_length=4000)
    admission_requirements: str | None = Field(default=None, max_length=4000, description="Требования к слушателям")
    annotation: str | None = Field(default=None, max_length=8000, description="Аннотация программы")
    relevance: str | None = Field(default=None, max_length=8000, description="Актуальность программы")
    form: str = Field(min_length=1, max_length=500, description="Форма обучения")
    issued_document: str = Field(
        min_length=1, max_length=500, description="Документ по итогам обучения: удостоверение, сертификат"
    )
    focus: str | None = Field(default=None, max_length=1000, description="Направленность программы")
    level: str | None = Field(default=None, max_length=1000, description="Уровень освоения программы")
    basis_source_ids: list[SourceId] = Field(
        default_factory=list, description="Documents the programme is based on («разработана на основе»)"
    )


class ProgramMeta(SchemaModel):
    """Identity of the document."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    kind: ProgramKind
    title: str = Field(min_length=1, max_length=500)
    status: ProgramStatus = ProgramStatus.DRAFT
    version: int = 1


class StageStatuses(SchemaModel):
    """Status of every generation stage, shown in the UI."""

    competences: StageStatus = StageStatus.PENDING
    hours: StageStatus = StageStatus.PENDING
    assessment: StageStatus = StageStatus.PENDING
    materials: StageStatus = StageStatus.PENDING
    sources: StageStatus = StageStatus.PENDING
    summary: StageStatus = StageStatus.PENDING


class EducationalProgram(SchemaModel):
    """Full state of the АЦР programme document."""

    meta: ProgramMeta
    current_stage: Stage = Field(description="Stage the agent is working on")
    stages: StageStatuses = Field(description="Status of each generation stage")
    summary: ProgramSummary
    competencies: list[Competence] = Field(min_length=1)
    curriculum: Curriculum
    assessment: AssessmentPlan
    materials: Materials
    sources: list[Source] = Field(min_length=1)
    conditions: Conditions
    staffing: list[StaffSlot] = Field(min_length=1)
    approval: ApprovalInfo


def _duplicate_ids(ids: list[str]) -> list[str]:
    """The ids that appear more than once, each reported a single time."""
    seen: set[str] = set()
    duplicates: list[str] = []
    for item_id in ids:
        if item_id in seen and item_id not in duplicates:
            duplicates.append(item_id)
        seen.add(item_id)
    return duplicates


def _declared_outcomes(draft: ProgramDraft) -> list[Outcome]:
    """The learning outcomes the competences declare, in declaration order."""
    return [
        outcome
        for competence in draft.competencies
        for outcome in (*competence.experience, *competence.skills, *competence.knowledge)
    ]


class ProgramDraft(SchemaModel):
    """Ранние блоки программы, которые методист подтверждает до полной генерации.

    The draft is what the methodist reviews and approves: the programme passport
    (kind and title), the summary, the competences with their outcomes, the
    curriculum with its hour split and the sources the agent found. The remaining
    blocks of :class:`EducationalProgram` — assessment, materials, conditions,
    staffing, approval — are generated later, from this approved draft, so no
    later stage has to invent the structure again.
    """

    kind: ProgramKind = Field(description="Тип программы: повышение квалификации или общеразвивающая")
    title: str = Field(min_length=1, max_length=500, description="Название программы")
    summary: ProgramSummary = Field(description="Паспорт программы: цель, аудитория, форма обучения")
    competencies: list[Competence] = Field(min_length=1, description="Компетенции с результатами освоения")
    curriculum: Curriculum = Field(description="Учебный план: модули, темы, часы и график")
    sources: list[Source] = Field(
        default_factory=list, description="Источники, найденные поиском и использованные в программе"
    )

    @model_validator(mode="after")
    def _reject_duplicate_ids(self) -> Self:
        """Reject repeated ids, one namespace at a time.

        Every cross-block reference resolves by id — ``standard_source_id``,
        ``outcome_ids``, the module tree — so a duplicate silently makes the
        reference ambiguous instead of failing loudly. The namespaces are
        independent: ``comp-01`` and ``m-01`` are patterns that cannot collide.
        """
        namespaces: dict[str, list[str]] = {
            "competencies": [competence.id for competence in self.competencies],
            "outcomes": [outcome.id for outcome in _declared_outcomes(self)],
            "modules": [module.id for module in self.curriculum.modules],
            "topics": [topic.id for module in self.curriculum.modules for topic in module.topics],
            "sources": [source.id for source in self.sources],
        }
        problems = [
            f"{namespace}: {', '.join(duplicates)}"
            for namespace, ids in namespaces.items()
            if (duplicates := _duplicate_ids(ids))
        ]
        if problems:
            raise ValueError("duplicate ids: " + "; ".join(problems))
        return self

    @model_validator(mode="after")
    def _reject_undeclared_outcomes(self) -> Self:
        """A topic may cover only outcomes a competence declares.

        ``Topic.outcome_ids`` is a reference into the competence block, and the
        two blocks arrive in the same payload without anything tying them
        together. An id nothing declares is a dangling link: the document cannot
        render it, and the methodist is promised an outcome that does not exist.
        """
        declared = {outcome.id for outcome in _declared_outcomes(self)}
        undeclared = [
            f"{topic.id}: {outcome_id}"
            for module in self.curriculum.modules
            for topic in module.topics
            for outcome_id in topic.outcome_ids
            if outcome_id not in declared
        ]
        if undeclared:
            raise ValueError("outcome_ids not declared by any competence: " + "; ".join(undeclared))
        return self
