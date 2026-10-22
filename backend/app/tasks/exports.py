"""Write the finished programme to storage, as a document, as slides and as JSON.

The exports are attachments of the chat, so the client fetches them with the
attachment route it already uses for a file the methodist uploaded: the
programme's state carries their ids, and the client asks that route for the
link. There is no separate download endpoint, because the attachment route
already is one.

The JSON is the whole document, not the draft: a client that reconstructs the
programme from it does not have to know which blocks the state has and which
it does not. The slides are written only when the programme has at least one
deck, because a title-only PPTX is not the "слайды хотя бы для одного модуля"
the criterion asks for.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import structlog.stdlib
from sqlalchemy import select

from app.agentic.errors import IncompleteDraft
from app.agentic.state import ProgramExports, ProgramState
from app.database import db_session
from app.models.chat import Attachment
from app.render import render_program_document
from app.slides import render_slides
from app.storage import get_s3_attachment_key, storage
from app.tasks.conf.broker import broker

log = structlog.stdlib.get_logger(__name__)

#: The names the exports are registered under. A name identifies an export, so
#: a second export replaces the first instead of adding another file.
DOCUMENT_NAME = "programme.docx"
SLIDES_NAME = "programme-slides.pptx"
JSON_NAME = "programme.json"

#: The bucket the exports live in: the one the attachments live in, so the
#: attachment route finds them where it looks.
BUCKET = "uploads"
DOCUMENT_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
SLIDES_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
JSON_MEDIA_TYPE = "application/json"


@broker.task(queue_name="default")
async def export_program(session_id: uuid.UUID, run_id: uuid.UUID) -> None:
    """Write the programme to storage and record the ids of the exports.

    The task runs once the programme is complete, and again whenever a
    revision changes it: the exports are the programme, so they are as stale
    as the state they were made from.
    """
    state = await _load(session_id, run_id)
    if state is None:
        return
    try:
        document, slides, payload = await asyncio.to_thread(_render, state)
    except IncompleteDraft as error:
        log.warning("program_export_incomplete", error=str(error))
        return
    exports = await _store(session_id, document, slides, payload)
    try:
        await store_save(state, exports, run_id)
    except Exception as error:
        log.warning("program_export_not_recorded", error=str(error))


async def _load(session_id: uuid.UUID, run_id: uuid.UUID) -> ProgramState | None:
    """The state of the run, if it is the one the task was enqueued for."""
    from app.agentic import store

    state = await store.load(session_id)
    if state is None or state.run_id != run_id or state.failed:
        return None
    if state.assessment is None or state.materials is None:
        # The programme is not complete, so there is nothing to export yet.
        return None
    return state


def _render(state: ProgramState) -> tuple[bytes, bytes | None, bytes]:
    """The document, the slides and the JSON of a finished programme.

    The slides are ``None`` when the programme has no deck: a title-only PPTX
    is not the criterion's "слайды хотя бы для одного модуля", so it is left
    out rather than written and handed on as if it were one.
    """
    document = state.to_document()
    title = state.title or "Презентационные материалы программы"
    slides = render_slides(state.materials, programme_title=title) if state.materials.slide_decks else None
    payload = json.dumps(
        document.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
    return render_program_document(document), slides, payload


async def _store(
    session_id: uuid.UUID,
    document: bytes,
    slides: bytes | None,
    payload: bytes,
) -> ProgramExports:
    """Store the exports and register them as attachments of the chat."""
    async with storage.internal_client() as s3:
        document_key = get_s3_attachment_key(session_id, DOCUMENT_NAME)
        await s3.put_object(Bucket=BUCKET, Key=document_key, Body=document, ContentType=DOCUMENT_MEDIA_TYPE)
        slides_key = None
        if slides is not None:
            slides_key = get_s3_attachment_key(session_id, SLIDES_NAME)
            await s3.put_object(Bucket=BUCKET, Key=slides_key, Body=slides, ContentType=SLIDES_MEDIA_TYPE)
        json_key = get_s3_attachment_key(session_id, JSON_NAME)
        await s3.put_object(Bucket=BUCKET, Key=json_key, Body=payload, ContentType=JSON_MEDIA_TYPE)

    async with db_session() as db:
        document_id = await _register(db, session_id, DOCUMENT_NAME, document_key)
        slides_id = await _register(db, session_id, SLIDES_NAME, slides_key) if slides_key is not None else None
        json_id = await _register(db, session_id, JSON_NAME, json_key)
        await db.commit()
    log.info(
        "program_exported",
        chat_session_id=str(session_id),
        document=document_id,
        slides=slides_id,
        json=json_id,
    )
    return ProgramExports(
        document_attachment_id=document_id,
        slides_attachment_id=slides_id,
        json_attachment_id=json_id,
    )


async def _register(db, session_id: uuid.UUID, name: str, key: str) -> int:
    """The attachment row of an export, replacing the one of the last export."""
    existing = await db.scalar(select(Attachment).where(Attachment.session_id == session_id, Attachment.name == name))
    if existing is None:
        attachment = Attachment(name=name, session_id=session_id, s3_key=key, ready=True)
        db.add(attachment)
        await db.flush()
        return attachment.id
    existing.s3_key = key
    existing.ready = True
    return existing.id


async def store_save(state: ProgramState, exports: ProgramExports, run_id: uuid.UUID) -> None:
    """Record the exports on the state."""
    from app.agentic import store

    await store.save(state.with_exports(exports), expected_run_id=run_id)
