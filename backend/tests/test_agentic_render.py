"""Tests for draft rendering and stage prompts.

Both modules are pure: they turn the stored state into text the model or the
methodist reads. Keeping them covered directly means a change to a label or a
prompt marker cannot quietly break the mock provider, which keys its scripted
response on the ``STAGE:`` marker.
"""

from __future__ import annotations

import uuid

from app.agentic.models import ProgramDraft, Source, SourceKind, Stage
from app.agentic.prompts import build_stage_messages, build_stage_system_prompt
from app.agentic.render import render_draft
from app.agentic.state import (
    CompetencesBlock,
    HoursBlock,
    ProgramState,
    SourcesBlock,
    SummaryBlock,
    apply_block,
    new_state,
)

SESSION = "00000000-0000-0000-0000-000000000001"
REQUESTED_HOURS = 4


def _competences() -> CompetencesBlock:
    return CompetencesBlock.model_validate(
        {
            "competencies": [
                {
                    "id": "comp-01",
                    "statement": "Способен анализировать данные",
                    "knowledge": [{"id": "ro-k-01", "statement": "Знает методы"}],
                    "skills": [{"id": "ro-s-01", "statement": "Умеет считать"}],
                    "experience": [{"id": "ro-e-01", "statement": "Имеет опыт"}],
                }
            ]
        }
    )


def _hours(*, schedule: dict | None = None) -> HoursBlock:
    return HoursBlock.model_validate(
        {
            "curriculum": {
                "modules": [
                    {
                        "id": "m-01",
                        "title": "Модуль 1",
                        "topics": [
                            {
                                "id": "t-01.1",
                                "title": "Тема 1",
                                "hours": {"lecture": 1, "practice": 1, "lab": 1, "self_study": 1},
                            }
                        ],
                    }
                ],
                "schedule": schedule or {"days": 1, "hours_per_day": 4},
            }
        }
    )


def _zero_hours() -> HoursBlock:
    return HoursBlock.model_validate(
        {
            "curriculum": {
                "modules": [
                    {
                        "id": "m-01",
                        "title": "Модуль 1",
                        "topics": [{"id": "t-01.1", "title": "Тема 1", "hours": {}}],
                    }
                ],
                "schedule": {"weeks": 1},
            }
        }
    )


def _summary(title: str = "Программа") -> SummaryBlock:
    return SummaryBlock.model_validate(
        {
            "kind": "general_development",
            "title": title,
            "summary": {
                "goal": "Цель программы",
                "target_audience": "Слушатели",
                "form": "очная",
                "issued_document": "сертификат",
                "tasks": ["Задача 1", "Задача 2"],
            },
        }
    )


def draft(*, sources: list[Source] | None = None, schedule: dict | None = None) -> ProgramDraft:
    return ProgramDraft(
        kind="general_development",
        title="Программа",
        requested_hours=REQUESTED_HOURS,
        summary=_summary().summary,
        competencies=_competences().competencies,
        curriculum=_hours(schedule=schedule).curriculum,
        sources=sources or [],
    )


def complete_state() -> ProgramState:
    state = new_state(
        uuid.UUID(SESSION),
        requested_hours=REQUESTED_HOURS,
        requirements="Тема: анализ данных",
        title="Программа",
    )
    state = apply_block(state, Stage.COMPETENCES, _competences())
    state = apply_block(state, Stage.HOURS, _hours())
    state = apply_block(state, Stage.SOURCES, SourcesBlock(sources=[]))
    return apply_block(state, Stage.SUMMARY, _summary())


def test_render_includes_the_blocks_the_methodist_reviews():
    text = render_draft(draft())

    assert "Черновик программы готов к подтверждению." in text
    assert "Название: Программа" in text
    assert "Объём: 4 ак. ч." in text
    assert "Цель программы" in text
    assert "- Модуль 1" in text
    assert "Тема 1: лек 1, пр 1, лаб 1, срс 1" in text
    assert "знание: Знает методы" in text
    assert "умение: Умеет считать" in text
    assert "опыт: Имеет опыт" in text
    assert "Задача 1" in text
    assert "Напишите «подтверждаю»" in text


def test_render_includes_sources_and_omits_a_missing_identifier():
    text = render_draft(
        draft(
            sources=[
                Source(id="src-01", kind=SourceKind.NORMATIVE_ACT, title="ФГОС", identifier="273-ФЗ"),
                Source(id="src-02", kind=SourceKind.LITERATURE, title="Учебник"),
            ]
        )
    )

    assert "Источники:" in text
    assert "- ФГОС (273-ФЗ)" in text
    assert "- Учебник" in text
    assert "Учебник (" not in text


def test_render_shows_the_schedule_in_the_forms_it_has():
    assert "График: 4 нед." in render_draft(draft(schedule={"weeks": 4}))
    assert "График: 2 дн., 2 ч/день" in render_draft(draft(schedule={"days": 2, "hours_per_day": 2}))


def test_render_with_a_topic_that_has_no_hours_prints_zero():
    draft_without_hours = ProgramDraft(
        kind="general_development",
        title="Программа",
        requested_hours=None,
        summary=_summary().summary,
        competencies=_competences().competencies,
        curriculum=_zero_hours().curriculum,
    )

    assert "Тема 1: 0" in render_draft(draft_without_hours)


def test_stage_prompt_carries_the_stage_marker_and_the_block_schema():
    prompt = build_stage_system_prompt(Stage.SOURCES)

    assert "STAGE: sources" in prompt
    assert "search_sources" in prompt
    assert "Схема блока payload" in prompt
    assert '"sources"' in prompt


def test_stage_messages_carry_the_state_as_data():
    state = complete_state().model_copy(update={"current_stage": Stage.SUMMARY})

    messages = build_stage_messages(state)

    assert [message.role for message in messages] == ["system", "user"]
    assert "STAGE: summary" in messages[0].content[0].text
    user = messages[1].content[0].text
    assert "Тема: анализ данных" in user
    assert '"requested_hours": 4.0' in user
    assert "save_program_draft" in user


def test_stage_messages_fall_back_to_the_first_stage_without_a_current_one():
    state = complete_state()

    messages = build_stage_messages(state)

    assert "STAGE: competences" in messages[0].content[0].text
