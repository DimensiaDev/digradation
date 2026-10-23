"""Export the assessment of a programme as a Moodle XML question bank.

The LMS export carries the part of the programme an LMS can actually import:
the test questions with their keys and the seminar tasks with their criteria.
The document stays the DOCX, and the slides stay the PPTX; a learning
management system needs the questions, not the prose around them.

Moodle XML is used rather than a ZIP-based SCORM package because it is a single
text file, it imports into Moodle and its forks without a plugin, and it keeps
the answer keys and the grading fractions in the file. The XML is generated
with :mod:`xml.etree.ElementTree` rather than string formatting, so a question
containing ``<`` or ``&`` cannot break the import.
"""

from __future__ import annotations

from xml.etree import ElementTree as ET

from app.agentic.models import AssessmentPlan, SeminarTask, TestQuestion

#: The format Moodle expects in the ``format`` attributes of a text element.
PLAIN_TEXT = "plain_text"
HTML = "html"


def render_lms(assessment: AssessmentPlan, *, programme_title: str) -> bytes:
    """Return the assessment as a Moodle XML question bank."""
    root = ET.Element("quiz")
    _category(root, programme_title)

    for question in assessment.questions:
        if question.options:
            _multichoice(root, question)
        else:
            _shortanswer(root, question)

    for task in assessment.seminar_tasks:
        _essay(root, task)

    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _category(root: ET.Element, title: str) -> None:
    question = ET.SubElement(root, "question", {"type": "category"})
    category = ET.SubElement(question, "category")
    _text(category, f"$course$/Программа/{title}")


def _multichoice(root: ET.Element, question: TestQuestion) -> None:
    """A closed question: every option is a choice, the key is the answer."""
    node = _question(root, question, "multichoice")
    ET.SubElement(node, "single").text = "true" if len(question.answers) == 1 else "false"
    ET.SubElement(node, "shuffleanswers").text = "1"
    ET.SubElement(node, "answernumbering").text = "abc"
    fraction = round(100 / len(question.answers), 2)
    for option in question.options:
        fraction_value = fraction if option in question.answers else 0
        answer = ET.SubElement(node, "answer", {"fraction": str(fraction_value), "format": PLAIN_TEXT})
        _text(answer, option)
    _feedback(node, "correctfeedback", "Верно.")
    _feedback(node, "partiallycorrectfeedback", "Частично верно.")
    _feedback(node, "incorrectfeedback", "Неверно.")
    ET.SubElement(node, "shownumcorrect")


def _shortanswer(root: ET.Element, question: TestQuestion) -> None:
    """An open question with a reference answer, imported as a short answer."""
    node = _question(root, question, "shortanswer")
    ET.SubElement(node, "usecase").text = "0"
    for answer in question.answers:
        choice = ET.SubElement(node, "answer", {"fraction": "100", "format": PLAIN_TEXT})
        _text(choice, answer)


def _essay(root: ET.Element, task: SeminarTask) -> None:
    """A seminar task: no automatic key, but the criteria travel with it."""
    node = ET.Element("question", {"type": "essay"})
    root.append(node)
    name = ET.SubElement(node, "name")
    _text(name, task.title)
    questiontext = ET.SubElement(node, "questiontext", {"format": HTML})
    _text(questiontext, task.statement)
    if task.outcome_ids:
        _text(ET.SubElement(node, "generalfeedback", {"format": HTML}), _outcomes(task.outcome_ids))
    grader = ET.SubElement(node, "graderinfo", {"format": HTML})
    _text(grader, "\n".join(task.criteria) or "Критерии не заданы.")
    ET.SubElement(node, "responseformat").text = "editor"
    ET.SubElement(node, "responserequired").text = "1"
    ET.SubElement(node, "responsefieldlines").text = "10"
    ET.SubElement(node, "attachments").text = "0"
    ET.SubElement(node, "attachmentsrequired").text = "0"
    ET.SubElement(node, "responsetemplate", {"format": HTML})


def _question(root: ET.Element, question: TestQuestion, kind: str) -> ET.Element:
    node = ET.SubElement(root, "question", {"type": kind})
    name = ET.SubElement(node, "name")
    _text(name, question.text[:200])
    questiontext = ET.SubElement(node, "questiontext", {"format": HTML})
    _text(questiontext, question.text)
    if question.outcome_ids:
        _text(ET.SubElement(node, "generalfeedback", {"format": HTML}), _outcomes(question.outcome_ids))
    return node


def _feedback(node: ET.Element, name: str, value: str) -> None:
    _text(ET.SubElement(node, name, {"format": HTML}), value)


def _text(parent: ET.Element, value: str) -> None:
    ET.SubElement(parent, "text").text = value


def _outcomes(outcome_ids: list[str]) -> str:
    return "Проверяет результаты обучения: " + ", ".join(outcome_ids)
