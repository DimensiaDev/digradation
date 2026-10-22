"""Tests for the blocks the offline mock fabricates.

The mock is what the Docker and end-to-end stacks run on, so the blocks it
fabricates have to be the ones the schema accepts and the checks pass: a
block the stages refuse is a programme that never gets written.
"""

from __future__ import annotations

import uuid

import pytest

from app.agentic.state import (
    AssessmentBlock,
    CompetencesBlock,
    HoursBlock,
    MaterialsBlock,
    ProgramState,
    SourcesBlock,
    SummaryBlock,
    new_state,
)
from app.providers.mock import _assessment, _catalog_source, _curriculum, _materials


def test_the_fabricated_competences_validate() -> None:
    block = CompetencesBlock(
        kind="professional_development",
        title="Программа (mock)",
        competencies=[
            {
                "id": "comp-01",
                "statement": "Готов применять полученные знания и умения в профессиональной деятельности",
                "knowledge": [{"id": "ro-k-01", "statement": "Знает основные понятия программы"}],
                "skills": [{"id": "ro-s-01", "statement": "Умеет применять их в типовых задачах"}],
                "experience": [{"id": "ro-e-01", "statement": "Владеет приёмами решения практических задач"}],
            }
        ],
    )

    assert block.competencies[0].knowledge[0].id == "ro-k-01"


def test_the_fabricated_curriculum_adds_up() -> None:
    block = HoursBlock(curriculum=_curriculum(72))

    assert block.curriculum.total_hours == 72
    assert block.curriculum.modules[0].hours == 72
    assert block.curriculum.attestation_hours == 0


def test_the_fabricated_assessment_passes_the_checks() -> None:
    """Every outcome has to be checked by two means, or the stage refuses it."""
    state = _a_run_with_the_curriculum()
    block = AssessmentBlock(assessment=_assessment())

    assert state.assessment_problems(block.assessment, state.curriculum) == []


def test_the_fabricated_materials_belong_to_the_plan() -> None:
    state = _a_run_with_the_curriculum()
    block = MaterialsBlock(materials=_materials())

    topics = {topic.id for module in state.curriculum.modules for topic in module.topics}
    assert all(plan.topic_id in topics for plan in block.materials.lecture_plans)
    assert all(deck.topic_id in topics for deck in block.materials.slide_decks)


def test_the_fabricated_sources_are_documents_of_the_corpus() -> None:
    block = SourcesBlock(sources=[_catalog_source()])

    assert block.sources[0].id == "profstd-06.001"
    assert block.sources[0].kind.value == "professional_standard"


def test_the_fabricated_passport_validates() -> None:
    block = SummaryBlock(
        summary={
            "goal": "Сформировать у слушателей практические знания и умения по теме программы.",
            "tasks": ["Изучить основные понятия", "Отработать приёмы на практике"],
            "target_audience": "Специалисты, которым нужны знания по теме программы",
            "admission_requirements": "Высшее или среднее профессиональное образование",
            "form": "Очно-заочная, с применением дистанционных технологий",
            "issued_document": "Удостоверение о повышении квалификации",
        }
    )

    assert block.summary.goal


def _a_run_with_the_curriculum() -> ProgramState:
    """A run that committed its competences and its curriculum."""
    state = new_state(uuid.uuid4(), requested_hours=72, requirements="Программа (mock)")
    state = state.with_competences(
        CompetencesBlock(
            kind="professional_development",
            title="Программа (mock)",
            competencies=[
                {
                    "id": "comp-01",
                    "statement": "Готов применять полученные знания",
                    "knowledge": [{"id": "ro-k-01", "statement": "Знает основные понятия программы"}],
                    "skills": [{"id": "ro-s-01", "statement": "Умеет применять их в типовых задачах"}],
                    "experience": [{"id": "ro-e-01", "statement": "Владеет приёмами решения практических задач"}],
                }
            ],
        ),
        [],
    )
    return state.with_curriculum(HoursBlock(curriculum=_curriculum(72)))


@pytest.mark.parametrize("hours", [16, 72, 256])
def test_the_fabricated_curriculum_adds_up_to_any_volume(hours: int) -> None:
    """The mock answers whatever volume the methodist named."""
    block = HoursBlock(curriculum=_curriculum(float(hours)))

    assert block.curriculum.total_hours == hours
