"""Tests for reading limits out of the methodist's prose."""

from __future__ import annotations

import pytest

from app.agentic.intake import (
    expected_test_questions,
    format_from_answer,
    hours_from_answer,
    kind_from_answer,
    min_practice_share,
    practice_share_from_answer,
    programme_format,
    programme_kind,
    questions_from_answer,
    requested_hours,
)
from app.agentic.models import ProgramFormat, ProgramKind


@pytest.mark.parametrize(
    ("text", "hours"),
    [
        ("Объём 16 часов, занятия по 2 часа", 16),
        ("Нужна программа на 72 часа", 72),
        ("Стажировка на 256 часов, не больше 4 часов в день", 256),
        ("Объём 8 часов", None),
        ("Объём 501 час", None),
        ("Без объёма", None),
        ("16 частей", None),
    ],
)
def test_the_volume_is_the_first_number_in_the_accepted_range(text: str, hours: float | None) -> None:
    assert requested_hours(text) == hours


@pytest.mark.parametrize(
    ("text", "count"),
    [
        ("Итоговый тест на 10 вопросов", 10),
        ("тест из 20 вопросов", 20),
        ("Нужен тест на 10", 10),
        ("Вопросов нет", None),
        ("тест на 500 вопросов", None),
    ],
)
def test_the_number_of_test_questions_is_read_when_it_is_named(text: str, count: int | None) -> None:
    assert expected_test_questions(text) == count


@pytest.mark.parametrize(
    ("text", "share"),
    [
        ("Практики не меньше половины всех часов", 0.5),
        ("Практика — минимум 40%", 0.4),
        ("Не менее двух третей", 2 / 3),
        ("Практика по усмотрению", None),
    ],
)
def test_the_minimum_practice_share_is_read_from_the_wording(text: str, share: float | None) -> None:
    expected = None if share is None else pytest.approx(share)
    assert min_practice_share(text) == expected


def test_a_general_programme_is_not_mistaken_for_a_professional_one() -> None:
    """A general programme may still talk about professional skills."""
    text = "Дополнительная общеразвивающая программа формирует профессиональные навыки"

    assert programme_kind(text) is ProgramKind.GENERAL_DEVELOPMENT


def test_the_professional_and_internship_words_are_read() -> None:
    assert programme_kind("Программа повышения квалификации") is ProgramKind.PROFESSIONAL_DEVELOPMENT
    assert programme_kind("Профессиональная переподготовка") is ProgramKind.PROFESSIONAL_DEVELOPMENT
    assert programme_format("Повышение квалификации в форме стажировки") is ProgramFormat.INTERNSHIP
    assert programme_format("Очно-заочная программа") is None


def test_a_bare_answer_is_read_against_its_question() -> None:
    assert hours_from_answer("Сколько академических часов?", "72") == 72
    assert hours_from_answer("Сколько часов?", "8") is None
    assert hours_from_answer("Какая форма обучения?", "72") is None
    assert questions_from_answer("Сколько тестовых вопросов?", "10") == 10
    assert questions_from_answer("Сколько часов?", "10") is None
    assert practice_share_from_answer("Какая доля практики?", "50") == pytest.approx(0.5)
    assert practice_share_from_answer("Сколько часов практики?", "50") is None
    assert practice_share_from_answer("Практическая часть — сколько часов?", "50") is None
    assert kind_from_answer("Какой тип программы?", "профессиональная") is ProgramKind.PROFESSIONAL_DEVELOPMENT
    assert kind_from_answer("Какой тип программы?", "общеразвивающая") is ProgramKind.GENERAL_DEVELOPMENT
    assert (
        kind_from_answer("Нужна ли программа повышения квалификации?", "Да, нужна")
        is ProgramKind.PROFESSIONAL_DEVELOPMENT
    )
    assert format_from_answer("Нужна ли стажировка?", "Да") is ProgramFormat.INTERNSHIP
    assert format_from_answer("Нужна ли стажировка?", "Да, нужна") is ProgramFormat.INTERNSHIP
    assert format_from_answer("Нужна ли стажировка?", "Нет") is None
    assert format_from_answer("Нужна ли стажировка?", "Да, не нужна") is None
