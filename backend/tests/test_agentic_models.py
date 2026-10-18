from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.agentic.models import (
    EducationalProgram,
    ProgramDraft,
    ProgramKind,
    ProgramStatus,
    Stage,
    StageStatus,
    StageStatuses,
)

EXAMPLE_PATH = Path(__file__).resolve().parents[1] / "examples" / "document_state_example.json"

Program = dict[str, Any]


@pytest.fixture()
def raw() -> Program:
    return json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))


@pytest.fixture()
def draft() -> Program:
    """A valid programme draft, the payload ``save_program_draft`` accepts."""
    return {
        "kind": "general_development",
        "title": "Тестовая программа",
        "summary": {
            "goal": "Научить основам работы с данными",
            "target_audience": "Взрослые слушатели",
            "form": "очно-заочная",
            "issued_document": "сертификат",
        },
        "competencies": [
            {
                "id": "comp-01",
                "statement": "Способен анализировать данные",
                "knowledge": [{"id": "ro-k-01", "statement": "Знает основы статистики"}],
                "skills": [{"id": "ro-s-01", "statement": "Умеет строить отчёты"}],
            },
            {
                "id": "comp-02",
                "statement": "Способен защитить результат",
                "experience": [{"id": "ro-e-01", "statement": "Имеет опыт защиты проекта"}],
            },
        ],
        "curriculum": {
            "modules": [
                {
                    "id": "m-01",
                    "title": "Модуль 1",
                    "topics": [
                        {
                            "id": "t-01.1",
                            "title": "Тема 1",
                            "hours": {"lecture": 1},
                            "outcome_ids": ["ro-k-01"],
                        }
                    ],
                },
                {
                    "id": "m-02",
                    "title": "Модуль 2",
                    "topics": [{"id": "t-02.1", "title": "Тема 2", "hours": {"practice": 1}}],
                },
            ],
            "schedule": {"days": 1, "hours_per_day": 2},
        },
        "sources": [],
    }


def validate_draft(raw: Program) -> ProgramDraft:
    return ProgramDraft.model_validate(raw)


def draft_problems(raw: Program) -> str:
    with pytest.raises(ValidationError) as caught:
        validate_draft(raw)
    return str(caught.value)


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
    assert all(getattr(program.stages, stage) is StageStatus.COMPLETED for stage in Stage)
    assert program.curriculum.total_hours == 66
    assert program.curriculum.contact_hours == 38
    assert program.curriculum.self_study_hours == 28


def test_stage_statuses_default_to_pending(raw: Program) -> None:
    raw["stages"] = {}
    assert all(getattr(validate(raw).stages, stage) is StageStatus.PENDING for stage in Stage)


def test_stage_statuses_accept_a_partial_update(raw: Program) -> None:
    raw["stages"] = {"hours": "in_progress"}
    program = validate(raw)
    assert program.stages.hours is StageStatus.IN_PROGRESS
    assert program.stages.summary is StageStatus.PENDING


def test_stage_statuses_cover_the_stage_enum() -> None:
    assert set(StageStatuses.model_fields) == set(Stage)


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
    assert list(dumped["stages"]) == [stage.value for stage in Stage]


def test_draft_validates(draft: Program) -> None:
    assert validate_draft(draft).title == "Тестовая программа"


def test_duplicate_competence_ids_are_rejected(draft: Program) -> None:
    draft["competencies"][1]["id"] = "comp-01"
    assert "duplicate ids: competencies: comp-01" in draft_problems(draft)


def test_duplicate_outcome_ids_are_rejected(draft: Program) -> None:
    draft["competencies"][1]["experience"][0]["id"] = "ro-k-01"
    assert "outcomes: ro-k-01" in draft_problems(draft)


def test_duplicate_module_ids_are_rejected(draft: Program) -> None:
    draft["curriculum"]["modules"][1]["id"] = "m-01"
    assert "modules: m-01" in draft_problems(draft)


def test_duplicate_topic_ids_are_rejected(draft: Program) -> None:
    draft["curriculum"]["modules"][1]["topics"][0]["id"] = "t-01.1"
    assert "topics: t-01.1" in draft_problems(draft)


def test_duplicate_source_ids_are_rejected(draft: Program) -> None:
    draft["sources"] = [
        {"id": "src-01", "kind": "literature", "title": "Основы анализа данных"},
        {"id": "src-01", "kind": "literature", "title": "Повторно добавленный источник"},
    ]
    assert "sources: src-01" in draft_problems(draft)


def test_a_repeated_id_is_reported_once(draft: Program) -> None:
    draft["curriculum"]["modules"][1]["id"] = "m-01"
    draft["curriculum"]["modules"].append(
        {"id": "m-01", "title": "Модуль 3", "topics": [{"id": "t-03.1", "title": "Тема 3", "hours": {}}]}
    )
    assert draft_problems(draft).count("m-01") == 1
