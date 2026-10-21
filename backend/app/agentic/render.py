"""Render an assembled draft into the message the methodist reads.

Ids stay visible next to the things they name, because the methodist corrects
the model by id; bookkeeping — attempts, versions, the run id — stays out.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.agentic.models import Hours, ProgramDraft, ProgramKind, Schedule, SourceKind
from app.agentic.state import ClarificationAnswer, ProgramState

PROGRAM_KIND_NAMES = {
    ProgramKind.PROFESSIONAL_DEVELOPMENT: "повышение квалификации",
    ProgramKind.GENERAL_DEVELOPMENT: "общеразвивающая программа",
}
SOURCE_KIND_NAMES = {
    SourceKind.NORMATIVE_ACT: "нормативный акт",
    SourceKind.PROFESSIONAL_STANDARD: "профессиональный стандарт",
    SourceKind.LITERATURE: "литература",
    SourceKind.INTERNET_RESOURCE: "интернет-ресурс",
    SourceKind.REFERENCE: "справочное издание",
}

#: What the methodist is asked to answer. Kept next to the renderer because the
#: approval phrase is matched literally by the chat worker.
CONFIRMATION_PROMPT = (
    "Черновик готов. Проверьте его и ответьте «подтверждаю», если всё верно. "
    "Замечания к программе можно обсудить в этом же чате после утверждения."
)


def render_answers(state: ProgramState, answers: Iterable[ClarificationAnswer]) -> str:
    """The methodist's answers as the chat shows them, next to the questions.

    The answers are structured data on their way in, but the chat is prose, so
    the transcript repeats them next to the question each one answers.
    """
    by_id = {answer.question_id: answer for answer in answers}
    lines = ["Ответы на уточняющие вопросы по программе:"]
    for question in state.questions:
        answer = by_id.get(question.id)
        if answer is not None:
            lines.append(f"— {question.text}: {answer.text}")
    return "\n".join(lines)


def _hours(value: float) -> str:
    """A number of hours without a trailing ``.0``."""
    return f"{value:g}"


def _schedule(schedule: Schedule) -> str:
    """The schedule as one sentence, without the closing full stop."""
    parts: list[str] = []
    if schedule.weeks is not None:
        parts.append(f"{schedule.weeks} нед.")
    if schedule.days is not None:
        parts.append(f"{schedule.days} дн.")
    if schedule.hours_per_day is not None:
        parts.append(f"{_hours(schedule.hours_per_day)} ч в день")
    text = ", ".join(parts)
    if schedule.note:
        note = schedule.note.strip().rstrip(".")
        text = f"{text}; {note}" if text else note
    return text


def _topic(topic_hours: Hours, control_form: str | None) -> str:
    """The hour split of one topic, with the parts that are not zero."""
    split = ", ".join(
        f"{label} {_hours(value)}"
        for label, value in (
            ("лекции", topic_hours.lecture),
            ("практика", topic_hours.practice),
            ("лабораторные", topic_hours.lab),
            ("самостоятельная работа", topic_hours.self_study),
        )
        if value
    )
    total = topic_hours.lecture + topic_hours.practice + topic_hours.lab + topic_hours.self_study
    text = f"{_hours(total)} ч" + (f" ({split})" if split else "")
    return f"{text}; контроль: {control_form}" if control_form else text


def render_draft(draft: ProgramDraft) -> str:
    """The draft as the chat message that asks for confirmation."""
    sources = {source.id: source for source in draft.sources}
    lines: list[str] = [
        f"# {draft.title}",
        "",
        f"**Вид программы:** {PROGRAM_KIND_NAMES[draft.kind]}.",
        "",
        "## Паспорт программы",
        "",
        f"**Цель:** {draft.summary.goal}",
    ]
    if draft.summary.tasks:
        lines.extend(["", "**Задачи:**"])
        lines.extend(f"{number}. {task}" for number, task in enumerate(draft.summary.tasks, start=1))
    for label, value in (
        ("Целевая аудитория", draft.summary.target_audience),
        ("Требования к слушателям", draft.summary.admission_requirements),
        ("Форма обучения", draft.summary.form),
        ("Документ по итогам", draft.summary.issued_document),
        ("Направленность", draft.summary.focus),
        ("Уровень освоения", draft.summary.level),
        ("Аннотация", draft.summary.annotation),
        ("Актуальность", draft.summary.relevance),
    ):
        if value:
            lines.extend(["", f"**{label}:** {value}"])
    if draft.summary.basis_source_ids:
        cited = [sources[source_id].title for source_id in draft.summary.basis_source_ids if source_id in sources]
        if cited:
            lines.extend(["", f"**Разработана на основе:** {'; '.join(cited)}."])

    lines.extend(["", "## Компетенции и результаты освоения"])
    for number, competence in enumerate(draft.competencies, start=1):
        code = f", код {competence.code}" if competence.code else ""
        lines.extend(["", f"### Компетенция {number}{code}", "", f"{competence.statement} ({competence.id})"])
        if competence.activity:
            lines.extend(["", f"Виды деятельности: {competence.activity}."])
        for label, outcomes in (
            ("Знать", competence.knowledge),
            ("Уметь", competence.skills),
            ("Владеть", competence.experience),
        ):
            lines.extend(f"- {label}: {outcome.statement} ({outcome.id})" for outcome in outcomes)

    lines.extend(["", "## Учебный план"])
    for module in draft.curriculum.modules:
        lines.extend(["", f"### {module.title}", "", f"*{module.id}*"])
        for topic in module.topics:
            lines.append(f"- {topic.title} ({topic.id}) — {_topic(topic.hours, topic.control_form)}")
    curriculum = draft.curriculum
    lines.extend(
        [
            "",
            (
                f"**Итого:** {_hours(curriculum.total_hours)} ч, в том числе аудиторных "
                f"{_hours(curriculum.contact_hours)} ч и самостоятельной работы "
                f"{_hours(curriculum.self_study_hours)} ч."
            ),
            f"**График:** {_schedule(curriculum.schedule)}.",
        ]
    )

    lines.extend(["", "## Источники", ""])
    if draft.sources:
        for source in draft.sources:
            identifier = f" {source.identifier}" if source.identifier else ""
            url = f" — {source.url}" if source.url else ""
            lines.append(f"- {source.title}{identifier} ({SOURCE_KIND_NAMES[source.kind]}){url}")
    else:
        lines.append("Источники не подобраны.")

    lines.extend(["", "---", "", CONFIRMATION_PROMPT])
    return "\n".join(lines)
