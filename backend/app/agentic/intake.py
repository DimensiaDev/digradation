"""The limits a methodist names in prose, read back from the text.

The intake tool carries the volume, the number of test questions and the
minimum practice share as structured fields, but a model does not always fill
them: a request may name the volume only, or the methodist may answer a
clarifying question with prose. These parsers are the one place that turns the
wording into the numbers the pipeline checks against.

They are deliberately conservative. A value that cannot be read is ``None``,
which leaves the field unset; an out-of-range value is also left unset rather
than written into a state that validates the range. The stage still sees the
original words in the brief, so nothing is lost.
"""

from __future__ import annotations

import re

from app.agentic.models import MAX_PROGRAM_HOURS, MIN_PROGRAM_HOURS, ProgramFormat, ProgramKind

_HOURS = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:академических\s+)?час(?:ов|а)?\b", re.IGNORECASE)
_QUESTIONS = re.compile(r"(?:тест\w*\s+(?:на|из)\s+)?(\d+)\s*(?:вопрос|тестов)", re.IGNORECASE)
_QUESTIONS_AFTER_TEST = re.compile(r"тест\w*[^\d]{0,40}(\d+)", re.IGNORECASE)
_PERCENT = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
_HALF = re.compile(r"(?:не\s+менее|не\s+меньше|минимум)\s+половин", re.IGNORECASE)
_TWO_THIRDS = re.compile(r"(?:не\s+менее|не\s+меньше|минимум)\s+(?:двух\s+третей|2/3)", re.IGNORECASE)
_BARE_NUMBER = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*$")
_YES = frozenset({"да", "конечно", "нужна", "нужно", "требуется"})


def _is_yes(answer: str) -> bool:
    """A yes that is not a no, allowing "Да, нужна" and punctuation."""
    tokens = [token.strip(".,!?") for token in answer.lower().split()]
    return bool(tokens) and tokens[0] in _YES and "нет" not in tokens and "не" not in tokens


def requested_hours(text: str) -> float | None:
    """The programme volume named in the text, if it is in the accepted range.

    A request may mention another number of hours first — "занятия по 2 часа,
    объём 16 часов" — so every mention is considered and the first one in the
    accepted range wins.
    """
    for match in _HOURS.finditer(text):
        value = float(match.group(1).replace(",", "."))
        if MIN_PROGRAM_HOURS <= value <= MAX_PROGRAM_HOURS:
            return value
    return None


def expected_test_questions(text: str) -> int | None:
    """How many test questions the text asks for, if it names them."""
    match = _QUESTIONS.search(text) or _QUESTIONS_AFTER_TEST.search(text)
    if match is None:
        return None
    value = int(match.group(1))
    return value if 1 <= value <= 200 else None


def min_practice_share(text: str) -> float | None:
    """The smallest share of practice hours the text asks for.

    A percentage is read as written; "half" and "two thirds" are the phrases
    a methodist actually uses. Any other wording leaves the field unset.
    """
    match = _PERCENT.search(text)
    if match is not None:
        value = float(match.group(1).replace(",", ".")) / 100
        return value if 0 <= value <= 1 else None
    if _TWO_THIRDS.search(text):
        return 2 / 3
    if _HALF.search(text):
        return 0.5
    return None


def programme_kind(text: str) -> ProgramKind | None:
    """The programme template the text names, if it names one.

    The general-development phrase is checked first. A general programme may
    still talk about professional skills, so a bare "профессиональн" is not
    enough to pick the professional template.
    """
    lowered = text.lower()
    if "общеразвива" in lowered:
        return ProgramKind.GENERAL_DEVELOPMENT
    if "повышени" in lowered and "квалификаци" in lowered:
        return ProgramKind.PROFESSIONAL_DEVELOPMENT
    if "переподготовк" in lowered:
        return ProgramKind.PROFESSIONAL_DEVELOPMENT
    return None


def programme_format(text: str) -> ProgramFormat | None:
    """The delivery format the text names, if it names one."""
    return ProgramFormat.INTERNSHIP if "стажиров" in text.lower() else None


def hours_from_answer(question: str, answer: str) -> float | None:
    """The volume in a bare-number answer to a question about hours.

    A methodist answers "72" to "Сколько часов?" as readily as "72 часа". The
    question supplies the unit, so the answer alone is enough.
    """
    if "час" not in question.lower():
        return None
    match = _BARE_NUMBER.fullmatch(answer)
    if match is None:
        return None
    value = float(match.group(1).replace(",", "."))
    return value if MIN_PROGRAM_HOURS <= value <= MAX_PROGRAM_HOURS else None


def questions_from_answer(question: str, answer: str) -> int | None:
    """The count in a bare-number answer to a question about a test."""
    lowered = question.lower()
    if "вопрос" not in lowered and "тест" not in lowered:
        return None
    match = _BARE_NUMBER.fullmatch(answer)
    if match is None:
        return None
    value = int(match.group(1))
    return value if 1 <= value <= 200 else None


def practice_share_from_answer(question: str, answer: str) -> float | None:
    """The share in a bare-number answer to a question about the practice share.

    A question about the share says "доля", "процент" or "часть"; a question
    about the number of practice hours does not, and must not turn "50" into
    a requirement that half the programme is practice.
    """
    lowered = question.lower()
    if "практик" not in lowered or "час" in lowered:
        return None
    if not any(word in lowered for word in ("дол", "процент", "часть", "%")):
        return None
    match = _BARE_NUMBER.fullmatch(answer)
    if match is None:
        return None
    value = float(match.group(1).replace(",", "."))
    if 0 <= value <= 1:
        return value
    if 1 < value <= 100:
        return value / 100
    return None


def kind_from_answer(question: str, answer: str) -> ProgramKind | None:
    """The template named by an answer to a question about the type."""
    lowered_question = question.lower()
    lowered_answer = answer.lower()
    if "общеразвива" in lowered_answer:
        return ProgramKind.GENERAL_DEVELOPMENT
    if "профессиональн" in lowered_answer:
        return ProgramKind.PROFESSIONAL_DEVELOPMENT
    if "тип" not in lowered_question and "программ" not in lowered_question:
        return None
    if not _is_yes(answer):
        return None
    if "общеразвива" in lowered_question:
        return ProgramKind.GENERAL_DEVELOPMENT
    if any(stem in lowered_question for stem in ("повышени", "переподготов", "профессиональн")):
        return ProgramKind.PROFESSIONAL_DEVELOPMENT
    return None


def format_from_answer(question: str, answer: str) -> ProgramFormat | None:
    """The format named by a yes/no answer to a question about an internship."""
    if "стажиров" not in question.lower():
        return None
    if "стажиров" in answer.lower() or _is_yes(answer):
        return ProgramFormat.INTERNSHIP
    return None
