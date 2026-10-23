"""Render a :class:`ProgramDraft` to a DOCX document.

:func:`render_program_draft` takes the approved draft — the early blocks a
methodist confirms before full generation — and returns the bytes of a
DOCX file. The document is built from code rather than from a binary template
file, but it follows the sections of the АЦР template: Times New Roman 12pt,
2 cm margins, 'Table Grid' for the curriculum.

Rendered from the full document:

- title page (organisation, order placeholder, programme kind, title, city
  and year);
- explanatory note (focus, level, form, issued document, annotation,
  relevance, and the actualization/adaptation mode when it is not a new one);
- goal and tasks;
- target audience and admission requirements;
- competencies with their experience / skills / knowledge;
- schedule and total hours;
- curriculum as a table;
- sources grouped by kind (normative acts, professional standards,
  primary and secondary literature, internet resources, references);
- assessment, lecture plans and slides;
- delivery conditions, staffing and the cases appendix;
- signatures and the self-check report.

A draft that has no content yet still renders: the sections that are not there
are listed as pending rather than silently omitted.
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO

from docx import Document
from docx.document import Document as DocxDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from app.agentic.models import (
    ApprovalInfo,
    AssessmentPlan,
    CaseStudy,
    Competence,
    Conditions,
    Hours,
    Materials,
    Module,
    ProgramDraft,
    ProgramFormat,
    ProgramKind,
    ProgramMode,
    Source,
    SourceKind,
    StaffSlot,
)
from app.agentic.state import ProgramDocument, ProgramReport

ORGANIZATION_LINES: tuple[str, ...] = (
    "Автономная некоммерческая организация",
    "дополнительного профессионального образования",
    "«Академия Информационных Технологий»",
)

DEFAULT_CITY = "Магас"
DEFAULT_YEAR = datetime.now().year

KIND_TITLES: dict[ProgramKind, str] = {
    ProgramKind.PROFESSIONAL_DEVELOPMENT: "ДОПОЛНИТЕЛЬНАЯ ПРОФЕССИОНАЛЬНАЯ ПРОГРАММА ПОВЫШЕНИЯ КВАЛИФИКАЦИИ",
    ProgramKind.GENERAL_DEVELOPMENT: "ДОПОЛНИТЕЛЬНАЯ ОБЩЕРАЗВИВАЮЩАЯ ПРОГРАММА",
}

INTERNSHIP_SUFFIX = "В ФОРМЕ СТАЖИРОВКИ"

MODE_TITLES: dict[ProgramMode, str] = {
    ProgramMode.NEW: "новая программа",
    ProgramMode.ACTUALIZATION: "актуализация действующей программы",
    ProgramMode.ADAPTATION: "адаптация действующей программы",
}

_MONTHS = (
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def _kind_title(draft: ProgramDraft) -> str:
    """The programme's title line on the cover."""
    title = KIND_TITLES[draft.kind]
    if draft.format is ProgramFormat.INTERNSHIP:
        return f"{title} {INTERNSHIP_SUFFIX}"
    return title


def _approval_line(approval: ApprovalInfo, year: int) -> str:
    """The order line, filled in where the methodist has filled it."""
    number = approval.order_number or "____"
    if approval.order_date is not None:
        date = f"«{approval.order_date.day:02d}» {_MONTHS[approval.order_date.month]} {approval.order_date.year}"
    else:
        date = f"«___» __________ {year}"
    return f"Приказом № {number}-ОБР от {date} г."


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

    _render_title_page(
        doc,
        draft,
        approval=document.approval,
        city=city,
        year=year,
        organization_lines=organization_lines,
    )
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
        conditions = document.materials.conditions
        if conditions.equipment or conditions.teaching_materials:
            _render_conditions(doc, conditions)
        if conditions.staffing_requirements or document.materials.staffing:
            _render_staffing(doc, conditions.staffing_requirements, document.materials.staffing)
        if document.materials.cases:
            _render_cases(doc, document.materials.cases)
    _render_signatures(doc, document.approval)
    if document.report is not None:
        _render_report(doc, document.report)
    _render_pending_sections(doc, document)

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


# --------------------------------------------------------------------------- style helpers
def _set_global_style(doc: DocxDocument) -> None:
    style = doc.styles["Normal"]
    style.font.name = "Times New Roman"
    style.font.size = Pt(12)
    # The name alone only covers the ascii and the hAnsi faces; the Russian
    # text is resolved through the complex-script face in most readers.
    fonts = style.element.rPr.rFonts
    fonts.set(qn("w:eastAsia"), "Times New Roman")
    fonts.set(qn("w:cs"), "Times New Roman")
    for section in doc.sections:
        section.left_margin = Cm(2)
        section.right_margin = Cm(2)
        section.top_margin = Cm(2)
        section.bottom_margin = Cm(2)


def _page_break(doc: DocxDocument) -> None:
    run = doc.add_paragraph().add_run()
    run.add_break(WD_BREAK.PAGE)


def _heading(doc: DocxDocument, text: str, *, level: int) -> None:
    doc.add_heading(text, level=level)


def _labeled(doc: DocxDocument, label: str, value: str | None) -> None:
    if not value:
        return
    p = doc.add_paragraph()
    p.add_run(label).bold = True
    p.add_run(f" {value}")


def _bullets(doc: DocxDocument, items: list[str]) -> None:
    for item in items:
        doc.add_paragraph(item, style="List Bullet")


# --------------------------------------------------------------------------- title page
def _render_title_page(
    doc: DocxDocument,
    draft: ProgramDraft,
    *,
    approval: ApprovalInfo,
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
    p = doc.add_paragraph(_approval_line(approval, year))
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    doc.add_paragraph("")
    doc.add_paragraph("")
    doc.add_paragraph("")

    p = doc.add_paragraph(_kind_title(draft))
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
def _render_explanatory_note(doc: DocxDocument, draft: ProgramDraft) -> None:
    _heading(doc, "Пояснительная записка", level=1)
    s = draft.summary

    _labeled(doc, "Направленность программы:", s.focus)
    _labeled(doc, "Уровень освоения:", s.level)
    _labeled(doc, "Форма обучения:", s.form)
    _labeled(doc, "Документ по итогам обучения:", s.issued_document)
    if draft.mode is not ProgramMode.NEW:
        _labeled(doc, "Режим разработки:", MODE_TITLES[draft.mode])

    if s.annotation:
        doc.add_paragraph(s.annotation)

    if s.relevance:
        _heading(doc, "Актуальность программы", level=2)
        doc.add_paragraph(s.relevance)

    if s.basis_source_ids:
        _labeled(doc, "Разработана на основе:", ", ".join(s.basis_source_ids))


def _render_goal_and_tasks(doc: DocxDocument, draft: ProgramDraft) -> None:
    s = draft.summary
    _heading(doc, "Цель и задачи программы", level=1)
    _labeled(doc, "Цель:", s.goal)
    if s.tasks:
        doc.add_paragraph("Задачи программы:")
        _bullets(doc, s.tasks)


def _render_target_audience(doc: DocxDocument, draft: ProgramDraft) -> None:
    s = draft.summary
    _heading(doc, "Категории обучающихся", level=1)
    doc.add_paragraph(s.target_audience)
    _labeled(doc, "Требования к слушателям:", s.admission_requirements)


# --------------------------------------------------------------------------- results
def _render_planned_results(doc: DocxDocument, draft: ProgramDraft) -> None:
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


def _render_competence(doc: DocxDocument, c: Competence) -> None:
    p = doc.add_paragraph()
    p.add_run(f"{c.id}.").bold = True
    p.add_run(f" {c.statement}")
    if c.code:
        p.add_run(f" (код трудовой функции {c.code})")
    if c.activity:
        p.add_run(f" Вид деятельности: {c.activity}")
    if c.standard_source_id:
        # The competence rests on a professional standard, and the reader has to
        # see which one: it is what the bibliography entry is checked against.
        p.add_run(f" Профессиональный стандарт: {c.standard_source_id}")


def _outcomes(competencies: list[Competence], attr: str) -> list[str]:
    """The statements of one kind of outcome, each with its identifier.

    The identifier is what the curriculum and the assessment refer to, so a
    reader of the document can trace a result back to the topic that teaches
    it.
    """
    statements: list[str] = []
    for c in competencies:
        for o in getattr(c, attr):
            statements.append(f"{o.id}. {o.statement}")
    return statements


# --------------------------------------------------------------------------- schedule
def _render_schedule(doc: DocxDocument, draft: ProgramDraft) -> None:
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

    _labeled(doc, "Общая трудоёмкость:", f"{_fmt_total(c.total_hours)} ч")
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


def _render_curriculum(doc: DocxDocument, draft: ProgramDraft) -> None:
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
    """An hour count for the tables, where no hours in a column is a dash."""
    if value == 0:
        return "—"
    if value == int(value):
        return str(int(value))
    return f"{value:g}".replace(".", ",")


def _fmt_total(value: float) -> str:
    """An hour count where zero is zero, not a dash.

    A total of zero is a fact; a column of the table with nothing in it is
    only an absence.
    """
    if value == int(value):
        return str(int(value))
    return f"{value:g}".replace(".", ",")


def _add_table(doc: DocxDocument, headers: tuple[str, ...], rows: list[list[str]]) -> None:
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
def _render_sources(doc: DocxDocument, draft: ProgramDraft) -> None:
    if not draft.sources:
        return

    _heading(doc, "Литература и источники", level=1)

    for title, items in _group_sources(draft.sources):
        if not items:
            continue
        _heading(doc, title, level=2)
        for number, source in enumerate(items, start=1):
            # The number is written into the text rather than left to the
            # "List Number" style: that style keeps one counter for the whole
            # document, so the second group would start where the first ended.
            doc.add_paragraph(f"{number}. {_format_source(source)}")


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
def _render_assessment(doc: DocxDocument, assessment: AssessmentPlan) -> None:
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


def _render_materials(doc: DocxDocument, materials: Materials) -> None:
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


# --------------------------------------------------------------------------- conditions and staff
def _render_conditions(doc: DocxDocument, conditions: Conditions) -> None:
    """The material and methodical conditions of the programme."""
    _page_break(doc)
    _heading(doc, "Условия реализации программы", level=1)
    if conditions.equipment:
        _heading(doc, "Материально-техническое обеспечение", level=2)
        _bullets(doc, conditions.equipment)
    if conditions.teaching_materials:
        _heading(doc, "Учебно-методическое обеспечение", level=2)
        _bullets(doc, conditions.teaching_materials)


def _render_staffing(doc: DocxDocument, requirements: list[str], staffing: list[StaffSlot]) -> None:
    """The staff requirements and the staff of the programme.

    The requirements say what a teacher has to be; the table lists the positions
    the programme actually has, with empty cells the methodist fills in. Both
    belong to the one section the report checks as «кадровое обеспечение».
    """
    _page_break(doc)
    _heading(doc, "Кадровое обеспечение программы", level=1)
    if requirements:
        _heading(doc, "Требования к кадровому обеспечению", level=2)
        _bullets(doc, requirements)
    headers = ("№", "Модуль/дисциплина", "Должность", "ФИО", "Учёная степень/звание")
    rows: list[list[str]] = [
        [
            str(index),
            slot.module_id or "—",
            slot.position,
            slot.fio or "",
            slot.degree or "",
        ]
        for index, slot in enumerate(staffing, start=1)
    ]
    _add_table(doc, headers, rows)


def _render_cases(doc: DocxDocument, cases: list[CaseStudy]) -> None:
    """The partner-company cases of the programme."""
    _page_break(doc)
    _heading(doc, "Приложение № 1. Перечень кейсов", level=1)
    headers = ("№", "Компания-владелец", "Кейс", "Бизнес-задача", "Данные", "Ответственный")
    rows: list[list[str]] = [
        [
            str(index),
            case.company or "",
            case.title,
            case.task or "",
            case.data or "",
            case.owner or "",
        ]
        for index, case in enumerate(cases, start=1)
    ]
    _add_table(doc, headers, rows)


def _render_signatures(doc: DocxDocument, approval: ApprovalInfo) -> None:
    """The signature block: blank lines the methodist signs by hand."""
    doc.add_paragraph("")
    _heading(doc, "Подписи", level=1)
    _labeled(doc, "Разработчик:", "______________ / ______________")
    _labeled(
        doc,
        "Руководитель организации:",
        (approval.signatory + " / ______________") if approval.signatory else "______________ / ______________",
    )


def _render_report(doc: DocxDocument, report: ProgramReport) -> None:
    """The self-check report: what the backend verified and what it found."""
    _page_break(doc)
    _heading(doc, "Отчёт автопроверки программы", level=1)
    p = doc.add_paragraph()
    p.add_run("Итог: ").bold = True
    p.add_run("все проверки пройдены" if report.ok else "есть замечания")
    for check in report.checks:
        _heading(doc, check.name, level=2)
        p = doc.add_paragraph()
        p.add_run("Статус: ").bold = True
        p.add_run("пройдена" if check.ok else "есть проблемы")
        if check.problems:
            _bullets(doc, check.problems)
        if check.details:
            for key, value in check.details.items():
                _labeled(doc, f"{key}:", str(value))
    if report.notes:
        _heading(doc, "Замечания", level=2)
        _bullets(doc, report.notes)


# --------------------------------------------------------------------------- pending
def _materials_cover_a_module(document: ProgramDocument) -> bool:
    """Whether every lecture of at least one module has its plan and its deck.

    The report's materials check uses the same rule. A module with one artefact
    missing is not covered, so the document must not call it complete either.
    """
    materials = document.materials
    if materials is None:
        return False
    plans = {plan.topic_id for plan in materials.lecture_plans}
    decks = {deck.topic_id for deck in materials.slide_decks}
    for module in document.draft.curriculum.modules:
        lectures = [topic.id for topic in module.topics if topic.hours.lecture > 0]
        if lectures and all(topic_id in plans and topic_id in decks for topic_id in lectures):
            return True
    return False


def _render_pending_sections(doc: DocxDocument, document: ProgramDocument) -> None:
    """The sections the document does not carry yet.

    A programme that has its assessment, its materials, its conditions and its
    staff does not say that they are still to be written; only the sections
    nothing was written for are listed.
    """
    materials = document.materials
    missing: list[str] = []
    has_lectures = any(
        topic.hours.lecture > 0 for module in document.draft.curriculum.modules for topic in module.topics
    )
    if document.assessment is None:
        missing.append("Оценочные материалы")
    if has_lectures and not _materials_cover_a_module(document):
        missing.append("Методические материалы")
    if materials is None or not (materials.conditions.equipment or materials.conditions.teaching_materials):
        missing.append("Условия реализации программы")
    if materials is None or not (materials.conditions.staffing_requirements or materials.staffing):
        missing.append("Кадровое обеспечение")
    if materials is None or not materials.cases:
        missing.append("Приложение № 1. Перечень кейсов")
    if not missing:
        return
    _page_break(doc)
    _heading(doc, "Разделы, заполняемые на следующем этапе генерации", level=1)
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
