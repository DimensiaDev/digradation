from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Iterable, Iterator
from datetime import date
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

HOURS_STEP = 0.5
MAX_HOURS = 2000.0
_TOLERANCE = 1e-9


class SchemaModel(BaseModel):

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True, revalidate_instances="always")


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

HoursAmount = Annotated[float, Field(ge=0, le=MAX_HOURS, multiple_of=HOURS_STEP)]
PositiveHoursAmount = Annotated[float, Field(gt=0, le=MAX_HOURS, multiple_of=HOURS_STEP)]


class ProgramKind(StrEnum):

    PROFESSIONAL_DEVELOPMENT = "professional_development"
    GENERAL_DEVELOPMENT = "general_development"

    @property
    def label(self) -> str:
        return _PROGRAM_KIND_LABELS[self]


_PROGRAM_KIND_LABELS: dict[ProgramKind, str] = {
    ProgramKind.PROFESSIONAL_DEVELOPMENT: "повышение квалификации",
    ProgramKind.GENERAL_DEVELOPMENT: "общеразвивающая программа",
}


class ProgramStatus(StrEnum):

    DRAFT = "draft"
    GENERATING = "generating"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    VERIFIED = "verified"

    @property
    def label(self) -> str:
        return _PROGRAM_STATUS_LABELS[self]


_PROGRAM_STATUS_LABELS: dict[ProgramStatus, str] = {
    ProgramStatus.DRAFT: "черновик",
    ProgramStatus.GENERATING: "генерируется",
    ProgramStatus.PENDING_APPROVAL: "ожидает утверждения",
    ProgramStatus.APPROVED: "утверждена",
    ProgramStatus.VERIFIED: "проверена",
}

STATUS_TRANSITIONS: dict[ProgramStatus, frozenset[ProgramStatus]] = {
    ProgramStatus.DRAFT: frozenset({ProgramStatus.GENERATING}),
    ProgramStatus.GENERATING: frozenset({ProgramStatus.DRAFT, ProgramStatus.PENDING_APPROVAL}),
    ProgramStatus.PENDING_APPROVAL: frozenset({ProgramStatus.DRAFT, ProgramStatus.APPROVED}),
    ProgramStatus.APPROVED: frozenset({ProgramStatus.DRAFT, ProgramStatus.GENERATING}),
    ProgramStatus.VERIFIED: frozenset({ProgramStatus.DRAFT, ProgramStatus.APPROVED}),
}


class SourceKind(StrEnum):
    NORMATIVE_ACT = "normative_act"
    PROFESSIONAL_STANDARD = "professional_standard"
    LITERATURE = "literature"
    INTERNET_RESOURCE = "internet_resource"
    REFERENCE = "reference"


class AssessmentKind(StrEnum):
    TEST = "test"
    SEMINAR_TASK = "seminar_task"
    INTERVIEW = "interview"
    CREDIT = "credit"
    EXAM = "exam"
    PROJECT_DEFENCE = "project_defence"
    PORTFOLIO = "portfolio"
    OTHER = "other"


class Hours(SchemaModel):

    lecture: HoursAmount = 0
    practice: HoursAmount = 0
    lab: HoursAmount = 0
    self_study: HoursAmount = 0

    @property
    def total(self) -> float:
        return self.lecture + self.practice + self.lab + self.self_study

    @property
    def contact(self) -> float:
        return self.lecture + self.practice + self.lab


class Topic(SchemaModel):

    id: TopicId
    title: str = Field(min_length=1, max_length=500)
    hours: Hours
    control_form: str | None = Field(default=None, max_length=500)
    outcome_ids: list[OutcomeId] = Field(default_factory=list)
    source_ids: list[SourceId] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_positive_total(self) -> Self:
        if self.hours.total <= 0:
            raise ValueError(f"topic {self.id}: hours add up to zero")
        return self


class Module(SchemaModel):

    id: ModuleId
    title: str = Field(min_length=1, max_length=500)
    topics: list[Topic] = Field(min_length=1)

    @property
    def total_hours(self) -> float:
        return sum(topic.hours.total for topic in self.topics)


class Schedule(SchemaModel):

    weeks: int | None = Field(default=None, ge=1, le=520)
    days: int | None = Field(default=None, ge=1, le=3650)
    hours_per_day: float | None = Field(default=None, gt=0, le=24)
    note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _require_duration_or_load(self) -> Self:
        if self.weeks is None and self.days is None and self.hours_per_day is None:
            raise ValueError("schedule: weeks, days or hours_per_day must be filled")
        return self


class Curriculum(SchemaModel):

    modules: list[Module] = Field(min_length=1)
    schedule: Schedule

    @property
    def total_hours(self) -> float:
        return sum(module.total_hours for module in self.modules)

    @property
    def contact_hours(self) -> float:
        return sum(topic.hours.contact for module in self.modules for topic in module.topics)

    @property
    def self_study_hours(self) -> float:
        return sum(topic.hours.self_study for module in self.modules for topic in module.topics)


class Outcome(SchemaModel):

    id: OutcomeId
    statement: str = Field(min_length=1, max_length=2000)
    source_ids: list[SourceId] = Field(default_factory=list)


class Competence(SchemaModel):

    id: CompetenceId
    code: str | None = Field(
        default=None,
        max_length=50,
        description="Function code from the professional standard, e.g. C/14.6",
    )
    statement: str = Field(min_length=1, max_length=2000)
    activity: str | None = Field(default=None, max_length=2000)
    standard_source_id: SourceId | None = None
    experience: list[Outcome] = Field(default_factory=list)
    skills: list[Outcome] = Field(default_factory=list)
    knowledge: list[Outcome] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_an_outcome(self) -> Self:
        if not (self.experience or self.skills or self.knowledge):
            raise ValueError(f"competence {self.id}: no knowledge, skills or experience declared")
        return self


class AssessmentTool(SchemaModel):

    id: ToolId
    kind: AssessmentKind
    title: str = Field(min_length=1, max_length=500)
    topic_ids: list[TopicId] = Field(default_factory=list)
    outcome_ids: list[OutcomeId] = Field(min_length=1)
    criteria: list[str] = Field(default_factory=list)
    materials: list[str] = Field(default_factory=list)


class TestQuestion(SchemaModel):

    id: QuestionId
    tool_id: ToolId
    topic_id: TopicId | None = None
    outcome_ids: list[OutcomeId] = Field(min_length=1)
    text: str = Field(min_length=1, max_length=2000)
    options: list[str] = Field(default_factory=list)
    answers: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_the_key(self) -> Self:
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

    id: SeminarTaskId
    topic_id: TopicId
    tool_id: ToolId | None = None
    title: str = Field(min_length=1, max_length=500)
    statement: str = Field(min_length=1, max_length=4000)
    criteria: list[str] = Field(default_factory=list)
    outcome_ids: list[OutcomeId] = Field(min_length=1)


class FinalAttestation(SchemaModel):

    form: str = Field(min_length=1, max_length=500)
    hours: PositiveHoursAmount
    outcome_ids: list[OutcomeId] = Field(min_length=1)
    criteria: list[str] = Field(default_factory=list)
    materials: list[str] = Field(default_factory=list)


class AssessmentPlan(SchemaModel):
    tools: list[AssessmentTool] = Field(default_factory=list)
    final: FinalAttestation
    questions: list[TestQuestion] = Field(default_factory=list)
    seminar_tasks: list[SeminarTask] = Field(default_factory=list)


class LecturePlan(SchemaModel):

    id: LecturePlanId
    topic_id: TopicId
    title: str = Field(min_length=1, max_length=500)
    plan: list[str] = Field(min_length=1)
    summary: str | None = Field(default=None, max_length=4000)


class Slide(SchemaModel):
    title: str = Field(min_length=1, max_length=500)
    bullets: list[str] = Field(default_factory=list)


class SlideDeck(SchemaModel):

    id: SlideDeckId
    topic_id: TopicId
    lecture_plan_id: LecturePlanId | None = None
    title: str = Field(min_length=1, max_length=500)
    slides: list[Slide] = Field(min_length=1)


class Materials(SchemaModel):
    lecture_plans: list[LecturePlan] = Field(default_factory=list)
    slide_decks: list[SlideDeck] = Field(default_factory=list)


class Source(SchemaModel):

    id: SourceId
    kind: SourceKind
    title: str = Field(min_length=1, max_length=2000)
    identifier: str | None = Field(default=None, max_length=1000)
    url: str | None = Field(default=None, max_length=2000)
    locator: str | None = Field(
        default=None,
        max_length=2000,
        description="Corpus document id and the page or fragment a quote came from",
    )
    primary: bool | None = Field(default=None, description="Literature only: True is основная, False is дополнительная")
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _require_literature_for_primary(self) -> Self:
        if self.primary is not None and self.kind is not SourceKind.LITERATURE:
            raise ValueError(f"source {self.id}: primary is set for literature only")
        return self


class Conditions(SchemaModel):

    equipment: list[str] = Field(default_factory=list)
    teaching_materials: list[str] = Field(default_factory=list)
    staffing_requirements: list[str] = Field(default_factory=list)


class StaffSlot(SchemaModel):

    id: StaffSlotId
    role: str = Field(min_length=1, max_length=500)
    module_id: ModuleId | None = None
    fio: str | None = Field(default=None, max_length=500)
    degree: str | None = Field(default=None, max_length=500)
    position: str | None = Field(default=None, max_length=500)


class ApprovalInfo(SchemaModel):

    order_number: str | None = Field(default=None, max_length=100)
    order_date: date | None = None
    signatory: str | None = Field(default=None, max_length=500)


class ProgramSummary(SchemaModel):

    goal: str = Field(min_length=1, max_length=4000)
    tasks: list[str] = Field(default_factory=list)
    target_audience: str = Field(min_length=1, max_length=4000)
    admission_requirements: str | None = Field(default=None, max_length=4000)
    annotation: str | None = Field(default=None, max_length=8000)
    relevance: str | None = Field(default=None, max_length=8000)
    form: str = Field(min_length=1, max_length=500)
    issued_document: str = Field(min_length=1, max_length=500)
    focus: str | None = Field(default=None, max_length=1000, description="Required for general_development")
    level: str | None = Field(default=None, max_length=1000, description="Required for general_development")
    basis_source_ids: list[SourceId] = Field(default_factory=list)


class VolumeSpec(SchemaModel):

    total_hours: PositiveHoursAmount
    declared_contact_hours: HoursAmount | None = None
    declared_self_study_hours: HoursAmount | None = None


class ProgramMeta(SchemaModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    kind: ProgramKind
    title: str = Field(min_length=1, max_length=500)
    status: ProgramStatus = ProgramStatus.DRAFT
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _reject_verified_input(self) -> Self:
        if self.status is ProgramStatus.VERIFIED:
            raise ValueError("status verified is assigned only by verify() after a clean check")
        return self

    def can_transition_to(self, new_status: ProgramStatus) -> bool:
        return new_status is self.status or new_status in STATUS_TRANSITIONS[self.status]

    def transition_to(self, new_status: ProgramStatus) -> Self:
        if not self.can_transition_to(new_status):
            raise ValueError(f"status transition {self.status} -> {new_status} is not allowed")
        return self.model_copy(update={"status": new_status})


def _tool_binding_problems(
    label: str, tool: AssessmentTool, outcome_ids: Iterable[str], topic_id: str | None
) -> list[str]:
    problems = []
    outside = sorted(set(outcome_ids) - set(tool.outcome_ids))
    if outside:
        problems.append(f"{label}: outcome(s) {', '.join(outside)} are not covered by tool {tool.id}")
    if topic_id is not None and tool.topic_ids and topic_id not in tool.topic_ids:
        problems.append(f"{label}: topic {topic_id} is not covered by tool {tool.id}")
    return problems


class EducationalProgram(SchemaModel):

    meta: ProgramMeta
    summary: ProgramSummary
    volume: VolumeSpec
    competencies: list[Competence] = Field(min_length=1)
    curriculum: Curriculum
    assessment: AssessmentPlan
    materials: Materials
    sources: list[Source] = Field(min_length=1)
    conditions: Conditions
    staffing: list[StaffSlot] = Field(min_length=1)
    approval: ApprovalInfo

    def iter_outcomes(self) -> Iterator[Outcome]:
        for competence in self.competencies:
            yield from competence.knowledge
            yield from competence.skills
            yield from competence.experience

    def transition_to(self, new_status: ProgramStatus) -> Self:
        return self.model_copy(update={"meta": self.meta.transition_to(new_status)})

    def revise(self, **updates: object) -> Self:
        raw = {**self.model_dump(), **updates}
        meta = raw["meta"]
        if isinstance(meta, ProgramMeta):
            meta = meta.model_dump()
        raw["meta"] = {**meta, "status": ProgramStatus.DRAFT.value}
        return type(self).model_validate(raw)

    def problems(self) -> list[str]:
        return [
            *self._id_problems(),
            *self._reference_problems(),
            *self._template_problems(),
            *self._hours_problems(),
        ]

    @model_validator(mode="after")
    def _validate(self) -> Self:
        problems = self.problems()
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def _source_index(self) -> dict[str, Source]:
        return {source.id: source for source in self.sources}

    def _topic_index(self) -> dict[str, Topic]:
        return {topic.id: topic for module in self.curriculum.modules for topic in module.topics}

    def _id_problems(self) -> list[str]:
        groups: dict[str, Iterable[str]] = {
            "source": (source.id for source in self.sources),
            "competence": (competence.id for competence in self.competencies),
            "outcome": (outcome.id for outcome in self.iter_outcomes()),
            "module": (module.id for module in self.curriculum.modules),
            "topic": (topic.id for module in self.curriculum.modules for topic in module.topics),
            "assessment tool": (tool.id for tool in self.assessment.tools),
            "question": (question.id for question in self.assessment.questions),
            "seminar task": (task.id for task in self.assessment.seminar_tasks),
            "lecture plan": (plan.id for plan in self.materials.lecture_plans),
            "slide deck": (deck.id for deck in self.materials.slide_decks),
            "staff slot": (slot.id for slot in self.staffing),
        }
        problems = []
        for label, ids in groups.items():
            repeated = sorted(value for value, count in Counter(ids).items() if count > 1)
            if repeated:
                problems.append(f"duplicate {label} id(s): {', '.join(repeated)}")
        for competence in self.competencies:
            for bucket, prefix in (
                (competence.knowledge, "ro-k-"),
                (competence.skills, "ro-s-"),
                (competence.experience, "ro-e-"),
            ):
                for outcome in bucket:
                    if not outcome.id.startswith(prefix):
                        problems.append(f"outcome {outcome.id} must start with {prefix}")
        for module in self.curriculum.modules:
            number = module.id.removeprefix("m-")
            for topic in module.topics:
                if not topic.id.startswith(f"t-{number}."):
                    problems.append(f"topic {topic.id} does not belong to module {module.id}")
        return problems

    def _reference_problems(self) -> list[str]:
        sources = self._source_index()
        source_ids = set(sources)
        outcome_ids = {outcome.id for outcome in self.iter_outcomes()}
        topic_ids = set(self._topic_index())
        tool_ids = {tool.id for tool in self.assessment.tools}
        tools_by_id = {tool.id: tool for tool in self.assessment.tools}
        plans_by_id = {plan.id: plan for plan in self.materials.lecture_plans}
        plan_ids = set(plans_by_id)
        module_ids = {module.id for module in self.curriculum.modules}

        problems: list[str] = []
        references: list[tuple[str, Iterable[str], set[str]]] = [
            ("summary.basis_source_ids", self.summary.basis_source_ids, source_ids),
        ]
        for competence in self.competencies:
            if competence.standard_source_id is None:
                continue
            references.append(
                (f"competence {competence.id}.standard_source_id", [competence.standard_source_id], source_ids)
            )
            standard = sources.get(competence.standard_source_id)
            if standard is not None and standard.kind is not SourceKind.PROFESSIONAL_STANDARD:
                problems.append(f"competence {competence.id}: standard_source_id must point at a professional standard")
        for outcome in self.iter_outcomes():
            references.append((f"outcome {outcome.id}.source_ids", outcome.source_ids, source_ids))
        for topic in self._topic_index().values():
            references.append((f"topic {topic.id}.outcome_ids", topic.outcome_ids, outcome_ids))
            references.append((f"topic {topic.id}.source_ids", topic.source_ids, source_ids))
        for tool in self.assessment.tools:
            references.append((f"tool {tool.id}.topic_ids", tool.topic_ids, topic_ids))
            references.append((f"tool {tool.id}.outcome_ids", tool.outcome_ids, outcome_ids))
        references.append(("assessment.final.outcome_ids", self.assessment.final.outcome_ids, outcome_ids))
        for question in self.assessment.questions:
            references.append((f"question {question.id}.tool_id", [question.tool_id], tool_ids))
            tool = tools_by_id.get(question.tool_id)
            if tool is not None:
                if tool.kind is not AssessmentKind.TEST:
                    problems.append(f"question {question.id}: tool {tool.id} is not a test")
                problems.extend(
                    _tool_binding_problems(f"question {question.id}", tool, question.outcome_ids, question.topic_id)
                )
            if question.topic_id is not None:
                references.append((f"question {question.id}.topic_id", [question.topic_id], topic_ids))
            references.append((f"question {question.id}.outcome_ids", question.outcome_ids, outcome_ids))
        for task in self.assessment.seminar_tasks:
            references.append((f"seminar task {task.id}.topic_id", [task.topic_id], topic_ids))
            if task.tool_id is not None:
                references.append((f"seminar task {task.id}.tool_id", [task.tool_id], tool_ids))
                tool = tools_by_id.get(task.tool_id)
                if tool is not None:
                    problems.extend(
                        _tool_binding_problems(f"seminar task {task.id}", tool, task.outcome_ids, task.topic_id)
                    )
            references.append((f"seminar task {task.id}.outcome_ids", task.outcome_ids, outcome_ids))
        for plan in self.materials.lecture_plans:
            references.append((f"lecture plan {plan.id}.topic_id", [plan.topic_id], topic_ids))
        for deck in self.materials.slide_decks:
            references.append((f"slide deck {deck.id}.topic_id", [deck.topic_id], topic_ids))
            if deck.lecture_plan_id is not None:
                references.append((f"slide deck {deck.id}.lecture_plan_id", [deck.lecture_plan_id], plan_ids))
                plan = plans_by_id.get(deck.lecture_plan_id)
                if plan is not None and plan.topic_id != deck.topic_id:
                    problems.append(
                        f"slide deck {deck.id}: lecture plan {plan.id} belongs to topic {plan.topic_id}, "
                        f"not {deck.topic_id}"
                    )
        for slot in self.staffing:
            if slot.module_id is not None:
                references.append((f"staff slot {slot.id}.module_id", [slot.module_id], module_ids))

        for label, ids, known in references:
            missing = sorted(set(ids) - known)
            if missing:
                problems.append(f"{label} references unknown id(s): {', '.join(missing)}")
        return problems

    def _template_problems(self) -> list[str]:
        if self.meta.kind is ProgramKind.PROFESSIONAL_DEVELOPMENT:
            unbound = [competence.id for competence in self.competencies if competence.standard_source_id is None]
            problems = [f"competences without a standard: {', '.join(unbound)}"] if unbound else []
            if not any(source.kind is SourceKind.PROFESSIONAL_STANDARD for source in self.sources):
                problems.append("no professional standard source declared")
            return problems
        problems = []
        if not self.summary.focus:
            problems.append("general development programme requires summary.focus")
        if not self.summary.level:
            problems.append("general development programme requires summary.level")
        return problems

    def _hours_problems(self) -> list[str]:
        problems = []
        curriculum = self.curriculum
        total = curriculum.total_hours + self.assessment.final.hours
        if abs(total - self.volume.total_hours) > _TOLERANCE:
            problems.append(
                f"hours: modules {curriculum.total_hours} + final attestation {self.assessment.final.hours} "
                f"!= declared total {self.volume.total_hours}"
            )
        declared_contact = self.volume.declared_contact_hours
        if declared_contact is not None and abs(curriculum.contact_hours - declared_contact) > _TOLERANCE:
            problems.append(f"hours: contact work {curriculum.contact_hours} != declared {declared_contact}")
        declared_self_study = self.volume.declared_self_study_hours
        if declared_self_study is not None and abs(curriculum.self_study_hours - declared_self_study) > _TOLERANCE:
            problems.append(f"hours: self-study {curriculum.self_study_hours} != declared {declared_self_study}")
        return problems
