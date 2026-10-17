from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.agentic.models import EducationalProgram, ProgramKind, ProgramStatus, Stage, StageStatus

EXAMPLE_PATH = Path(__file__).resolve().parents[1] / "examples" / "document_state_example.json"

Program = dict[str, Any]


@pytest.fixture()
def raw() -> Program:
    return json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))


def validate(raw: Program) -> EducationalProgram:
    return EducationalProgram.model_validate(raw)


def problems(raw: Program) -> str:
    with pytest.raises(ValidationError) as caught:
        validate(raw)
    return str(caught.value)


def test_example_document_state_validates(raw: Program) -> None:
    program = validate(raw)
    assert program.meta.kind is ProgramKind.PROFESSIONAL_DEVELOPMENT
    assert program.meta.status is ProgramStatus.DRAFT
    assert program.current_stage is Stage.SUMMARY
    assert [state.status for state in program.stages] == [StageStatus.COMPLETED] * len(Stage)
    assert program.curriculum.total_hours == 66
    assert program.curriculum.contact_hours == 38
    assert program.curriculum.self_study_hours == 28


def test_stages_must_cover_every_stage_exactly_once(raw: Program) -> None:
    raw["stages"][1]["stage"] = "competences"
    assert "repeated: competences" in problems(raw)

    raw = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
    raw["stages"] = [state for state in raw["stages"] if state["stage"] != "sources"]
    assert "missing: sources" in problems(raw)


def test_question_key_must_match_the_options(raw: Program) -> None:
    raw["assessment"]["questions"][0]["answers"] = ["Такого варианта нет"]
    assert "are not among the options" in problems(raw)


def test_open_question_needs_exactly_one_answer(raw: Program) -> None:
    question = raw["assessment"]["questions"][0]
    question["options"] = []
    question["answers"] = ["эталонный ответ"]
    validate(raw)
    question["answers"] = ["эталон", "допустимая формулировка"]
    assert "exactly one reference answer" in problems(raw)


def test_question_with_options_needs_at_least_two(raw: Program) -> None:
    question = raw["assessment"]["questions"][0]
    question["options"] = ["единственный вариант"]
    question["answers"] = ["единственный вариант"]
    assert "at least two" in problems(raw)


def test_primary_is_literature_only(raw: Program) -> None:
    raw["sources"][4]["primary"] = True
    assert "primary is set for literature only" in problems(raw)


def test_competence_needs_an_outcome(raw: Program) -> None:
    competence = raw["competencies"][0]
    competence["knowledge"] = []
    competence["skills"] = []
    competence["experience"] = []
    assert "no knowledge, skills or experience declared" in problems(raw)


def test_schedule_needs_a_duration(raw: Program) -> None:
    raw["curriculum"]["schedule"] = {}
    assert "must be filled" in problems(raw)


def test_staff_slot_requires_a_position(raw: Program) -> None:
    raw["staffing"][0].pop("position")
    assert "position" in problems(raw)


def test_hours_are_plain_numbers(raw: Program) -> None:
    raw["curriculum"]["modules"][0]["topics"][0]["hours"] = {"lecture": 1.25, "practice": 0, "lab": 0, "self_study": 0}
    assert validate(raw).curriculum.modules[0].topics[0].hours.lecture == 1.25


def test_unknown_keys_are_ignored(raw: Program) -> None:
    raw["volume"] = {"total_hours": 72}
    raw["meta"]["unknown"] = True
    assert validate(raw).meta.title == raw["meta"]["title"]


def test_document_is_immutable(raw: Program) -> None:
    program = validate(raw)
    with pytest.raises(ValidationError):
        program.summary.goal = "Другая цель"
    with pytest.raises(ValidationError):
        program.meta.status = ProgramStatus.APPROVED


def test_json_round_trip(raw: Program) -> None:
    program = validate(raw)
    dumped = program.model_dump(mode="json")
    assert validate(dumped).meta.title == program.meta.title
    assert [state["stage"] for state in dumped["stages"]] == [stage.value for stage in Stage]
