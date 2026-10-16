"""Tests for the file workers in :mod:`app.tasks.files`.

Every test here drives a real task body through ``tests.helpers.run_task``,
against a real PostgreSQL, a real Redis and a real MinIO. Nothing is enqueued to
RabbitMQ: the tasks that fan out to siblings call ``.kiq`` on a decorated task,
so every such attribute is monkeypatched with an ``AsyncMock``.

The rows these tests create are **committed**, unlike the rows the shared
``db_session`` fixture creates. That is deliberate and unavoidable: the workers
open their own session through ``tsq_db()``, which is bound to the pool, while
``db_session`` holds everything inside a transaction it rolls back. A committed
row written through the pool is what a worker actually sees, so the ``world``
fixture below writes through a pool-bound session and deletes what it created.
"""

import uuid
from datetime import UTC, datetime, timedelta
from io import BytesIO
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

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
from app.storage import (
    get_s3_avatar_processed_key,
    get_s3_avatar_unprocessed_key,
    storage,
)
from app.tasks import files
from app.tasks.files import (
    _get_user_uuid_from_attachment_id,
    _prepare_for_llm,
    _redis_error,
    _s3_get_object,
    _s3_try_delete,
    cleanup_orphan_attachments,
    cleanup_stale_results,
    process_attachment,
    process_avatar,
    process_document,
)
from tests.helpers import run_task

#: A syntactically valid but empty PDF. Nothing parses it — the worker hands the
#: bytes straight to the provider — so a real document would only be a liability.
PDF_BYTES = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
)

#: Well past the one-hour cutoff both cleanup tasks use.
STALE = datetime.now(tz=UTC) - timedelta(hours=3)


def png_bytes(size: tuple[int, int] = (64, 48), color: tuple[int, int, int] = (200, 30, 30)) -> bytes:
    """A small real PNG, so Pillow accepts it and ``normalize_image`` converts it."""
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


class World:
    """Committed database rows plus a pool-bound reader, with teardown."""

    def __init__(self, session: AsyncSession, maker: async_sessionmaker):
        self.session = session
        self._maker = maker
        self._user_ids: list[int] = []
        self._session_ids: list[uuid.UUID] = []

    async def user(self) -> User:
        row = User(username=f"w-{uuid.uuid4().hex[:12]}", password_hash="not-a-real-hash")
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        self._user_ids.append(row.id)
        return row

    async def chat(self, user: User, name: str = "Новый чат") -> ChatSession:
        # ``session_id`` is the primary key and has no default: the route layer
        # mints it, so the tests have to as well.
        row = ChatSession(session_id=uuid.uuid4(), user_id=user.id, name=name)
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        self._session_ids.append(row.session_id)
        return row

    async def attachment(
        self,
        chat: ChatSession,
        name: str = "drawing.pdf",
        s3_key: str | None = None,
        ready: bool = False,
        timestamp: datetime | None = None,
    ) -> Attachment:
        row = Attachment(
            session_id=chat.session_id,
            name=name,
            s3_key=s3_key or f"attachments/{chat.session_id}/{uuid.uuid4().hex}",
            ready=ready,
        )
        if timestamp is not None:
            row.timestamp = timestamp
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def uploadable(self, attachment: Attachment, s3_key: str) -> ProcessingResultUploadable:
        row = ProcessingResultUploadable(attachment_id=attachment.id, s3_key=s3_key, sber_id=None)
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def fresh_get(self, model, pk):
        """Read through a brand new session, so a worker's commit is visible."""
        async with self._maker() as reader:
            return await reader.get(model, pk)

    async def uploadables_of(self, attachment_id: int) -> list[ProcessingResultUploadable]:
        async with self._maker() as reader:
            stmt = (
                select(ProcessingResultUploadable)
                .where(ProcessingResultUploadable.attachment_id == attachment_id)
                .order_by(ProcessingResultUploadable.id)
            )
            return list((await reader.scalars(stmt)).all())

    async def purge(self) -> None:
        session_stmt = select(Attachment.id).where(Attachment.session_id.in_(self._session_ids))
        await self.session.execute(
            delete(ProcessingResultUploadable).where(ProcessingResultUploadable.attachment_id.in_(session_stmt))
        )
        await self.session.execute(delete(Attachment).where(Attachment.session_id.in_(self._session_ids)))
        await self.session.execute(delete(ChatSession).where(ChatSession.session_id.in_(self._session_ids)))
        await self.session.execute(delete(User).where(User.id.in_(self._user_ids)))
        await self.session.commit()


@pytest_asyncio.fixture()
async def world(engine: AsyncEngine, task_db: None):
    """Committed rows, deleted again when the test finishes."""
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with maker() as session:
        api = World(session, maker)
        try:
            yield api
        finally:
            await api.purge()


@pytest_asyncio.fixture()
async def rconn(redis_session: None):
    """A Redis client for assertions.

    Local rather than the shared ``redis_client`` fixture: that one calls
    ``ConnectionPool.get_connection()`` without awaiting it, which redis-py 8
    rejects. :func:`get_redis_client` is the supported path and is exactly what
    the workers themselves use.
    """
    async with get_redis_client() as conn:
        yield conn


async def s3_exists(bucket: str, key: str) -> bool:
    async with storage.internal_client() as s3:
        try:
            await s3.head_object(Bucket=bucket, Key=key)
        except Exception:
            return False
    return True


async def s3_read(bucket: str, key: str) -> tuple[bytes, str]:
    return await _s3_get_object(bucket, key)


def block_kiq(monkeypatch, *tasks) -> dict:
    """Replace the ``.kiq`` of each task with an ``AsyncMock``; return them by name.

    ``kiq`` is a bound method, so it can only be shadowed on the decorated-task
    instance. That is what a dotted ``monkeypatch.setattr`` target does.
    """
    mocks = {}
    for task in tasks:
        mock = AsyncMock()
        # taskiq names tasks "<module>:<function>"; the dotted path needs only the function.
        monkeypatch.setattr(f"{task.__module__}.{task.task_name.split(':')[-1]}.kiq", mock)
        mocks[task.task_name.split(":")[-1]] = mock
    return mocks


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------


def test_prepare_for_llm_returns_the_input_unchanged():
    """The processing seam is a pass-through, so this pins that it is not lossy.

    It is the one function standing in for whatever a real implementation would do,
    so the test that matters is the one that fails loudly when it starts changing
    bytes without anything downstream knowing.
    """
    assert _prepare_for_llm("drawing.pdf", PDF_BYTES) is PDF_BYTES
    assert _prepare_for_llm("photo.png", b"") == b""


async def test_get_user_uuid_from_attachment_id_with_session(world, task_db):
    user = await world.user()
    chat = await world.chat(user)
    attachment = await world.attachment(chat)

    assert await _get_user_uuid_from_attachment_id(attachment.id, db=world.session) == user.uuid


async def test_get_user_uuid_from_attachment_id_without_session(world, task_db):
    """The ``db is None`` branch opens its own session via ``tsq_db``."""
    user = await world.user()
    chat = await world.chat(user)
    attachment = await world.attachment(chat)

    assert await _get_user_uuid_from_attachment_id(attachment.id, db=None) == user.uuid


async def test_get_user_uuid_from_attachment_id_unknown_attachment(world, task_db):
    assert await _get_user_uuid_from_attachment_id(10**9, db=None) is None


async def test_redis_error_writes_status(redis_session, rconn):
    key = get_attachment_status_key(4321)
    await _redis_error(key)
    assert await rconn.get(key) == "error"


# ---------------------------------------------------------------------------
# s3 helpers
# ---------------------------------------------------------------------------


async def test_s3_try_delete_removes_object(s3, s3_put):
    key = "try-delete/target.bin"
    await s3_put(b"payload", key, content_type="application/octet-stream")
    assert await s3_exists("uploads", key)

    await _s3_try_delete(key)

    assert not await s3_exists("uploads", key)


async def test_s3_try_delete_swallows_errors(monkeypatch, s3):
    """A broken storage client must not propagate out of the cleanup gather."""

    class _Broken:
        async def __aenter__(self):
            raise RuntimeError("storage is down")

        async def __aexit__(self, *exc_info):
            return False

    monkeypatch.setattr(storage, "internal_client", lambda: _Broken())

    await _s3_try_delete("whatever/key")


async def test_s3_get_object_returns_bytes_and_content_type(s3, s3_put):
    key = "get-object/sample.png"
    data = png_bytes()
    await s3_put(data, key, content_type="image/png")

    assert await _s3_get_object("uploads", key) == (data, "image/png")


# ---------------------------------------------------------------------------
# cleanup_orphan_attachments
# ---------------------------------------------------------------------------


async def test_cleanup_orphan_attachments_deletes_stale_unready(world, task_db, s3, s3_put, redis_session):
    user = await world.user()
    chat = await world.chat(user)
    stale = await world.attachment(chat, name="stale.pdf", s3_key="orphans/stale.pdf", timestamp=STALE)
    await s3_put(b"stale", stale.s3_key)

    await run_task(cleanup_orphan_attachments)

    assert await world.fresh_get(Attachment, stale.id) is None


async def test_cleanup_orphan_attachments_leaves_s3_objects_behind(world, task_db, s3, s3_put, redis_session):
    """Pins a bug in the worker, not a desired behaviour.

    ``cleanup_orphan_attachments`` calls ``orphans.all()`` to log the count, and
    that **exhausts the ``ScalarResult``**. The ``for a in orphans`` on the next
    line then iterates an already-closed result and yields nothing, so
    ``asyncio.gather`` runs zero ``_s3_try_delete`` calls and every orphan object
    is leaked in MinIO even though its database row is gone.

    When the worker is fixed, this test fails and its assertion flips to
    ``not await s3_exists(...)``. That is deliberate: a silent leak in an hourly
    cleanup job is worth a loud test failure, not a green one.
    """
    user = await world.user()
    chat = await world.chat(user)
    stale = await world.attachment(chat, name="leaked.pdf", s3_key="orphans/leaked.pdf", timestamp=STALE)
    await s3_put(b"leaked", stale.s3_key)

    await run_task(cleanup_orphan_attachments)

    assert await world.fresh_get(Attachment, stale.id) is None
    assert await s3_exists("uploads", stale.s3_key)


async def test_cleanup_orphan_attachments_keeps_fresh_and_ready(world, task_db, s3, redis_session):
    user = await world.user()
    chat = await world.chat(user)
    fresh_unready = await world.attachment(chat, name="fresh.pdf", s3_key="orphans/fresh.pdf")
    old_ready = await world.attachment(chat, name="ready.pdf", s3_key="orphans/ready.pdf", ready=True, timestamp=STALE)

    await run_task(cleanup_orphan_attachments)

    assert await world.fresh_get(Attachment, fresh_unready.id) is not None
    assert await world.fresh_get(Attachment, old_ready.id) is not None


async def test_cleanup_orphan_attachments_without_orphans(world, task_db, s3, redis_session):
    await run_task(cleanup_orphan_attachments)


# ---------------------------------------------------------------------------
# cleanup_stale_results
# ---------------------------------------------------------------------------


async def test_cleanup_stale_results_deletes_old_rows(world, task_db, s3, redis_session):
    user = await world.user()
    chat = await world.chat(user)
    stale = await world.attachment(chat, s3_key="stale/one.pdf", timestamp=STALE)
    await world.uploadable(stale, "stale/one.png")

    fresh = await world.attachment(chat, s3_key="stale/two.pdf")
    await world.uploadable(fresh, "stale/two.png")

    await run_task(cleanup_stale_results)

    assert await world.uploadables_of(stale.id) == []
    assert len(await world.uploadables_of(fresh.id)) == 1


async def test_cleanup_stale_results_without_stale_rows(world, task_db, s3, redis_session):
    await run_task(cleanup_stale_results)


# ---------------------------------------------------------------------------
# process_avatar
# ---------------------------------------------------------------------------


async def test_process_avatar_without_waiting_key(redis_session, s3):
    user_uuid = uuid.uuid4()
    await run_task(process_avatar, user_uuid)
    assert not await s3_exists("avatars", get_s3_avatar_processed_key(user_uuid))


async def test_process_avatar_when_s3_read_fails(redis_session, s3, rconn):
    user_uuid = uuid.uuid4()
    key = get_s3_avatar_unprocessed_key(user_uuid)
    await rconn.set(get_avatar_waiting_key(user_uuid), key)

    await run_task(process_avatar, user_uuid)

    # The key is consumed by ``getdel`` even though the read failed.
    assert await rconn.get(get_avatar_waiting_key(user_uuid)) is None
    assert not await s3_exists("avatars", get_s3_avatar_processed_key(user_uuid))


async def test_process_avatar_with_invalid_image(redis_session, s3, s3_put, rconn):
    user_uuid = uuid.uuid4()
    key = get_s3_avatar_unprocessed_key(user_uuid)
    await s3_put(b"definitely not an image", key, bucket="avatars", content_type="image/png")
    await rconn.set(get_avatar_waiting_key(user_uuid), key)

    await run_task(process_avatar, user_uuid)

    assert not await s3_exists("avatars", get_s3_avatar_processed_key(user_uuid))
    assert await s3_exists("avatars", key)


async def test_process_avatar_happy_path(redis_session, s3, s3_put, rconn):
    user_uuid = uuid.uuid4()
    source_key = get_s3_avatar_unprocessed_key(user_uuid)
    processed_key = get_s3_avatar_processed_key(user_uuid)
    await s3_put(png_bytes(), source_key, bucket="avatars", content_type="image/png")
    await rconn.set(get_avatar_waiting_key(user_uuid), source_key)
    await rconn.set(get_avatar_url_key(user_uuid), "http://minio/avatars/old")

    await run_task(process_avatar, user_uuid)

    assert await s3_exists("avatars", processed_key)
    stored, content_type = await s3_read("avatars", processed_key)
    assert content_type == "image/webp"
    with Image.open(BytesIO(stored)) as image:
        assert image.size == (200, 200)
        assert image.format == "WEBP"

    assert not await s3_exists("avatars", source_key)
    # The cached public URL is invalidated, the waiting key was already consumed.
    assert await rconn.get(get_avatar_url_key(user_uuid)) is None
    assert await rconn.get(get_avatar_waiting_key(user_uuid)) is None


# ---------------------------------------------------------------------------
# process_attachment
# ---------------------------------------------------------------------------


async def test_process_attachment_missing_row_sets_error(world, task_db, s3, redis_session, rconn):
    status_key = get_attachment_status_key(777001)
    await run_task(process_attachment, 777001)
    assert await rconn.get(status_key) == "error"


async def test_process_attachment_missing_object_sets_error(world, task_db, s3, redis_session, rconn, monkeypatch):
    user = await world.user()
    chat = await world.chat(user)
    attachment = await world.attachment(chat, s3_key="uploads/does-not-exist.pdf")
    monkeypatch.setattr(files.process_document, "kiq", AsyncMock())

    await run_task(process_attachment, attachment.id)

    assert await rconn.get(get_attachment_status_key(attachment.id)) == "error"


@pytest.mark.parametrize(
    "content_type",
    ["application/pdf", "image/png", "image/jpeg"],
)
async def test_process_attachment_accepts_every_allowed_content_type(
    world, task_db, s3, s3_put, redis_session, rconn, monkeypatch, content_type
):
    """Every type the route allowlists reaches the handler, and nothing else does.

    The dispatch is a `match` on the object's stored content type, so a type added
    to `ALLOWED_CHAT_CONTENT_TYPES` without a matching arm here would be accepted
    by the upload route and then rejected by the worker.
    """
    user = await world.user()
    chat = await world.chat(user)
    attachment = await world.attachment(chat, s3_key=f"routing/{uuid.uuid4().hex}")
    await s3_put(png_bytes(), attachment.s3_key, content_type=content_type)
    mocks = block_kiq(monkeypatch, process_document)

    await run_task(process_attachment, attachment.id)

    mocks["process_document"].assert_awaited_once_with(attachment.id)
    assert await rconn.get(get_attachment_status_key(attachment.id)) is None


async def test_process_attachment_unknown_content_type_sets_error(
    world, task_db, s3, s3_put, redis_session, rconn, monkeypatch
):
    user = await world.user()
    chat = await world.chat(user)
    attachment = await world.attachment(chat, s3_key=f"routing/{uuid.uuid4().hex}.txt")
    await s3_put(b"plain text", attachment.s3_key, content_type="text/plain")
    mocks = block_kiq(monkeypatch, process_document)

    await run_task(process_attachment, attachment.id)

    assert await rconn.get(get_attachment_status_key(attachment.id)) == "error"
    for mock in mocks.values():
        mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# process_document
# ---------------------------------------------------------------------------


async def test_process_document_uploads_a_pdf_unchanged(world, task_db, s3, s3_put, redis_session, rconn):
    """A PDF goes to the provider as the bytes that were uploaded.

    Nothing rasterises it any more, so the artifact row points at the original
    object rather than at a derived one, and there is exactly one of them.
    """
    user = await world.user()
    chat = await world.chat(user)
    attachment = await world.attachment(chat, name="drawing.pdf", s3_key="pdf/happy.pdf")
    await s3_put(PDF_BYTES, attachment.s3_key, content_type="application/pdf")

    await run_task(process_document, attachment.id)

    rows = await world.uploadables_of(attachment.id)
    assert len(rows) == 1
    assert rows[0].s3_key == attachment.s3_key
    assert rows[0].sber_id.startswith("mock-file-")
    assert (await world.fresh_get(Attachment, attachment.id)).ready is True
    assert await rconn.get(get_attachment_status_key(attachment.id)) == "completed"


async def test_process_document_uploads_an_image_unchanged(world, task_db, s3, s3_put, redis_session, rconn):
    user = await world.user()
    chat = await world.chat(user)
    attachment = await world.attachment(chat, name="photo.png", s3_key=f"images/{uuid.uuid4().hex}.png")
    await s3_put(png_bytes(), attachment.s3_key, content_type="image/png")

    await run_task(process_document, attachment.id)

    rows = await world.uploadables_of(attachment.id)
    assert len(rows) == 1
    assert rows[0].s3_key == attachment.s3_key
    assert rows[0].sber_id.startswith("mock-file-")
    assert (await world.fresh_get(Attachment, attachment.id)).ready is True
    assert await rconn.get(get_attachment_status_key(attachment.id)) == "completed"


async def test_process_document_missing_attachment_sets_error(world, task_db, s3, redis_session, rconn):
    await run_task(process_document, 777002)
    assert await rconn.get(get_attachment_status_key(777002)) == "error"


async def test_process_document_missing_object_sets_error(world, task_db, s3, redis_session, rconn):
    user = await world.user()
    chat = await world.chat(user)
    attachment = await world.attachment(chat, s3_key="pdf/absent.pdf")

    await run_task(process_document, attachment.id)

    assert await rconn.get(get_attachment_status_key(attachment.id)) == "error"
    assert await world.uploadables_of(attachment.id) == []
    assert (await world.fresh_get(Attachment, attachment.id)).ready is False


async def test_process_document_failure_in_the_seam_sets_error(
    world, task_db, s3, s3_put, redis_session, rconn, monkeypatch
):
    """The worker's ``except`` branch, driven by a processing step that raises.

    `_prepare_for_llm` cannot raise while it is a pass-through, so this is the only
    way to cover the failure path it will gain the moment it does real work.
    """
    user = await world.user()
    chat = await world.chat(user)
    attachment = await world.attachment(chat, name="drawing.pdf", s3_key="pdf/raises.pdf")
    await s3_put(PDF_BYTES, attachment.s3_key, content_type="application/pdf")
    monkeypatch.setattr(files, "_prepare_for_llm", Mock(side_effect=ValueError("bad document")))

    await run_task(process_document, attachment.id)

    assert await rconn.get(get_attachment_status_key(attachment.id)) == "error"
    assert await world.uploadables_of(attachment.id) == []


async def test_process_document_missing_user_uuid_sets_error(
    world, task_db, s3, s3_put, redis_session, rconn, monkeypatch
):
    """``required_data_null`` is unreachable through the schema, so stub the lookup."""
    user = await world.user()
    chat = await world.chat(user)
    attachment = await world.attachment(chat, name="photo.png", s3_key=f"images/{uuid.uuid4().hex}.png")
    await s3_put(png_bytes(), attachment.s3_key, content_type="image/png")
    monkeypatch.setattr(files, "_get_user_uuid_from_attachment_id", AsyncMock(return_value=None))

    await run_task(process_document, attachment.id)

    assert await rconn.get(get_attachment_status_key(attachment.id)) == "error"
    assert await world.uploadables_of(attachment.id) == []
