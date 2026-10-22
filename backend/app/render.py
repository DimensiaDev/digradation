"""Render a :class:`ProgramDraft` to a DOCX document.

:func:`render_program_draft` takes the approved draft — the early blocks a
methodist confirms before full generation — and returns the bytes of a
DOCX file. The document is built from code, not from a template:
Times New Roman 12pt, 2 cm margins, 'Table Grid' for the curriculum.

Rendered from the draft:

- title page (organisation, order placeholder, programme kind and title,
  city and year);
- explanatory note (focus, level, form, issued document, annotation,
  relevance);
- goal and tasks;
- target audience and admission requirements;
- competencies with their experience / skills / knowledge;
- schedule and total hours;
- curriculum as a table;
- sources grouped by kind (normative acts, professional standards,
  primary and secondary literature, internet resources, references).

Not rendered — sections that live in :class:`EducationalProgram`, not in
the draft: assessment materials, teaching materials, delivery
conditions, staffing, cases appendix. They appear as an explicit
"pending" block so a methodist can fill them in by hand or regenerate
once the full document exists.
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.shared import Cm, Pt

from app.agentic.models import (
    AssessmentPlan,
    Competence,
    Hours,
    Materials,
    Module,
    ProgramDraft,
    ProgramKind,
    Source,
    SourceKind,
)
from app.agentic.state import ProgramDocument

ORGANIZATION_LINES: tuple[str, ...] = (
    "Автономная некоммерческая организация",
    "дополнительного профессионального образования",
    "«Академия Информационных Технологий»",
)

DEFAULT_CITY = "Магас"
DEFAULT_YEAR = datetime.now().year

KIND_TITLES: dict[ProgramKind, str] = {
    ProgramKind.PROFESSIONAL_DEVELOPMENT: "ПРОГРАММА ПОВЫШЕНИЯ КВАЛИФИКАЦИИ",
    ProgramKind.GENERAL_DEVELOPMENT: "ДОПОЛНИТЕЛЬНАЯ ОБЩЕРАЗВИВАЮЩАЯ ПРОГРАММА",
}


# --------------------------------------------------------------------------- entry point
def render_program_draft(
    draft: ProgramDraft,
    *,
    city: str = DEFAULT_CITY,
    year: int = DEFAULT_YEAR,
    organization_lines: tuple[str, ...] = ORGANIZATION_LINES,
) -> bytes:
    """Return the DOCX bytes of a programme draft.

    The draft is the skeleton alone: the goal, the competences and the hours.
    The content is not part of it, so the sections that belong to the content
    are listed as still to be written.
    """
    return render_program_document(
        ProgramDocument(draft=draft),
        city=city,
        year=year,
        organization_lines=organization_lines,
    )


def render_program_document(
    document: ProgramDocument,
    *,
    city: str = DEFAULT_CITY,
    year: int = DEFAULT_YEAR,
    organization_lines: tuple[str, ...] = ORGANIZATION_LINES,
) -> bytes:
    """Return the DOCX bytes of a whole programme."""
    draft = document.draft
    doc = Document()
    _set_global_style(doc)

    _render_title_page(doc, draft, city=city, year=year, organization_lines=organization_lines)
    _render_explanatory_note(doc, draft)
    _render_goal_and_tasks(doc, draft)
    _render_target_audience(doc, draft)
    _render_planned_results(doc, draft)
    _render_schedule(doc, draft)
    _render_curriculum(doc, draft)
    _render_sources(doc, draft)
    if document.assessment is not None:
        _render_assessment(doc, document.assessment)
    if document.materials is not None:
        _render_materials(doc, document.materials)
    _render_pending_sections(doc, document)

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


# --------------------------------------------------------------------------- style helpers
def _set_global_style(doc: Document) -> None:
    style = doc.styles["Normal"]
    style.font.name = "Times New Roman"
    style.font.size = Pt(12)
    for section in doc.sections:
        section.left_margin = Cm(2)
        section.right_margin = Cm(2)
        section.top_margin = Cm(2)
        section.bottom_margin = Cm(2)


def _page_break(doc: Document) -> None:
    run = doc.add_paragraph().add_run()
    run.add_break(WD_BREAK.PAGE)


def _heading(doc: Document, text: str, *, level: int) -> None:
    doc.add_heading(text, level=level)


def _labeled(doc: Document, label: str, value: str | None) -> None:
    if not value:
        return
    p = doc.add_paragraph()
    p.add_run(label).bold = True
    p.add_run(f" {value}")


def _bullets(doc: Document, items: list[str]) -> None:
    for item in items:
        doc.add_paragraph(item, style="List Bullet")


# --------------------------------------------------------------------------- title page
def _render_title_page(
    doc: Document,
    draft: ProgramDraft,
    *,
    city: str,
    year: int,
    organization_lines: tuple[str, ...],
) -> None:
    for line in organization_lines:
        p = doc.add_paragraph(line)
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph("")
    doc.add_paragraph("")

    p = doc.add_paragraph("УТВЕРЖДЕНО")
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p = doc.add_paragraph(f"Приказом № ____-ОБР от «___» __________ {year} г.")
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    doc.add_paragraph("")
    doc.add_paragraph("")
    doc.add_paragraph("")

    p = doc.add_paragraph(KIND_TITLES[draft.kind])
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    p = doc.add_paragraph(f"«{draft.title}»")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.runs[0].bold = True

    doc.add_paragraph("")
    doc.add_paragraph("")
    doc.add_paragraph("")
    doc.add_paragraph("")
    doc.add_paragraph("")

    p = doc.add_paragraph(f"{city} {year}")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    _page_break(doc)


# --------------------------------------------------------------------------- explanatory note
def _render_explanatory_note(doc: Document, draft: ProgramDraft) -> None:
    _heading(doc, "Пояснительная записка", level=1)
    s = draft.summary

    _labeled(doc, "Направленность программы:", s.focus)
    _labeled(doc, "Уровень освоения:", s.level)
    _labeled(doc, "Форма обучения:", s.form)
    _labeled(doc, "Документ по итогам обучения:", s.issued_document)

    if s.annotation:
        doc.add_paragraph(s.annotation)

    if s.relevance:
        _heading(doc, "Актуальность программы", level=2)
        doc.add_paragraph(s.relevance)


def _render_goal_and_tasks(doc: Document, draft: ProgramDraft) -> None:
    s = draft.summary
    _heading(doc, "Цель и задачи программы", level=1)
    _labeled(doc, "Цель:", s.goal)
    if s.tasks:
        doc.add_paragraph("Задачи программы:")
        _bullets(doc, s.tasks)


def _render_target_audience(doc: Document, draft: ProgramDraft) -> None:
    s = draft.summary
    _heading(doc, "Категории обучающихся", level=1)
    doc.add_paragraph(s.target_audience)
    _labeled(doc, "Требования к слушателям:", s.admission_requirements)


# --------------------------------------------------------------------------- results
def _render_planned_results(doc: Document, draft: ProgramDraft) -> None:
    _heading(doc, "Планируемые результаты обучения", level=1)

    _heading(doc, "Компетенции", level=2)
    for c in draft.competencies:
        _render_competence(doc, c)

    for title, outcomes in (
        ("Практический опыт", _outcomes(draft.competencies, "experience")),
        ("Умения", _outcomes(draft.competencies, "skills")),
        ("Знания", _outcomes(draft.competencies, "knowledge")),
    ):
        if not outcomes:
            continue
        _heading(doc, title, level=2)
        _bullets(doc, outcomes)


def _render_competence(doc: Document, c: Competence) -> None:
    p = doc.add_paragraph()
    p.add_run(f"{c.id}.").bold = True
    p.add_run(f" {c.statement}")
    if c.code:
        p.add_run(f" (код трудовой функции {c.code})")
    if c.activity:
        p.add_run(f" Вид деятельности: {c.activity}")


def _outcomes(competencies: list[Competence], attr: str) -> list[str]:
    statements: list[str] = []
    for c in competencies:
        for o in getattr(c, attr):
            statements.append(o.statement)
    return statements


# --------------------------------------------------------------------------- schedule
def _render_schedule(doc: Document, draft: ProgramDraft) -> None:
    _heading(doc, "Сроки реализации и календарный учебный график", level=1)
    s = draft.curriculum.schedule
    c = draft.curriculum
    if s.weeks:
        _labeled(doc, "Срок реализации:", f"{s.weeks} нед.")
    if s.days:
        _labeled(doc, "Количество учебных дней:", str(s.days))
    if s.hours_per_day:
        _labeled(doc, "Режим занятий:", f"до {_fmt_hours(s.hours_per_day)} ч в день")
    if s.max_hours_per_day:
        _labeled(doc, "Предельная дневная нагрузка:", f"{_fmt_hours(s.max_hours_per_day)} ч в день")
    if s.note:
        doc.add_paragraph(s.note)

    _labeled(doc, "Общая трудоёмкость:", f"{_fmt_hours(c.total_hours)} ч")
    if c.distance_hours is not None:
        _labeled(
            doc,
            "Дистанционная работа:",
            f"{_fmt_hours(c.distance_hours)} ч, очно {_fmt_hours(c.in_person_hours)} ч",
        )


# --------------------------------------------------------------------------- curriculum table
CURRICULUM_HEADERS: tuple[str, ...] = (
    "№ п/п",
    "Наименование разделов и тем",
    "Всего",
    "Аудиторные",
    "Лекции",
    "Практика",
    "Лабораторные",
    "СРС",
)


def _render_curriculum(doc: Document, draft: ProgramDraft) -> None:
    _heading(doc, "Учебный план", level=1)

    rows: list[list[str]] = []
    for i, module in enumerate(draft.curriculum.modules, start=1):
        rows.append(_module_row(i, module))
        for topic in module.topics:
            rows.append(_topic_row(topic))

    c = draft.curriculum
    if c.attestation_hours:
        # The attestation is the last row of the plan, so the rows add up to
        # the total the plan states.
        rows.append(
            [
                "",
                "Итоговая аттестация",
                _fmt_hours(c.attestation_hours),
                _fmt_hours(c.attestation_hours),
                "",
                "",
                "",
                "",
            ]
        )
    rows.append(
        [
            "",
            "Итого",
            _fmt_hours(c.total_hours),
            _fmt_hours(c.contact_hours),
            _fmt_hours(_sum_lecture(c.modules)),
            _fmt_hours(_sum_practice(c.modules)),
            _fmt_hours(_sum_lab(c.modules)),
            _fmt_hours(c.self_study_hours),
        ]
    )

    _add_table(doc, CURRICULUM_HEADERS, rows)


def _module_row(index: int, module: Module) -> list[str]:
    totals = _module_totals(module)
    return [
        str(index),
        module.title,
        _fmt_hours(totals["total"]),
        _fmt_hours(totals["contact"]),
        _fmt_hours(totals["lecture"]),
        _fmt_hours(totals["practice"]),
        _fmt_hours(totals["lab"]),
        _fmt_hours(totals["self_study"]),
    ]


def _topic_row(topic) -> list[str]:
    h: Hours = topic.hours
    return [
        "",
        f"  {topic.id} {topic.title}",
        _fmt_hours(_total(h)),
        _fmt_hours(_contact(h)),
        _fmt_hours(h.lecture),
        _fmt_hours(h.practice),
        _fmt_hours(h.lab),
        _fmt_hours(h.self_study),
    ]


def _module_totals(module: Module) -> dict[str, float]:
    lecture = sum(t.hours.lecture for t in module.topics)
    practice = sum(t.hours.practice for t in module.topics)
    lab = sum(t.hours.lab for t in module.topics)
    self_study = sum(t.hours.self_study for t in module.topics)
    return {
        "lecture": lecture,
        "practice": practice,
        "lab": lab,
        "self_study": self_study,
        "contact": lecture + practice + lab,
        "total": lecture + practice + lab + self_study,
    }


def _total(h: Hours) -> float:
    return h.lecture + h.practice + h.lab + h.self_study


def _contact(h: Hours) -> float:
    return h.lecture + h.practice + h.lab


def _sum_lecture(modules: list[Module]) -> float:
    return sum(t.hours.lecture for m in modules for t in m.topics)


def _sum_practice(modules: list[Module]) -> float:
    return sum(t.hours.practice for m in modules for t in m.topics)


def _sum_lab(modules: list[Module]) -> float:
    return sum(t.hours.lab for m in modules for t in m.topics)


def _fmt_hours(value: float) -> str:
    if value == 0:
        return "—"
    if value == int(value):
        return str(int(value))
    return f"{value:g}".replace(".", ",")


def _add_table(doc: Document, headers: tuple[str, ...], rows: list[list[str]]) -> None:
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"

    header_cells = table.rows[0].cells
    for i, text in enumerate(headers):
        header_cells[i].text = text
        for paragraph in header_cells[i].paragraphs:
            for run in paragraph.runs:
                run.bold = True

    for row in rows:
        cells = table.add_row().cells
        for i, text in enumerate(row):
            cells[i].text = text


# --------------------------------------------------------------------------- sources
def _render_sources(doc: Document, draft: ProgramDraft) -> None:
    if not draft.sources:
        return

    _heading(doc, "Литература и источники", level=1)

    for title, items in _group_sources(draft.sources):
        if not items:
            continue
        _heading(doc, title, level=2)
        for source in items:
            doc.add_paragraph(_format_source(source), style="List Number")


def _group_sources(
    sources: list[Source],
) -> list[tuple[str, list[Source]]]:
    normative = [s for s in sources if s.kind is SourceKind.NORMATIVE_ACT]
    standards = [s for s in sources if s.kind is SourceKind.PROFESSIONAL_STANDARD]
    primary = [s for s in sources if s.kind is SourceKind.LITERATURE and s.primary is True]
    secondary = [s for s in sources if s.kind is SourceKind.LITERATURE and s.primary is not True]
    internet = [s for s in sources if s.kind is SourceKind.INTERNET_RESOURCE]
    references = [s for s in sources if s.kind is SourceKind.REFERENCE]

    return [
        ("Нормативные акты", normative),
        ("Профессиональные стандарты", standards),
        ("Основная литература", primary),
        ("Дополнительная литература", secondary),
        ("Интернет-ресурсы", internet),
        ("Справочные материалы", references),
    ]


def _format_source(source: Source) -> str:
    parts = [source.title]
    if source.identifier:
        parts.append(source.identifier)
    if source.url:
        parts.append(source.url)
    text = " — ".join(parts)
    if not source.confirmed:
        # Nobody verified the document, so the document says so too.
        text += " (не подтверждён каталогом источников)"
    return text


# --------------------------------------------------------------------------- content
def _render_assessment(doc: Document, assessment: AssessmentPlan) -> None:
    """The means of checking: the attestation, the current control, the questions."""
    _page_break(doc)
    _heading(doc, "Оценочные материалы", level=1)

    _heading(doc, "Итоговая аттестация", level=2)
    _labeled(doc, "Форма:", assessment.final.form)
    _labeled(doc, "Количество часов:", f"{_fmt_hours(assessment.final.hours)} ч")
    _bullets(doc, assessment.final.criteria)
    if assessment.final.materials:
        _bullets(doc, assessment.final.materials)

    if assessment.tools:
        _heading(doc, "Текущий контроль", level=2)
        for tool in assessment.tools:
            _heading(doc, tool.title, level=3)
            _labeled(doc, "Форма контроля:", tool.kind.value)
            if tool.topic_ids:
                _labeled(doc, "Темы:", ", ".join(tool.topic_ids))
            if tool.outcome_ids:
                _labeled(doc, "Проверяемые результаты:", ", ".join(tool.outcome_ids))
            if tool.criteria:
                _bullets(doc, tool.criteria)
            if tool.materials:
                _bullets(doc, tool.materials)

    if assessment.questions:
        _heading(doc, "Тестовые вопросы", level=2)
        for number, question in enumerate(assessment.questions, start=1):
            _heading(doc, f"Вопрос {number}", level=3)
            doc.add_paragraph(question.text)
            for option in question.options:
                doc.add_paragraph(option, style="List Bullet")
            if question.answers:
                _labeled(doc, "Правильный ответ:", ", ".join(question.answers))

    if assessment.seminar_tasks:
        _heading(doc, "Задания для семинаров", level=2)
        for number, task in enumerate(assessment.seminar_tasks, start=1):
            _heading(doc, f"Задание {number}. {task.title}", level=3)
            doc.add_paragraph(task.statement)
            _labeled(doc, "Тема:", task.topic_id)
            if task.outcome_ids:
                _labeled(doc, "Проверяемые результаты:", ", ".join(task.outcome_ids))
            if task.criteria:
                _bullets(doc, task.criteria)


def _render_materials(doc: Document, materials: Materials) -> None:
    """The lecture plans and the slides."""
    if materials.lecture_plans:
        _page_break(doc)
        _heading(doc, "Методические материалы", level=1)
        for number, plan in enumerate(materials.lecture_plans, start=1):
            _heading(doc, f"{number}. {plan.title}", level=2)
            _labeled(doc, "Тема:", plan.topic_id)
            _bullets(doc, plan.plan)
            if plan.summary:
                doc.add_paragraph(plan.summary)

    if materials.slide_decks:
        _page_break(doc)
        _heading(doc, "Слайды", level=1)
        for deck in materials.slide_decks:
            _heading(doc, deck.title, level=2)
            _labeled(doc, "Тема:", deck.topic_id)
            for number, slide in enumerate(deck.slides, start=1):
                _heading(doc, f"Слайд {number}. {slide.title}", level=3)
                _bullets(doc, slide.bullets)


# --------------------------------------------------------------------------- pending
def _render_pending_sections(doc: Document, document: ProgramDocument) -> None:
    """The sections the document does not carry yet.

    A programme that has its assessment and its materials does not say that
    they are still to be written; only the sections nothing was written for
    are listed.
    """
    missing: list[str] = []
    if document.assessment is None:
        missing.append("Оценочные материалы")
    if document.materials is None:
        missing.append("Методические материалы")
    missing += [
        "Условия реализации программы",
        "Кадровое обеспечение",
        "Приложение № 1. Перечень кейсов",
    ]
    _page_break(doc)
    _heading(
        doc,
        "Разделы, заполняемые на следующем этапе генерации",
        level=1,
    )
    p = doc.add_paragraph()
    p.add_run(
        "Следующие разделы формируются из полного документа программы "
        "(EducationalProgram) и в этой заготовке намеренно оставлены "
        "пустыми:"
    ).italic = True

    for title in missing:
        doc.add_paragraph(title, style="List Bullet")

    doc.add_paragraph("")
    p = doc.add_paragraph()
    p.add_run("Данная заготовка предназначена для ручной доработки методистом.").italic = True
