import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import structlog.stdlib
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import (
    get_attachment_status_key,
    get_avatar_url_key,
    get_avatar_waiting_key,
    get_redis_client,
)
from app.models.auth import User
from app.models.chat import (
    Attachment,
    ChatSession,
    ProcessingResultUploadable,
)
from app.providers.containers import provider
from app.storage import get_s3_avatar_processed_key, storage
from app.tasks.conf.broker import broker, tsq_db
from app.util import normalize_image

log = structlog.stdlib.get_logger(__name__)


def _prepare_for_llm(name: str, data: bytes) -> bytes:
    """Transform an attachment before it is handed to the provider.

    This is the seam a content-type-specific processing step belongs in: extract
    the text of a document, rasterise a page, summarise a table. Whatever it does,
    it has to be cheap enough to be worth it and it must not block the event loop,
    so a caller doing real work offloads it with `asyncio.to_thread`.

    It returns the input unchanged today. That is deliberate rather than a stub
    left behind: nothing about an attachment has to be transformed for the chat to
    work, and the pipeline is here to show where such a step would sit, not to
    pretend one exists.
    """
    log.debug("prepare_attachment", name=name, size=len(data))
    return data


async def _get_user_uuid_from_attachment_id(attachment_id: int, db: AsyncSession | None) -> uuid.UUID | None:
    q = select(User.uuid).join(User.chat_sessions).join(ChatSession.attachments).where(Attachment.id == attachment_id)
    if db is not None:
        return await db.scalar(q)
    async with tsq_db() as db:
        return await db.scalar(q)


async def _redis_error(key: str):
    async with get_redis_client() as redis:
        await redis.set(key, "error", nx=False, ex=3600)


async def _s3_try_delete(s3_key: str):
    try:
        async with storage.internal_client() as s3:
            await s3.delete_object(Bucket="uploads", Key=s3_key)
    except Exception as e:
        log.warning("delete_upload_s3_failure", s3_key=s3_key, exc=e)


async def _s3_get_object(bucket: str, s3_key: str):
    async with storage.internal_client() as s3:
        resp = await s3.get_object(Bucket=bucket, Key=s3_key)
        content_type = resp["ContentType"]
        async with resp["Body"] as body:
            return await body.read(), content_type


@broker.task(queue_name="default", schedule=[{"interval": timedelta(hours=1)}])
async def cleanup_orphan_attachments():
    log.info("orphan_attachments_cleanup_start")
    async with tsq_db() as db:
        clause = (
            Attachment.ready.is_(False),
            Attachment.timestamp < datetime.now(tz=UTC) - timedelta(hours=1),
        )
        orphans = await db.scalars(select(Attachment).where(*clause))
        log.info("orphan_attachments_cleanup_count", count=len(orphans.all()))
        tasks = [_s3_try_delete(a.s3_key) for a in orphans]
        await asyncio.gather(*tasks)
        await db.execute(delete(Attachment).where(*clause))
        await db.commit()
        log.info("orphan_attachments_cleanup_ok")


@broker.task(queue_name="default", schedule=[{"interval": timedelta(hours=1)}])
async def cleanup_stale_results():
    log.info("stale_results_cleanup_start")
    async with tsq_db() as db:
        cutoff = datetime.now(tz=UTC) - timedelta(hours=1)
        old_attachment_ids = select(Attachment.id).where(Attachment.timestamp < cutoff)
        res = await db.execute(
            delete(ProcessingResultUploadable).where(ProcessingResultUploadable.attachment_id.in_(old_attachment_ids))
        )
        log.info("cleanup_orphan_attachments_stale_uploadables", count=res.rowcount)
        await db.commit()


@broker.task(queue_name="default")
async def process_avatar(user_uuid: uuid.UUID):
    avatar_waiting_key = get_avatar_waiting_key(user_uuid)
    _log = log.bind(avatar_waiting_key=avatar_waiting_key)
    async with get_redis_client() as redis:
        s3_unprocessed_key: str | None = await redis.getdel(avatar_waiting_key)  # type: ignore
        if s3_unprocessed_key is None:
            _log.warning("inexistent_avatar")
            return

    try:
        data, _ = await _s3_get_object("avatars", s3_unprocessed_key)
    except Exception as e:
        _log.error("s3_avatar_acquire_failed", exc=e)
        return

    try:
        normalized = await asyncio.to_thread(normalize_image, data)
    except ValueError as e:
        _log.error("s3_invalid_image", exc=e)
        return

    async with storage.internal_client() as s3:
        await s3.put_object(
            Bucket="avatars",
            Key=get_s3_avatar_processed_key(user_uuid),
            Body=normalized,
            ContentType="image/webp",
        )
        await s3.delete_object(Bucket="avatars", Key=s3_unprocessed_key)
    async with get_redis_client() as redis:
        await redis.delete(get_avatar_url_key(user_uuid))


@broker.task(queue_name="default")
async def process_attachment(attachment_id: int):
    _log = log.bind(attachment_id=attachment_id)
    redis_status_key = get_attachment_status_key(attachment_id)
    async with tsq_db() as db:
        attachment = await db.get(Attachment, attachment_id)
    if attachment is None:
        _log.error("null_attachment_id")
        await _redis_error(redis_status_key)
        return

    async with storage.internal_client() as s3:
        try:
            resp = await s3.head_object(Bucket="uploads", Key=attachment.s3_key)
        except Exception as e:
            _log.error("s3_attachment_acquire_failed", exc=e)
            await _redis_error(redis_status_key)
            return

    content_type = resp["ContentType"]
    match content_type:
        case (
            "application/pdf"
            | "image/png"
            | "image/jpeg"
            # A programme request arrives with its template or an approved
            # programme as the example, and the provider reads a .docx the way
            # it reads a PDF.
            | "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            # A plain-text reference is handed on as-is; the provider reads it
            # the same way it reads a PDF.
            | "text/plain"
        ):
            await process_document.kiq(attachment_id)
        case _:
            _log.error("s3_bad_content_type", key=attachment.s3_key, content_type=content_type)
            await _redis_error(redis_status_key)
            return


@broker.task(queue_name="default")
async def process_document(attachment_id: int):
    """Hand an uploaded attachment to the provider.

    This is the whole per-type pipeline: fetch the object, run it through
    `_prepare_for_llm`, upload the result, record the artifact, then publish the
    terminal status the frontend is polling for. There is no per-content-type branch
    because no accepted type needs a different preparation today; a type that does
    is added to the `match` above and handled in `_prepare_for_llm`.
    """
    _log = log.bind(attachment_id=attachment_id)
    try:
        async with tsq_db() as db:
            attachment = await db.get(Attachment, attachment_id)
            if attachment is None:
                raise ValueError("null_attachment")
            user_uuid = await _get_user_uuid_from_attachment_id(attachment_id, db=db)
            session_id = await db.scalar(
                select(ChatSession.session_id).join(ChatSession.attachments).where(Attachment.id == attachment_id)
            )
            if user_uuid is None or session_id is None:
                log.error("required_data_null", user_uuid=user_uuid, session_id=session_id)
                raise ValueError("required_data_null")

            data, content_type = await _s3_get_object("uploads", attachment.s3_key)
            prepared = await asyncio.to_thread(_prepare_for_llm, attachment.name, data)
            sber_id = await provider.upload(
                attachment.name,
                prepared,
                x_client_id=user_uuid,
                x_session_id=session_id,
                content_type=content_type,
            )

            uploadable = ProcessingResultUploadable(
                attachment_id=attachment_id,
                s3_key=attachment.s3_key,
                sber_id=sber_id,
            )
            db.add(uploadable)
            attachment.ready = True
            await db.commit()
    except Exception as e:
        log.error("process_document_exception", exc=e)
        await _redis_error(get_attachment_status_key(attachment_id))
    else:
        log.info("process_document_ok", attachment_id=attachment_id)
        async with get_redis_client() as redis:
            await redis.set(get_attachment_status_key(attachment_id), "completed", nx=False, ex=600)
