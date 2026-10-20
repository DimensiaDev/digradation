"""Unit tests for the programme state machine in :mod:`app.agentic.state`.

The state is a value: every transition returns a new envelope. These tests pin
the order of the stages, the block each stage writes, the retry budget and the
two gates — ``to_draft`` and ``approve_state`` — that keep a half-built document
from being mistaken for a reviewable one.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.agentic.models import ProgramStatus, Source, SourceKind, Stage, StageStatus
from app.agentic.state import (
    MAX_STAGE_ATTEMPTS,
    STAGE_ORDER,
    CompetencesBlock,
    HoursBlock,
    ProgramState,
    SourcesBlock,
    SummaryBlock,
    apply_block,
    approve_state,
    is_approval_message,
    mark_finalized,
    mark_run_failed,
    mark_stage_failed,
    mark_stage_started,
    new_state,
    set_known_sources,
)

SESSION_ID = uuid.uuid4()
REQUESTED_HOURS = 2


def competences_block() -> CompetencesBlock:
    return CompetencesBlock.model_validate(
        {
            "competencies": [
                {
                    "id": "comp-01",
                    "statement": "Способен применять методы анализа данных",
                    "knowledge": [{"id": "ro-k-01", "statement": "Знает основы анализа данных"}],
                    "skills": [{"id": "ro-s-01", "statement": "Умеет выбирать метод"}],
                }
            ]
        }
    )


def hours_block(hours_per_day: float = REQUESTED_HOURS) -> HoursBlock:
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
                                "hours": {"lecture": 1, "practice": 1},
                            }
                        ],
                    }
                ],
                "schedule": {"days": 1, "hours_per_day": hours_per_day},
            }
        }
    )


def summary_block() -> SummaryBlock:
    return SummaryBlock.model_validate(
        {
            "kind": "general_development",
            "title": "Тестовая программа",
            "summary": {
                "goal": "Научить основам анализа данных",
                "target_audience": "Взрослые слушатели",
                "form": "очно-заочная",
                "issued_document": "сертификат",
            },
        }
    )


def ready_state() -> ProgramState:
    state = mark_stage_started(
        new_state(SESSION_ID, requested_hours=REQUESTED_HOURS, requirements="Тема"), Stage.COMPETENCES
    )
    state = apply_block(state, Stage.COMPETENCES, competences_block())
    state = apply_block(state, Stage.HOURS, hours_block())
    return apply_block(state, Stage.SOURCES, SourcesBlock(sources=[]))


def approved_state() -> ProgramState:
    state = apply_block(ready_state(), Stage.SUMMARY, summary_block())
    return approve_state(state)


def test_stage_order_is_the_pipeline_order():
    assert STAGE_ORDER == (Stage.COMPETENCES, Stage.HOURS, Stage.SOURCES, Stage.SUMMARY)


def test_new_state_starts_at_the_first_stage():
    state = new_state(SESSION_ID, requested_hours=REQUESTED_HOURS, requirements="Тема", title="Черновик")

    assert state.meta.session_id == SESSION_ID
    assert state.meta.version == 0
    assert state.status is ProgramStatus.GENERATING
    assert state.current_stage is Stage.COMPETENCES
    assert state.stage_status(Stage.COMPETENCES) is StageStatus.IN_PROGRESS
    assert state.stage_status(Stage.HOURS) is StageStatus.PENDING
    assert state.next_stage() is Stage.COMPETENCES
    assert state.requested_hours == REQUESTED_HOURS
    assert state.title == "Черновик"


def test_new_state_accepts_an_explicit_run_id():
    state = new_state(SESSION_ID, requested_hours=2, requirements="Тема", run_id="run-1")

    assert state.meta.run_id == "run-1"


def test_mark_stage_started_bumps_the_attempt_and_the_version():
    state = new_state(SESSION_ID, requested_hours=2, requirements="Тема")

    started = mark_stage_started(state, Stage.COMPETENCES)

    assert started.meta.version == state.meta.version + 1
    assert started.attempts == {"competences": 1}
    assert started.stage_status(Stage.COMPETENCES) is StageStatus.IN_PROGRESS
    assert started.last_error is None
    assert started.failed is False


def test_mark_stage_started_is_a_no_op_for_a_completed_stage():
    state = ready_state()

    started = mark_stage_started(state, Stage.COMPETENCES)

    assert started is state


def test_mark_stage_failed_keeps_the_stage_retryable_until_the_budget_is_spent():
    state = new_state(SESSION_ID, requested_hours=2, requirements="Тема")
    state = mark_stage_started(state, Stage.COMPETENCES)

    failed = mark_stage_failed(state, Stage.COMPETENCES, "провайдер упал")

    assert failed.stage_status(Stage.COMPETENCES) is StageStatus.IN_PROGRESS
    assert failed.last_error == "провайдер упал"
    assert failed.failed is False
    assert failed.meta.version == state.meta.version + 1


def test_mark_stage_failed_marks_the_state_terminal_after_the_budget():
    state = new_state(SESSION_ID, requested_hours=2, requirements="Тема")
    for _ in range(MAX_STAGE_ATTEMPTS):
        state = mark_stage_started(state, Stage.COMPETENCES)

    failed = mark_stage_failed(state, Stage.COMPETENCES, "сломанный этап")

    assert failed.stage_status(Stage.COMPETENCES) is StageStatus.FAILED
    assert failed.failed is True
    assert failed.attempts["competences"] == MAX_STAGE_ATTEMPTS


def test_mark_stage_failed_ignores_a_completed_stage():
    state = ready_state()

    failed = mark_stage_failed(state, Stage.COMPETENCES, "поздно")

    assert failed is state


def test_mark_run_failed_is_terminal_and_keeps_the_blocks():
    state = apply_block(ready_state(), Stage.SUMMARY, summary_block())

    failed = mark_run_failed(state, "черновик не прошёл проверку")

    assert failed.failed is True
    assert failed.last_error == "черновик не прошёл проверку"
    assert failed.competencies == state.competencies
    assert failed.curriculum == state.curriculum
    assert failed.meta.version == state.meta.version + 1


def test_mark_run_failed_is_idempotent():
    state = mark_run_failed(ready_state(), "первая ошибка")

    again = mark_run_failed(state, "вторая ошибка")

    assert again is state


def test_apply_block_advances_to_the_next_stage():
    state = new_state(SESSION_ID, requested_hours=REQUESTED_HOURS, requirements="Тема")

    state = apply_block(state, Stage.COMPETENCES, competences_block())

    assert state.stage_status(Stage.COMPETENCES) is StageStatus.COMPLETED
    assert state.stage_status(Stage.HOURS) is StageStatus.IN_PROGRESS
    assert state.current_stage is Stage.HOURS
    assert state.competencies[0].id == "comp-01"


def test_apply_block_can_be_replayed_without_changing_the_state():
    state = apply_block(
        new_state(SESSION_ID, requested_hours=REQUESTED_HOURS, requirements="Тема"),
        Stage.COMPETENCES,
        competences_block(),
    )

    replayed = apply_block(state, Stage.COMPETENCES, competences_block())

    assert replayed is state


def test_apply_block_refuses_a_block_for_a_later_stage():
    state = new_state(SESSION_ID, requested_hours=REQUESTED_HOURS, requirements="Тема")

    with pytest.raises(ValueError, match="not the current stage"):
        apply_block(state, Stage.HOURS, hours_block())


def test_the_last_block_moves_the_state_to_pending_approval():
    state = apply_block(ready_state(), Stage.SUMMARY, summary_block())

    assert state.status is ProgramStatus.PENDING_APPROVAL
    assert state.current_stage is None
    assert state.next_stage() is None
    assert state.stage_status(Stage.SUMMARY) is StageStatus.COMPLETED


def test_to_draft_refuses_an_incomplete_state():
    state = new_state(SESSION_ID, requested_hours=REQUESTED_HOURS, requirements="Тема")

    with pytest.raises(ValueError, match="draft is incomplete"):
        state.to_draft()


def test_to_draft_runs_the_cross_block_validators():
    state = apply_block(ready_state(), Stage.SUMMARY, summary_block())
    broken = state.model_copy(update={"curriculum": hours_block(hours_per_day=10).curriculum})

    with pytest.raises(ValidationError, match="does not fit the requested volume"):
        broken.to_draft()


def test_to_draft_returns_the_requested_hours_and_sources():
    state = apply_block(ready_state(), Stage.SUMMARY, summary_block())
    source = Source(id="src-01", kind=SourceKind.NORMATIVE_ACT, title="ФГОС")
    state = state.model_copy(update={"sources": [source]})

    draft = state.to_draft()

    assert draft.requested_hours == REQUESTED_HOURS
    assert draft.sources == [source]


def test_set_known_sources_sorts_without_a_version_bump():
    state = new_state(SESSION_ID, requested_hours=2, requirements="Тема")

    updated = set_known_sources(state, {"src-02", "src-01"})

    assert updated.known_source_ids == ["src-01", "src-02"]
    assert updated.meta.version == state.meta.version


def test_approve_state_requires_a_complete_draft():
    state = ready_state()

    with pytest.raises(ValueError, match="not ready"):
        approve_state(state)


def test_approve_state_marks_the_draft_approved():
    state = apply_block(ready_state(), Stage.SUMMARY, summary_block())

    approved = approve_state(state)

    assert approved.status is ProgramStatus.APPROVED
    assert approved.approved_at is not None
    assert approved.current_stage is None
    assert approved.meta.version == state.meta.version + 1


def test_approve_state_is_idempotent():
    state = approved_state()

    assert approve_state(state) is state


def test_mark_finalized_sets_the_flag_and_bumps_the_version():
    state = approved_state()

    final = mark_finalized(state)

    assert final.finalized is True
    assert final.meta.version == state.meta.version + 1


@pytest.mark.parametrize(
    "text",
    ["подтверждаю", "Подтверждаю.", "  утверждаю  ", "согласен", "да, подтверждаю", "черновик утверждаю"],
)
def test_explicit_confirmation_phrases_are_recognised(text):
    assert is_approval_message(text) is True


@pytest.mark.parametrize("text", ["не подтверждаю", "подтверждаю, но переделай", "спасибо", "", "да"])
def test_other_messages_are_not_confirmations(text):
    assert is_approval_message(text) is False
