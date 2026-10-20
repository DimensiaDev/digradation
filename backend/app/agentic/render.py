"""Render an approved-ready draft as the text the methodist reads in chat.

The draft is stored as structured blocks; the chat needs a representation a
person can review and confirm. The renderer is pure and deterministic: the same
draft always produces the same text, which is what lets the final message be
asserted on and lets a retry overwrite it without drift.
"""

from __future__ import annotations

from app.agentic.models import ProgramDraft, ProgramKind

KIND_LABELS = {
    ProgramKind.PROFESSIONAL_DEVELOPMENT: "Повышение квалификации",
    ProgramKind.GENERAL_DEVELOPMENT: "Общеразвивающая программа",
}


def _hours(topic) -> str:
    """Human-readable split of one topic's hours, omitting zero parts."""
    labels = (
        ("лек", topic.hours.lecture),
        ("пр", topic.hours.practice),
        ("лаб", topic.hours.lab),
        ("срс", topic.hours.self_study),
    )
    parts = [f"{label} {value:g}" for label, value in labels if value]
    return ", ".join(parts) if parts else "0"


def render_draft(draft: ProgramDraft) -> str:
    """Format the reviewable blocks of a draft as chat text."""
    lines: list[str] = [
        "Черновик программы готов к подтверждению.",
        "",
        f"Тип: {KIND_LABELS.get(draft.kind, draft.kind.value)}",
        f"Название: {draft.title}",
        f"Объём: {draft.curriculum.total_hours:g} ак. ч.",
        "",
        "Цель:",
        draft.summary.goal,
        "",
        f"Целевая аудитория: {draft.summary.target_audience}",
        f"Форма обучения: {draft.summary.form}",
        f"Документ по итогам: {draft.summary.issued_document}",
    ]

    if draft.summary.tasks:
        lines.extend(["", "Задачи:"])
        lines.extend(f"- {task}" for task in draft.summary.tasks)

    lines.extend(["", "Учебный план:"])
    for module in draft.curriculum.modules:
        lines.append(f"- {module.title}")
        for topic in module.topics:
            lines.append(f"  - {topic.title}: {_hours(topic)}")

    schedule = draft.curriculum.schedule
    schedule_parts = []
    if schedule.weeks is not None:
        schedule_parts.append(f"{schedule.weeks} нед.")
    if schedule.days is not None:
        schedule_parts.append(f"{schedule.days} дн.")
    if schedule.hours_per_day is not None:
        schedule_parts.append(f"{schedule.hours_per_day:g} ч/день")
    if schedule_parts:
        lines.append("График: " + ", ".join(schedule_parts))

    lines.extend(["", "Компетенции:"])
    for competence in draft.competencies:
        lines.append(f"- {competence.id}: {competence.statement}")
        for outcome in competence.knowledge:
            lines.append(f"  знание: {outcome.statement}")
        for outcome in competence.skills:
            lines.append(f"  умение: {outcome.statement}")
        for outcome in competence.experience:
            lines.append(f"  опыт: {outcome.statement}")

    if draft.sources:
        lines.extend(["", "Источники:"])
        for source in draft.sources:
            identifier = f" ({source.identifier})" if source.identifier else ""
            lines.append(f"- {source.title}{identifier}")

    lines.extend(["", "Напишите «подтверждаю», чтобы утвердить черновик и перейти к следующему этапу."])
    return "\n".join(lines)
