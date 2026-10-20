import os
import time
import uuid
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager
from datetime import datetime

# These must be set before anything under `app` is imported. `app.storage` builds
# an `ObjectStorage` at import time and `app.providers.containers` builds the
# provider at import time, both reading `os.environ` directly. An autouse fixture
# is too late: fixtures run after collection, and these modules import during it.
# Garage has no way to pre-declare an S3 key in its config, so tests import a
# key the same way conf/garage-init.sh does: by pinning the id and secret here
# and calling `garage key import` against the container.
_S3_ACCESS_KEY = "GKd1a1b2c3d4e5f60718293a4b5c6d"
_S3_SECRET_KEY = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
_GARAGE_RPC_SECRET = _S3_SECRET_KEY
_GARAGE_ADMIN_TOKEN = _S3_SECRET_KEY

os.environ.setdefault("SBER_API_KEY", "mock")
os.environ.setdefault("SBER_API_SCOPE", "PERS")
os.environ.setdefault("RABBITMQ_USER", "jut")
os.environ.setdefault("RABBITMQ_PASS", "jut")
os.environ.setdefault("TASKIQ_API_TOKEN", "jut")
os.environ.setdefault("S3_ACCESS_KEY", _S3_ACCESS_KEY)
os.environ.setdefault("S3_SECRET_KEY", _S3_SECRET_KEY)
# `app.main` reads these at import time to configure CORS, and raises when they
# are missing outside debug mode. Individual tests may still override them.
os.environ.setdefault("DEBUG", "1")
os.environ.setdefault("FRONTEND_URL", "http://frontend.test")
os.environ.setdefault("BACKEND_URL", "http://backend.test")

#: Provider variables as the process received them, captured before the autouse
#: ``env`` fixture overwrites ``SBER_API_KEY``/``SBER_API_SCOPE`` with mock values.
#: Live tests restore these through :func:`real_provider_env`.
REAL_ENV = {name: os.environ.get(name) for name in ("SBER_API_KEY", "SBER_API_SCOPE", "OPENAI_URL", "OPENAI_MODEL")}

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from redis.asyncio import ConnectionPool, Redis  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.community.postgres import PostgresContainer  # noqa: E402
from testcontainers.community.redis import AsyncRedisContainer  # noqa: E402
from testcontainers.core.container import DockerContainer  # noqa: E402

from app.auth import hash_password  # noqa: E402
from app.cache import get_redis_client, get_redis_pool  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.models.auth import AccountRole, User  # noqa: E402
from app.models.chat import (  # noqa: E402
    ChatMessage,
    ChatSession,
    GenerationResult,
    GenerationResultType,
    UserRole,
)

GARAGE_IMAGE = "dxflrs/garage:v2.4.1"
S3_ACCESS_KEY = _S3_ACCESS_KEY
S3_SECRET_KEY = _S3_SECRET_KEY
S3_REGION = "garage"
GARAGE_S3_PORT = 3900

#: Mirrors the buckets created by conf/garage-init.sh in the Compose stack.
S3_BUCKETS = ("avatars", "uploads", "artifacts")

#: Minimal Garage config for the test container. The test container is a single
#: node, so `--single-node` assigns the cluster layout at startup and no separate
#: layout bootstrap is needed. `rpc_public_addr` is deliberately loopback: the CLI
#: runs inside the container, where that is all it needs.
#:
#: Garage refuses to start when /etc/garage.toml is missing, so the file has to
#: exist even though every setting could be passed as a `GARAGE_` environment
#: variable. It is copied in rather than bind-mounted, which is what keeps this
#: fixture free of host bind mounts.
GARAGE_CONFIG = """
metadata_dir = "/var/lib/garage/meta"
data_dir = "/var/lib/garage/data"
db_engine = "lmdb"
replication_factor = 1
rpc_bind_addr = "0.0.0.0:3901"
rpc_public_addr = "127.0.0.1:3901"
rpc_secret = "{rpc_secret}"
[s3_api]
s3_region = "{region}"
api_bind_addr = "0.0.0.0:3900"
root_domain = ".s3.garage.localhost"
[admin]
admin_bind_addr = "0.0.0.0:3903"
"""

#: In-memory mounts for the two directories Garage writes. tmpfs rather than a
#: bind mount out of the pytest temp directory, because on Windows such a mount
#: makes Docker Desktop ask to share the path, and the run fails when that
#: prompt is dismissed. Garage creates its own LMDB directory inside the mount
#: point rather than being handed a pre-made one, so both must be writable.
GARAGE_TMPFS = {
    "/var/lib/garage/meta": "size=64m",
    "/var/lib/garage/data": "size=512m",
}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    """Re-apply the defaults per test, since some tests reload the provider module."""
    monkeypatch.setenv("SBER_API_KEY", "mock")
    monkeypatch.setenv("SBER_API_SCOPE", "PERS")
    monkeypatch.setenv("RABBITMQ_USER", "jut")
    monkeypatch.setenv("RABBITMQ_PASS", "jut")
    monkeypatch.setenv("TASKIQ_API_TOKEN", "jut")
    monkeypatch.setenv("S3_ACCESS_KEY", S3_ACCESS_KEY)
    monkeypatch.setenv("S3_SECRET_KEY", S3_SECRET_KEY)
    # Restored per test because a few tests deliberately change DEBUG/URLs.
    monkeypatch.setenv("DEBUG", "1")
    monkeypatch.setenv("FRONTEND_URL", "http://frontend.test")
    monkeypatch.setenv("BACKEND_URL", "http://backend.test")
    monkeypatch.delenv("MOCK_PROVIDER_MODE", raising=False)


@pytest.fixture(scope="session")
def postgres_container() -> Generator[PostgresContainer]:
    with PostgresContainer("postgres:18-alpine", driver="asyncpg") as postgres:
        yield postgres


@pytest.fixture(scope="session")
def redis_container() -> Generator[AsyncRedisContainer]:
    with AsyncRedisContainer("redis:8") as redis:
        yield redis


@pytest.fixture(scope="session")
def garage_container() -> Generator[DockerContainer]:
    """A real Garage, matching the object storage the Compose stack provides.

    Garage differs from MinIO in two ways that shape this fixture. It needs a
    config file rather than environment-only settings, because it refuses to
    start when `/etc/garage.toml` is absent. And it has no root credentials:
    access keys are created explicitly, so the key is imported before the S3 API
    is usable and buckets have to be created through the CLI, since an S3
    ``CreateBucket`` needs permissions the key does not have yet.
    """
    container = DockerContainer(
        GARAGE_IMAGE,
        # The image has no entrypoint, only a CMD of `/garage server`, so the
        # binary has to be named here.
        command="/garage server --single-node",
        env={"GARAGE_ADMIN_TOKEN": _GARAGE_ADMIN_TOKEN},
        ports=[GARAGE_S3_PORT, 3903],
    )
    # Copied in as bytes rather than mounted from a temporary file, so that no
    # host path is ever shared with the container.
    container.with_copy_into_container(
        GARAGE_CONFIG.format(rpc_secret=_GARAGE_RPC_SECRET, region=S3_REGION).encode(),
        "/etc/garage.toml",
    )
    for path, size in GARAGE_TMPFS.items():
        container.with_tmpfs_mount(path, size)
    container.start()
    try:
        _wait_for_garage(container)
        _bootstrap_garage(container)
        yield container
    finally:
        container.stop()


def _bootstrap_garage(container: DockerContainer) -> None:
    """Import the application key and create both buckets.

    Done once per session rather than per test, because Garage keys and buckets
    live in the metadata volume and outlive any single test.
    """
    _garage_cli(
        container,
        "key",
        "import",
        S3_ACCESS_KEY,
        S3_SECRET_KEY,
        "--yes",
        "-n",
        "digradation-app",
        tolerate=True,
    )
    for bucket in S3_BUCKETS:
        # Tolerated: on a re-run against the same volume the bucket exists.
        _garage_cli(container, "bucket", "create", bucket, tolerate=True)
        _garage_cli(
            container,
            "bucket",
            "allow",
            "--read",
            "--write",
            "--owner",
            bucket,
            "--key",
            S3_ACCESS_KEY,
            tolerate=True,
        )


def _wait_for_garage(container: DockerContainer, attempts: int = 60) -> None:
    """Block until the CLI reports a healthy cluster.

    Garage binds its RPC port before the cluster layout is usable, so a container
    that has merely started will refuse bucket operations. Polling the CLI is
    what the Compose bootstrap does too.
    """
    last_error = ""
    for _ in range(attempts):
        exit_code, output = container.exec("/garage status")
        if exit_code == 0:
            return
        last_error = output.decode(errors="replace")
        time.sleep(1)
    raise RuntimeError(f"Garage never became ready: {last_error}")


def _garage_cli(container: DockerContainer, *args: str, tolerate: bool = False) -> str:
    """Run the ``garage`` CLI inside the container and return its output.

    ``tolerate`` swallows the failure, for steps that legitimately fail on a
    re-run because the resource already exists.
    """
    exit_code, output = container.exec(f"/garage {' '.join(args)}")
    text = output.decode(errors="replace")
    if exit_code != 0 and not tolerate:
        raise RuntimeError(f"garage {' '.join(args)} failed ({exit_code}): {text}")
    return text


@pytest_asyncio.fixture(scope="function")
async def redis_session(monkeypatch, redis_container: AsyncRedisContainer):
    """Point the real ``get_redis_client()`` at the test container.

    Patching the pool factory rather than ``get_redis_client`` matters: task
    modules do ``from app.cache import get_redis_client``, binding the name into
    their own module namespace. Patching ``app.cache.get_redis_client`` would
    leave those bindings untouched, so every worker test would still try to reach
    the ``redis`` hostname. Patching the pool means all call sites work unchanged.
    """
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    pool = ConnectionPool.from_url(f"redis://{host}:{port}/0", decode_responses=True)
    monkeypatch.setattr("app.cache.get_redis_pool", lambda: pool)
    get_redis_pool.cache_clear()
    try:
        yield
    finally:
        await pool.disconnect()
        get_redis_pool.cache_clear()


@pytest_asyncio.fixture(scope="session")
async def engine(postgres_container: PostgresContainer):
    url = postgres_container.get_connection_url()
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)  # type: ignore
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture()
async def redis_client(redis_session: None) -> AsyncGenerator[Redis]:
    """A ready-to-use Redis connection.

    Built from ``get_redis_client()`` rather than the raw pool, so it inherits the
    patched test-container URL. ``get_connection()`` is a coroutine on redis-py 8
    and must be awaited.
    """
    from app.cache import get_redis_client

    async with get_redis_client() as conn:
        yield conn


@pytest_asyncio.fixture()
async def task_db(monkeypatch, engine: AsyncEngine):
    """Bind ``tsq_db()`` inside the workers to the test database.

    Task bodies open their own session via ``app.tasks.conf.broker.tsq_db``, which
    resolves ``app.database.get_session_maker()`` — a separate, ``lru_cache``d
    engine built from the ``POSTGRES_*`` environment. Without this a worker test
    would quietly write to whatever PostgreSQL the environment points at instead
    of the throwaway container.
    """
    from app import database
    from app.tasks.conf import broker as broker_module

    # Grab the lru_cache'd function before replacing the module attribute, so the
    # cache can still be cleared in teardown.
    cached_session_maker = database.get_session_maker
    cached_session_maker.cache_clear()

    maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(database, "get_session_maker", lambda: maker)

    @asynccontextmanager
    async def _tsq_db():
        async with maker() as session:
            yield session

    # The task modules do `from app.tasks.conf.broker import tsq_db`, which binds
    # the name into their own namespace. Patching only the defining module leaves
    # those bindings pointing at the real database, so every consumer is patched.
    monkeypatch.setattr(broker_module, "tsq_db", _tsq_db)
    for module_name in ("app.tasks.api", "app.tasks.files", "app.tasks.users", "app.tasks.program"):
        monkeypatch.setattr(f"{module_name}.tsq_db", _tsq_db)
    yield
    cached_session_maker.cache_clear()


@pytest_asyncio.fixture()
async def db_session(engine: AsyncEngine):
    async with engine.connect() as conn, conn.begin() as transaction:
        session_maker = async_sessionmaker(bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
        session = session_maker()
        yield session
        await session.close()
        await transaction.rollback()


class Rows:
    """Committed rows for worker tests, removed again on teardown.

    ``db_session`` writes inside a transaction that is rolled back, while task
    bodies open their own connection through ``tsq_db``. A worker cannot see
    anything ``db_session`` left uncommitted, so data a worker test needs has to
    be committed on its own connection and cleaned up explicitly.
    """

    def __init__(self, maker: async_sessionmaker):
        self._maker = maker
        self._session_ids: list[uuid.UUID] = []
        self._user_ids: list[int] = []

    @asynccontextmanager
    async def session(self):
        async with self._maker() as db:
            yield db

    async def user(self, username: str = "worker-test") -> User:
        async with self.session() as db:
            row = User(username=username, password_hash=hash_password("password1234"))
            db.add(row)
            await db.commit()
            await db.refresh(row)
        self._user_ids.append(row.id)
        return row

    async def chat_session(self, user: User, name: str = "Новый чат") -> ChatSession:
        async with self.session() as db:
            row = ChatSession(session_id=uuid.uuid4(), user_id=user.id, name=name)
            db.add(row)
            await db.commit()
            await db.refresh(row)
        self._session_ids.append(row.session_id)
        return row

    async def message(
        self,
        session_id: uuid.UUID,
        content: str = "Считай, пожалуйста",
        *,
        role: UserRole = UserRole.USER,
        display_text: str | None = None,
    ) -> ChatMessage:
        async with self.session() as db:
            row = ChatMessage(
                chat_session_id=session_id,
                role=role,
                content=content,
                display_text=display_text,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
        return row

    async def result(
        self,
        session_id: uuid.UUID,
        *,
        type: GenerationResultType = GenerationResultType.SUCCESS,
        content: str = "старое",
        timestamp: datetime | None = None,
    ) -> GenerationResult:
        async with self.session() as db:
            row = GenerationResult(chat_session_id=session_id, type=type, content=content, timestamp=timestamp)
            db.add(row)
            await db.commit()
            await db.refresh(row)
        return row

    async def drop_sessions(self, session_ids: list[uuid.UUID]) -> None:
        async with self.session() as db:
            for session_id in session_ids:
                await db.execute(delete(ChatSession).where(ChatSession.session_id == session_id))
            await db.commit()
        self._session_ids = [sid for sid in self._session_ids if sid not in session_ids]

    async def purge(self) -> None:
        async with self.session() as db:
            await db.execute(delete(ChatSession).where(ChatSession.session_id.in_(self._session_ids)))
            await db.execute(delete(User).where(User.id.in_(self._user_ids)))
            await db.commit()
        self._session_ids.clear()
        self._user_ids.clear()


@pytest_asyncio.fixture()
async def rows(engine: AsyncEngine):
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    helper = Rows(maker)
    try:
        yield helper
    finally:
        await helper.purge()


class RedisKeys:
    """Redis helper that removes every key it touched during teardown.

    Keep it in the signature of every worker test, including ones that never
    call it. Requesting it pulls in ``redis_session``, which is what stands the
    Redis testcontainer up and repoints the cached pool at it; without it the
    worker still dials the Compose ``redis`` hostname and fails with
    ``socket.gaierror``.
    """

    def __init__(self) -> None:
        self._tracked: list[str] = []

    def track(self, key: str) -> str:
        self._tracked.append(key)
        return key

    async def put(self, key: str, value: str = "1", ex: int = 300) -> str:
        self.track(key)
        async with get_redis_client() as redis:
            await redis.set(key, value, ex=ex)
        return key

    async def get(self, key: str):
        async with get_redis_client() as redis:
            return await redis.get(key)

    async def purge(self) -> None:
        if not self._tracked:
            return
        async with get_redis_client() as redis:
            await redis.delete(*self._tracked)
        self._tracked.clear()


@pytest_asyncio.fixture()
async def redis_keys(redis_session) -> RedisKeys:
    helper = RedisKeys()
    try:
        yield helper
    finally:
        await helper.purge()


@pytest_asyncio.fixture()
async def client(db_session: AsyncSession):
    from app.main import app as main_app

    async def override_get_db():
        yield db_session

    main_app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=main_app)
    async with AsyncClient(transport=transport, base_url="http://test/api/v1") as ac:
        yield ac
    main_app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def s3(garage_container: DockerContainer, monkeypatch):
    """A real Garage wired into ``app.storage.storage``, buckets pre-created.

    The key import and bucket creation happen once, in the session-scoped
    ``garage_container`` fixture, because they are cluster-level operations that
    need the CLI: an S3 ``CreateBucket`` requires permissions a freshly imported
    key does not have yet.
    """
    from app.storage import storage

    endpoint = (
        f"http://{garage_container.get_container_host_ip()}" f":{garage_container.get_exposed_port(GARAGE_S3_PORT)}"
    )

    monkeypatch.setattr(storage, "internal_client", lambda: _client(endpoint))
    monkeypatch.setattr(storage, "public_client", lambda: _client(endpoint))
    monkeypatch.setenv("S3_INTERNAL_URL", endpoint)
    monkeypatch.setenv("S3_PUBLIC_URL", endpoint)
    monkeypatch.setenv("S3_REGION", S3_REGION)
    yield


def _client(endpoint: str):
    import aioboto3

    return aioboto3.Session(
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        region_name=S3_REGION,
    ).client("s3", endpoint_url=endpoint, region_name=S3_REGION)


@pytest_asyncio.fixture()
async def chat_session(db_session: AsyncSession, test_user: User):
    """A chat session owned by ``test_user``."""
    from app.models.chat import ChatSession

    session = ChatSession(user_id=test_user.id, name="Новый чат")
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)
    return session


@pytest_asyncio.fixture()
async def factories(db_session: AsyncSession):
    """Small helpers for building chat rows without repeating boilerplate."""

    from app.models.chat import Attachment, ChatMessage, ChatSession, UserRole

    async def make_session(user_id: int, name: str = "Новый чат") -> ChatSession:
        row = ChatSession(user_id=user_id, name=name)
        db_session.add(row)
        await db_session.commit()
        await db_session.refresh(row)
        return row

    async def make_message(
        session_id,
        content: str = "hello",
        role: UserRole = UserRole.USER,
        files_str: str | None = None,
        display_text: str | None = None,
    ) -> ChatMessage:
        row = ChatMessage(
            chat_session_id=session_id,
            role=role,
            content=content,
            files_str=files_str,
            display_text=display_text,
        )
        db_session.add(row)
        await db_session.commit()
        await db_session.refresh(row)
        return row

    async def make_attachment(
        session_id,
        name: str = "drawing.pdf",
        s3_key: str | None = None,
        ready: bool = False,
        chat_message_id: int | None = None,
    ) -> Attachment:
        row = Attachment(
            session_id=session_id,
            name=name,
            s3_key=s3_key or f"attachments/{session_id}/{name}",
            ready=ready,
            chat_message_id=chat_message_id,
        )
        db_session.add(row)
        await db_session.commit()
        await db_session.refresh(row)
        return row

    return type(
        "Factories",
        (),
        {
            "session": staticmethod(make_session),
            "message": staticmethod(make_message),
            "attachment": staticmethod(make_attachment),
        },
    )()


@pytest_asyncio.fixture()
async def s3_put(s3: None):
    """Store bytes in MinIO and return the key."""

    from app.storage import storage

    async def _put(data: bytes, key: str, bucket: str = "uploads", content_type: str = "application/pdf"):
        async with storage.internal_client() as client:
            await client.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)
        return key

    return _put


@pytest_asyncio.fixture()
async def test_user(db_session: AsyncSession):
    user = User(username="test", password_hash=hash_password("password1234"))
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture()
async def test_100_users(db_session: AsyncSession):
    password_hash = hash_password("amongus")
    users = [User(username=f"test-{i}", password_hash=password_hash) for i in range(100)]
    db_session.add_all(users)
    await db_session.commit()
    return (await db_session.execute(select(User).order_by(User.id))).scalars().all()


@pytest_asyncio.fixture()
async def test_admin_user(db_session: AsyncSession):
    user = User(
        username="admin",
        password_hash=hash_password("admin-password"),
        role=AccountRole.SUPERUSER,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture()
async def test_tier_admin_user(db_session: AsyncSession):
    """A user on the admin tier: manages plain users, but not other admins."""
    user = User(
        username="tier-admin",
        password_hash=hash_password("tier-admin-password"),
        role=AccountRole.ADMIN,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture()
async def admin_client(
    client: AsyncClient,
    test_admin_user: User,
):
    response = await client.post(
        "/auth/login",
        json={
            "username": "admin",
            "password": "admin-password",
        },
    )
    assert response.status_code == 200

    return client


@pytest_asyncio.fixture()
async def tier_admin_client(
    client: AsyncClient,
    test_tier_admin_user: User,
):
    response = await client.post(
        "/auth/login",
        json={
            "username": "tier-admin",
            "password": "tier-admin-password",
        },
    )
    assert response.status_code == 200

    return client


@pytest.fixture()
def mock_mode(monkeypatch):
    """Switch the mock provider's behaviour within a test."""

    def _set(mode: str) -> None:
        monkeypatch.setenv("MOCK_PROVIDER_MODE", mode)

    return _set


@pytest.fixture()
def anyio_backend():
    return "asyncio"


@pytest.fixture
def real_provider_env(monkeypatch):
    """Restore the provider variables the autouse ``env`` fixture replaced.

    The autouse fixture pins ``SBER_API_KEY=mock`` so the normal suite never
    reaches a provider. Live tests opt back into whatever the process was started
    with, which is how a real key exported before ``pytest`` survives to the test.
    """
    for name, value in REAL_ENV.items():
        if value is not None:
            monkeypatch.setenv(name, value)
