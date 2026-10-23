"""Tests for the export of a finished programme.

The programme leaves the system as two files: the document and the slides.
They are attachments of the chat, so the client fetches them with the
attachment route it already uses — there is no separate download endpoint.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.agentic.state import ProgramState
from app.models.chat import Attachment
from app.tasks import exports
from app.tasks.exports import export_program
from tests.helpers import run_task


async def test_a_finished_programme_is_exported(
    chat: uuid.UUID, task_db: None, redis_client, s3: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The document and the slides are written and their ids are recorded."""
    state = _a_finished_run(chat)
    await _save(state)

    await run_task(export_program, chat, state.run_id)

    saved = await _state(chat)
    assert saved is not None
    assert saved.exports is not None
    assert saved.exports.document_attachment_id is not None
    assert saved.exports.slides_attachment_id is not None


async def test_the_export_is_an_attachment_of_the_chat(chat: uuid.UUID, task_db: None, redis_client, s3: None) -> None:
    """The client downloads it the way it downloads a file the methodist uploaded."""
    state = _a_finished_run(chat)
    await _save(state)

    await run_task(export_program, chat, state.run_id)

    async with exports.db_session() as db:
        rows = list(await db.scalars(select(Attachment).where(Attachment.session_id == chat)))
    assert sorted(row.name for row in rows) == ["programme-slides.pptx", "programme.docx", "programme.json"]
    assert all(row.ready for row in rows)
    assert all(row.s3_key for row in rows)


async def test_a_programme_that_is_not_complete_is_not_exported(
    chat: uuid.UUID, task_db: None, redis_client, s3: None
) -> None:
    """Without the content there is nothing to export."""
    from app.agentic.state import new_state

    state = new_state(chat, requested_hours=72, requirements="Программа")
    await _save(state)

    await run_task(export_program, chat, state.run_id)

    saved = await _state(chat)
    assert saved is not None
    assert saved.exports is None


async def test_a_second_export_replaces_the_first(chat: uuid.UUID, task_db: None, redis_client, s3: None) -> None:
    """The export is one file per programme, not a file per request."""
    state = _a_finished_run(chat)
    await _save(state)

    await run_task(export_program, chat, state.run_id)
    await run_task(export_program, chat, state.run_id)

    async with exports.db_session() as db:
        rows = list(await db.scalars(select(Attachment).where(Attachment.session_id == chat)))
    assert len(rows) == 3
    saved = await _state(chat)
    assert saved is not None
    assert saved.exports is not None


async def test_an_export_of_another_run_is_refused(chat: uuid.UUID, task_db: None, redis_client, s3: None) -> None:
    """A task that arrives after the methodist started a new run writes nothing."""
    state = _a_finished_run(chat)
    await _save(state)

    await run_task(export_program, chat, uuid.uuid4())

    saved = await _state(chat)
    assert saved is not None
    assert saved.exports is None


def _a_finished_run(chat: uuid.UUID) -> ProgramState:
    """A finished programme: the skeleton, the content and the approval."""
    import json
    from pathlib import Path

    from app.agentic.state import (
        AssessmentBlock,
        CompetencesBlock,
        HoursBlock,
        MaterialsBlock,
        SourcesBlock,
        SummaryBlock,
        new_state,
    )

    raw = json.loads(
        (Path(__file__).resolve().parents[1] / "examples" / "document_state_example.json").read_text(encoding="utf-8")
    )
    state = new_state(chat, requested_hours=72, requirements="Программа")
    state = state.with_competences(
        CompetencesBlock(
            kind=raw["meta"]["kind"],
            title=raw["meta"]["title"],
            competencies=raw["competencies"],
        ),
        [],
    )
    state = state.with_curriculum(HoursBlock(curriculum=raw["curriculum"]))
    state = state.with_sources(SourcesBlock(sources=raw["sources"]))
    state = state.with_summary(SummaryBlock(summary=raw["summary"]), [])
    state = state.with_assessment(AssessmentBlock(assessment=raw["assessment"]))
    state = state.with_materials(MaterialsBlock(materials=raw["materials"]))
    from app.agentic.state import STAGE_ORDER

    for stage in STAGE_ORDER:
        state = state.completed(stage)
    return state


async def _save(state: ProgramState) -> None:
    from app.agentic import store

    await store.save(state)


async def _state(chat: uuid.UUID) -> ProgramState | None:
    from app.agentic import store

    return await store.load(chat)


async def test_a_finished_programme_is_exported_as_json(chat: uuid.UUID, task_db: None, redis_client, s3: None) -> None:
    """The JSON holds the whole document, not only the blocks that are there."""
    import json as _json

    from app.storage import storage

    state = _a_finished_run(chat)
    await _save(state)

    await run_task(export_program, chat, state.run_id)

    saved = await _state(chat)
    assert saved is not None and saved.exports is not None
    assert saved.exports.json_attachment_id is not None
    async with exports.db_session() as db:
        row = await db.get(Attachment, saved.exports.json_attachment_id)
    assert row is not None and row.name == "programme.json"
    async with storage.internal_client() as s3:
        response = await s3.get_object(Bucket="uploads", Key=row.s3_key)
        async with response["Body"] as body:
            payload = _json.loads((await body.read()).decode("utf-8"))
    assert "draft" in payload and "assessment" in payload and "materials" in payload
    assert "approval" in payload and "report" in payload
    assert payload["draft"]["kind"] == "professional_development"


async def test_a_programme_without_slides_is_exported_without_a_pptx(
    chat: uuid.UUID, task_db: None, redis_client, s3: None
) -> None:
    """A title-only PPTX is not the criterion's slides, so it is not written."""
    from app.agentic.state import MaterialsBlock

    state = _a_finished_run(chat)
    empty = state.with_materials(MaterialsBlock(materials={"lecture_plans": [], "slide_decks": []}))
    await _save(empty)

    await run_task(export_program, chat, empty.run_id)

    saved = await _state(chat)
    assert saved is not None and saved.exports is not None
    assert saved.exports.document_attachment_id is not None
    assert saved.exports.json_attachment_id is not None
    assert saved.exports.slides_attachment_id is None
    async with exports.db_session() as db:
        rows = list(await db.scalars(select(Attachment).where(Attachment.session_id == chat)))
    assert sorted(row.name for row in rows) == ["programme.docx", "programme.json"]
