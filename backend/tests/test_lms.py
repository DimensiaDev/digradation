"""Tests for the Moodle XML question bank in ``app.lms``."""

from __future__ import annotations

from xml.etree import ElementTree as ET

from app.agentic.models import AssessmentPlan, SeminarTask
from app.agentic.models import TestQuestion as Question
from app.lms import render_lms


def an_assessment() -> AssessmentPlan:
    return AssessmentPlan(
        final={"form": "экзамен", "hours": 2, "outcome_ids": ["ro-k-01"]},
        questions=[
            Question(
                id="q-01-01",
                tool_id="at-01",
                outcome_ids=["ro-k-01"],
                text="Что такое <данные> & метрика?",
                answers=["Число"],
            ),
            Question(
                id="q-01-02",
                tool_id="at-01",
                outcome_ids=["ro-k-01"],
                text="Выберите верное",
                options=["Верно", "Неверно"],
                answers=["Верно"],
            ),
        ],
        seminar_tasks=[
            SeminarTask(
                id="st-01",
                topic_id="t-01.1",
                title="Разобрать кейс",
                statement="Разберите кейс компании",
                outcome_ids=["ro-k-01"],
                criteria=["Кейс разобран", "Вывод обоснован"],
            )
        ],
    )


def test_an_open_question_imports_as_a_short_answer_with_its_key() -> None:
    root = ET.fromstring(render_lms(an_assessment(), programme_title="Программа"))

    question = root.find("question[@type='shortanswer']")
    assert question is not None
    assert question.findtext("questiontext/text") == "Что такое <данные> & метрика?"
    assert question.find("answer").get("fraction") == "100"
    assert question.findtext("answer/text") == "Число"


def test_markup_in_a_question_is_escaped_by_the_xml_serializer() -> None:
    xml = render_lms(an_assessment(), programme_title="Программа & <данные>")

    assert "<данные>".encode() not in xml
    assert "&lt;данные&gt;".encode() in xml


def test_a_seminar_task_travels_with_its_criteria() -> None:
    root = ET.fromstring(render_lms(an_assessment(), programme_title="Программа"))

    task = root.find("question[@type='essay']")
    assert task is not None
    assert task.findtext("graderinfo/text") == "Кейс разобран\nВывод обоснован"
