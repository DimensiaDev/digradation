from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError

from agentic.models.checks import (
    CheckSeverity,
    ProgramConsistencyError,
    check_program,
    validate_consistent,
)
from agentic.models.example_72h import PROGRAM_72H
from agentic.models.program import STATUS_TRANSITIONS, EducationalProgram, ProgramKind, ProgramStatus

Program = dict[str, Any]


def fresh() -> Program:
    return copy.deepcopy(PROGRAM_72H)


def validate(raw: Program) -> EducationalProgram:
    return EducationalProgram.model_validate(raw)


def problems(raw: Program) -> str:
    with pytest.raises(ValidationError) as caught:
        validate(raw)
    return str(caught.value)


def severities(raw: Program) -> dict[str, CheckSeverity]:
    return {item.code: item.severity for item in check_program(validate(raw)).items}


def test_example_72h_validates() -> None:
    program = validate(fresh())
    assert program.meta.kind is ProgramKind.PROFESSIONAL_DEVELOPMENT
    assert program.meta.status is ProgramStatus.DRAFT
    assert program.volume.total_hours == 72
    assert program.curriculum.total_hours == 66
    assert program.assessment.final.hours == 6
    assert program.curriculum.contact_hours == 38
    assert program.curriculum.self_study_hours == 28


def test_example_72h_passes_the_strict_check() -> None:
    report = validate_consistent(validate(fresh()))
    assert report.ok
    assert [item.code for item in report.warnings] == ["sources.unconfirmed"]
    assert [item.code for item in report.items if item.severity is CheckSeverity.INFO] == ["hours.sums"]


def test_json_schema_exports_the_hours_step_and_id_patterns() -> None:
    definitions = EducationalProgram.model_json_schema()["$defs"]
    assert definitions["FinalAttestation"]["properties"]["hours"]["multipleOf"] == 0.5
    assert definitions["Topic"]["properties"]["id"]["pattern"] == r"^t-\d{1,3}\.\d{1,3}$"


def test_hours_must_match_the_declared_total() -> None:
    raw = fresh()
    raw["volume"]["total_hours"] = 76
    assert "!= declared total" in problems(raw)


def test_declared_contact_hours_must_match_the_tree() -> None:
    raw = fresh()
    raw["volume"]["declared_contact_hours"] = 40
    assert "contact work" in problems(raw)


def test_declared_self_study_hours_must_match_the_tree() -> None:
    raw = fresh()
    raw["volume"]["declared_self_study_hours"] = 20
    assert "self-study" in problems(raw)


def test_hours_move_in_half_hour_steps() -> None:
    raw = fresh()
    raw["assessment"]["final"]["hours"] = 6.25
    assert "multiple_of" in problems(raw)


def test_topic_hours_cannot_add_up_to_zero() -> None:
    raw = fresh()
    raw["curriculum"]["modules"][0]["topics"][0]["hours"] = {"lecture": 0, "practice": 0, "lab": 0, "self_study": 0}
    assert "add up to zero" in problems(raw)


def test_schedule_must_state_its_duration_or_load() -> None:
    raw = fresh()
    raw["curriculum"]["schedule"] = {}
    assert "must be filled" in problems(raw)


def test_duplicate_source_ids_are_rejected() -> None:
    raw = fresh()
    raw["sources"][1]["id"] = raw["sources"][0]["id"]
    assert "duplicate source id(s)" in problems(raw)


def test_duplicate_topic_ids_are_rejected() -> None:
    raw = fresh()
    raw["curriculum"]["modules"][1]["topics"][1]["id"] = "t-2.1"
    assert "duplicate topic id(s)" in problems(raw)


def test_topic_must_belong_to_its_module() -> None:
    raw = fresh()
    raw["curriculum"]["modules"][1]["topics"][0]["id"] = "t-3.9"
    assert "does not belong to module m-2" in problems(raw)


def test_dangling_references_are_rejected() -> None:
    raw = fresh()
    raw["curriculum"]["modules"][0]["topics"][0]["outcome_ids"].append("ro-k-99")
    assert "references unknown id(s): ro-k-99" in problems(raw)


def test_outcome_bucket_must_match_its_id_prefix() -> None:
    raw = fresh()
    raw["competencies"][0]["knowledge"][0]["id"] = "ro-s-09"
    assert "must start with ro-k-" in problems(raw)


def test_professional_program_needs_a_standard_for_every_competence() -> None:
    raw = fresh()
    raw["competencies"][0]["standard_source_id"] = None
    assert "competences without a standard: comp-01" in problems(raw)


def test_standard_binding_must_point_at_a_professional_standard() -> None:
    raw = fresh()
    raw["competencies"][0]["standard_source_id"] = "src-01"
    assert "must point at a professional standard" in problems(raw)


def test_general_development_needs_focus_and_level() -> None:
    raw = fresh()
    raw["meta"]["kind"] = "general_development"
    message = problems(raw)
    assert "requires summary.focus" in message
    assert "requires summary.level" in message


def test_question_key_must_be_among_the_options() -> None:
    raw = fresh()
    raw["assessment"]["questions"][0]["answers"] = ["Такого варианта нет"]
    assert "are not among the options" in problems(raw)


def test_open_question_keeps_exactly_one_reference_answer() -> None:
    raw = fresh()
    question = raw["assessment"]["questions"][0]
    question["options"] = []
    question["answers"] = ["эталонный ответ"]
    validate(raw)
    question["answers"] = ["эталон", "допустимая формулировка"]
    assert "exactly one reference answer" in problems(raw)


def test_unknown_fields_are_rejected() -> None:
    raw = fresh()
    raw["volume"]["total"] = 72
    assert "Extra inputs are not permitted" in problems(raw)


def test_status_machine_covers_every_status() -> None:
    assert set(STATUS_TRANSITIONS) == set(ProgramStatus)


def test_status_transitions_follow_the_machine() -> None:
    meta = validate(fresh()).meta
    assert not meta.can_transition_to(ProgramStatus.VERIFIED)
    assert meta.transition_to(ProgramStatus.DRAFT).status is ProgramStatus.DRAFT
    assert meta.transition_to(ProgramStatus.GENERATING).status is ProgramStatus.GENERATING
    with pytest.raises(ValueError, match="not allowed"):
        meta.transition_to(ProgramStatus.APPROVED)


def test_unconfirmed_normative_source_is_an_error() -> None:
    raw = fresh()
    raw["sources"][0]["confirmed"] = False
    assert severities(raw)["sources.normative_unconfirmed"] is CheckSeverity.ERROR


def test_missing_normative_basis_is_an_error() -> None:
    raw = fresh()
    raw["meta"]["kind"] = "general_development"
    raw["summary"]["focus"] = "цифровые навыки"
    raw["summary"]["level"] = "базовый"
    for source in raw["sources"]:
        source["kind"] = "reference"
    for competence in raw["competencies"]:
        competence["standard_source_id"] = None
    assert severities(raw)["sources.normative_basis"] is CheckSeverity.ERROR


def test_outcome_must_be_checked_by_two_instruments() -> None:
    raw = fresh()
    raw["assessment"]["tools"] = []
    raw["assessment"]["questions"] = []
    raw["assessment"]["seminar_tasks"] = []
    uncovered = [item for item in check_program(validate(raw)).errors if item.code == "coverage.outcome_instruments"]
    assert uncovered
    assert "менее чем двумя" in uncovered[0].message


def test_outcome_without_a_topic_is_an_error() -> None:
    raw = fresh()
    for module in raw["curriculum"]["modules"]:
        for topic in module["topics"]:
            topic["outcome_ids"] = []
    items = check_program(validate(raw)).items
    assert any(item.code == "coverage.outcome_topic" and item.severity is CheckSeverity.ERROR for item in items)
    assert any(item.code == "coverage.topic_outcomes" and item.severity is CheckSeverity.WARNING for item in items)


def test_missing_materials_are_errors() -> None:
    raw = fresh()
    raw["materials"] = {"lecture_plans": [], "slide_decks": []}
    report = check_program(validate(raw))
    assert {"materials.lecture_plans", "materials.slides"} <= {item.code for item in report.errors}
    with pytest.raises(ProgramConsistencyError) as caught:
        validate_consistent(validate(raw))
    assert caught.value.report.ok is False


def test_filled_placeholders_are_warnings() -> None:
    raw = fresh()
    raw["staffing"][0]["fio"] = "Иванов Иван Иванович"
    raw["approval"] = {"order_number": "12-О", "order_date": "2026-09-01"}
    codes = severities(raw)
    assert codes["placeholders.staffing"] is CheckSeverity.WARNING
    assert codes["placeholders.approval"] is CheckSeverity.WARNING
