"""Tests for ``app.agentic.store``: PostgreSQL is canonical, Redis is a mirror.

Every test uses the real database and the real Redis container, because the two
things worth proving here are the row lock's refusals and the mirror's TTL — both
of which a fake would implement, not verify.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.agentic import store
from app.agentic.errors import MissingChat, StaleState
from app.agentic.state import ProgramState, new_state
from app.auth import hash_password
from app.cache import PROGRAM_STATE_TTL, get_program_state_key
from app.models.auth import User
from app.models.chat import ChatSession


@pytest_asyncio.fixture()
async def chat(engine: AsyncEngine) -> AsyncGenerator[uuid.UUID]:
    """A committed chat, because the store opens its own connection."""
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with maker() as db:
        user = User(username="store-test", password_hash=hash_password("password1234"))
        db.add(user)
        await db.commit()
        await db.refresh(user)
        row = ChatSession(session_id=uuid.uuid4(), user_id=user.id)
        db.add(row)
        await db.commit()
        session_id, user_id = row.session_id, user.id
    try:
        yield session_id
    finally:
        async with maker() as db:
            await db.execute(delete(ChatSession).where(ChatSession.session_id == session_id))
            await db.execute(delete(User).where(User.id == user_id))
            await db.commit()


@pytest.fixture()
def state(chat: uuid.UUID) -> ProgramState:
    return new_state(chat, requested_hours=72, requirements="Курс для аналитиков")


async def write_raw(engine: AsyncEngine, session_id: uuid.UUID, value: dict) -> None:
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with maker() as db:
        row = await db.get(ChatSession, session_id)
        row.program_state = value
        await db.commit()


async def read_raw(engine: AsyncEngine, session_id: uuid.UUID) -> dict:
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with maker() as db:
        return await db.scalar(select(ChatSession.program_state).where(ChatSession.session_id == session_id))


async def test_a_saved_state_is_read_back_as_itself(state: ProgramState, task_db: None, redis_client: Redis) -> None:
    await store.save(state)

    assert await store.load(state.session_id) == state


async def test_the_mirror_expires_after_the_configured_hour(
    state: ProgramState, task_db: None, redis_client: Redis
) -> None:
    await store.save(state)

    key = get_program_state_key(state.session_id)
    ttl = await redis_client.ttl(key)
    mirrored = json.loads(await redis_client.get(key))

    assert 0 < ttl <= PROGRAM_STATE_TTL
    assert mirrored["run_id"] == str(state.run_id)


async def test_a_load_repopulates_a_dropped_mirror(state: ProgramState, task_db: None, redis_client: Redis) -> None:
    await store.save(state)
    key = get_program_state_key(state.session_id)
    await redis_client.delete(key)

    await store.load(state.session_id)

    assert await redis_client.exists(key) == 1


async def test_a_missing_row_is_not_a_programme(state: ProgramState, task_db: None, redis_client: Redis) -> None:
    assert await store.load(uuid.uuid4()) is None


async def test_an_unreadable_value_reads_as_no_programme(
    state: ProgramState, engine: AsyncEngine, task_db: None, redis_client: Redis
) -> None:
    await write_raw(engine, state.session_id, {"run_id": "not-a-uuid"})

    assert await store.load(state.session_id) is None


async def test_a_newer_version_replaces_the_row(state: ProgramState, task_db: None, redis_client: Redis) -> None:
    saved = await store.save(state)
    newer = saved.started(saved.current_stage)

    await store.save(newer)

    assert await store.load(state.session_id) == newer


async def test_a_state_derived_from_an_older_copy_is_refused(
    state: ProgramState, task_db: None, redis_client: Redis
) -> None:
    saved = await store.save(state)
    await store.save(saved.started(saved.current_stage))

    with pytest.raises(StaleState):
        await store.save(saved.approved())


async def test_an_older_version_is_refused(state: ProgramState, task_db: None, redis_client: Redis) -> None:
    saved = await store.save(state)
    await store.save(saved.enqueued(saved.current_stage).enqueued(saved.current_stage))

    with pytest.raises(StaleState):
        await store.save(saved)


async def test_a_restart_replaces_a_finished_run(state: ProgramState, task_db: None, redis_client: Redis) -> None:
    finished = (await store.save(state)).approved()
    await store.save(finished)

    restart = new_state(state.session_id, requested_hours=16, requirements=None)
    saved = await store.save(restart)

    assert saved.run_id != finished.run_id
    assert await store.load(state.session_id) == restart


async def test_naming_another_run_is_refused(state: ProgramState, task_db: None, redis_client: Redis) -> None:
    await store.save(state)

    with pytest.raises(StaleState):
        await store.save(state.approved(), expected_run_id=uuid.uuid4())


async def test_a_deleted_chat_is_reported(task_db: None, redis_client: Redis) -> None:
    orphan = new_state(uuid.uuid4(), requested_hours=None, requirements=None)

    with pytest.raises(MissingChat):
        await store.save(orphan)


async def test_a_redis_outage_does_not_lose_the_save(
    state: ProgramState, task_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable():
        raise ConnectionError("redis is down")

    monkeypatch.setattr("app.agentic.store.get_redis_client", unavailable)

    await store.save(state)

    assert await store.load(state.session_id) == state


async def test_the_row_holds_the_document_not_a_python_repr(
    state: ProgramState, engine: AsyncEngine, task_db: None, redis_client: Redis
) -> None:
    await store.save(state)

    raw = await read_raw(engine, state.session_id)

    assert isinstance(raw, dict)
    assert raw["competencies"] == []
    assert raw["attempts"] == {"competences": 1}
