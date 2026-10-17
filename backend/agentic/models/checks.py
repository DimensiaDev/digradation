from __future__ import annotations

from collections import defaultdict
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .program import EducationalProgram, SourceKind

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


def check_program(program: EducationalProgram) -> CheckReport:
    return CheckReport(
        items=[
            _hours_item(program),
            *_coverage_items(program),
            *_source_items(program),
            *_completeness_items(program),
            *_placeholder_items(program),
        ]
    )


def validate_consistent(program: EducationalProgram) -> CheckReport:
    report = check_program(program)
    if not report.ok:
        raise ProgramConsistencyError(report)
    return report


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
        attribute(question.outcome_ids, question.id)
    for task in program.assessment.seminar_tasks:
        attribute(task.outcome_ids, task.id)
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


def _source_items(program: EducationalProgram) -> list[CheckItem]:
    items: list[CheckItem] = []
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
        if source.confirmed:
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
    lecture_hours = sum(topic.hours.lecture for module in program.curriculum.modules for topic in module.topics)
    if lecture_hours > 0 and not program.materials.lecture_plans:
        items.append(
            CheckItem(
                code="materials.lecture_plans",
                severity=CheckSeverity.ERROR,
                message=f"Есть лекционные часы ({_fmt(lecture_hours)} ч), но нет ни одного плана лекций",
            )
        )
    if not program.materials.slide_decks:
        items.append(
            CheckItem(
                code="materials.slides",
                severity=CheckSeverity.ERROR,
                message="Нет слайдов ни для одного модуля",
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
