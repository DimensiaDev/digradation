from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .program import EducationalProgram, ProgramStatus, SourceKind

_FINAL_ATTESTATION = "assessment.final"
_NORMATIVE = (SourceKind.NORMATIVE_ACT, SourceKind.PROFESSIONAL_STANDARD)


class CheckSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class CheckItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    severity: CheckSeverity
    message: str
    element_ids: list[str] = Field(default_factory=list)


class CheckReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[CheckItem] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def errors(self) -> list[CheckItem]:
        return [item for item in self.items if item.severity is CheckSeverity.ERROR]

    @property
    def warnings(self) -> list[CheckItem]:
        return [item for item in self.items if item.severity is CheckSeverity.WARNING]


class ProgramConsistencyError(ValueError):
    def __init__(self, report: CheckReport) -> None:
        self.report = report
        problems = "; ".join(f"{item.code}: {item.message}" for item in report.errors)
        super().__init__(f"program consistency check failed: {problems}")


def check_program(program: EducationalProgram, confirmed_source_ids: Collection[str] = ()) -> CheckReport:
    problems = program.problems()
    items = [CheckItem(code="structure.invalid", severity=CheckSeverity.ERROR, message=problem) for problem in problems]
    if not problems:
        items.append(_hours_item(program))
    items.extend(_coverage_items(program))
    items.extend(_source_items(program, confirmed_source_ids))
    items.extend(_completeness_items(program))
    items.extend(_placeholder_items(program))
    return CheckReport(items=items)


def validate_consistent(program: EducationalProgram, confirmed_source_ids: Collection[str] = ()) -> CheckReport:
    report = check_program(program, confirmed_source_ids)
    if not report.ok:
        raise ProgramConsistencyError(report)
    return report


def verify(program: EducationalProgram, confirmed_source_ids: Collection[str] = ()) -> EducationalProgram:
    if program.meta.status is not ProgramStatus.APPROVED:
        raise ValueError(f"verify requires an approved program, got {program.meta.status}")
    validate_consistent(program, confirmed_source_ids)
    return program.model_copy(update={"meta": program.meta.model_copy(update={"status": ProgramStatus.VERIFIED})})


def load_program(raw: dict[str, Any], confirmed_source_ids: Collection[str] = ()) -> EducationalProgram:
    meta = raw.get("meta")
    claims_verified = isinstance(meta, dict) and meta.get("status") == ProgramStatus.VERIFIED
    if not claims_verified:
        return EducationalProgram.model_validate(raw)
    program = EducationalProgram.model_validate({**raw, "meta": {**meta, "status": ProgramStatus.APPROVED.value}})
    return verify(program, confirmed_source_ids)


def _fmt(value: float) -> str:
    return f"{value:g}"


def _hours_item(program: EducationalProgram) -> CheckItem:
    curriculum = program.curriculum
    return CheckItem(
        code="hours.sums",
        severity=CheckSeverity.INFO,
        message=(
            f"Суммы сходятся: {_fmt(curriculum.total_hours)} ч по разделам + "
            f"{_fmt(program.assessment.final.hours)} ч итоговой аттестации = "
            f"{_fmt(program.volume.total_hours)} ч; контактная работа {_fmt(curriculum.contact_hours)} ч, "
            f"самостоятельная работа {_fmt(curriculum.self_study_hours)} ч"
        ),
    )


def _coverage_items(program: EducationalProgram) -> list[CheckItem]:
    items: list[CheckItem] = []
    topics_by_outcome: dict[str, list[str]] = defaultdict(list)
    for module in program.curriculum.modules:
        for topic in module.topics:
            if not topic.outcome_ids:
                items.append(
                    CheckItem(
                        code="coverage.topic_outcomes",
                        severity=CheckSeverity.WARNING,
                        message=f"Тема {topic.id} не связана ни с одним результатом обучения",
                        element_ids=[topic.id],
                    )
                )
            for outcome_id in topic.outcome_ids:
                topics_by_outcome[outcome_id].append(topic.id)

    instruments_by_outcome: dict[str, list[str]] = defaultdict(list)

    def attribute(outcome_ids: list[str], instrument: str) -> None:
        for outcome_id in outcome_ids:
            instruments_by_outcome[outcome_id].append(instrument)

    for tool in program.assessment.tools:
        attribute(tool.outcome_ids, tool.id)
    for question in program.assessment.questions:
        attribute(question.outcome_ids, question.tool_id)
    for task in program.assessment.seminar_tasks:
        attribute(task.outcome_ids, task.tool_id or task.id)
    attribute(program.assessment.final.outcome_ids, _FINAL_ATTESTATION)

    for outcome in program.iter_outcomes():
        if not topics_by_outcome.get(outcome.id):
            items.append(
                CheckItem(
                    code="coverage.outcome_topic",
                    severity=CheckSeverity.ERROR,
                    message=f"Результат обучения {outcome.id} не раскрыт ни одной темой",
                    element_ids=[outcome.id],
                )
            )
        instruments = sorted(set(instruments_by_outcome.get(outcome.id, [])))
        if len(instruments) < 2:
            items.append(
                CheckItem(
                    code="coverage.outcome_instruments",
                    severity=CheckSeverity.ERROR,
                    message=(
                        f"Результат обучения {outcome.id} проверяется менее чем двумя "
                        f"оценочными средствами ({len(instruments)})"
                    ),
                    element_ids=[outcome.id, *instruments],
                )
            )
    return items


def _source_items(program: EducationalProgram, confirmed_source_ids: Collection[str]) -> list[CheckItem]:
    items: list[CheckItem] = []
    confirmed = set(confirmed_source_ids)
    normative = [source for source in program.sources if source.kind in _NORMATIVE]
    if not normative:
        items.append(
            CheckItem(
                code="sources.normative_basis",
                severity=CheckSeverity.ERROR,
                message="Нормативная база не заявлена: нужен хотя бы один нормативный акт или профстандарт",
            )
        )
    for source in program.sources:
        if source.id in confirmed:
            continue
        if source.kind in _NORMATIVE:
            items.append(
                CheckItem(
                    code="sources.normative_unconfirmed",
                    severity=CheckSeverity.ERROR,
                    message=f"Нормативный источник «{source.title}» не подтверждён",
                )
            )
        else:
            items.append(
                CheckItem(
                    code="sources.unconfirmed",
                    severity=CheckSeverity.WARNING,
                    message=f"Источник «{source.title}» не подтверждён базой знаний",
                )
            )
    return items


def _completeness_items(program: EducationalProgram) -> list[CheckItem]:
    items: list[CheckItem] = []
    planned_topics = {plan.topic_id for plan in program.materials.lecture_plans}
    decked_topics = {deck.topic_id for deck in program.materials.slide_decks}
    for module in program.curriculum.modules:
        if not {topic.id for topic in module.topics} & decked_topics:
            items.append(
                CheckItem(
                    code="materials.slides",
                    severity=CheckSeverity.ERROR,
                    message=f"В модуле {module.id} нет слайдов ни по одной теме",
                    element_ids=[module.id],
                )
            )
        for topic in module.topics:
            if topic.hours.lecture > 0 and topic.id not in planned_topics:
                items.append(
                    CheckItem(
                        code="materials.lecture_plans",
                        severity=CheckSeverity.ERROR,
                        message=f"Для темы {topic.id} с {_fmt(topic.hours.lecture)} ч лекций нет плана лекции",
                        element_ids=[topic.id],
                    )
                )
    if not (program.assessment.tools or program.assessment.questions or program.assessment.seminar_tasks):
        items.append(
            CheckItem(
                code="assessment.current_control",
                severity=CheckSeverity.ERROR,
                message="Не описан текущий контроль: ни оценочных средств, ни тестовых вопросов, ни заданий",
            )
        )
    return items


def _placeholder_items(program: EducationalProgram) -> list[CheckItem]:
    items: list[CheckItem] = []
    for slot in program.staffing:
        if slot.fio is not None:
            items.append(
                CheckItem(
                    code="placeholders.staffing",
                    severity=CheckSeverity.WARNING,
                    message=f"В штатной позиции {slot.id} заполнено ФИО «{slot.fio}» — проверьте вручную",
                    element_ids=[slot.id],
                )
            )
    approval = program.approval
    if approval.order_number is not None or approval.order_date is not None or approval.signatory is not None:
        items.append(
            CheckItem(
                code="placeholders.approval",
                severity=CheckSeverity.WARNING,
                message="Реквизиты приказа заполнены — проверьте вручную",
            )
        )
    return items
