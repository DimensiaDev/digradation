"""Tests for the PostgreSQL/Redis state store.

The database is canonical and Redis is a mirror with a TTL; these tests assert
both sides of that relationship, plus the two guards that keep a superseded run
or a stale worker from overwriting newer state.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from app.agentic.models import Stage
from app.agentic.state import mark_stage_started, new_state
from app.agentic.store import ProgramStateStore
from app.cache import PROGRAM_STATE_TTL, get_program_state_key, get_redis_client
from app.models.chat import ChatSession
from app.tasks import program as program_module


def store() -> ProgramStateStore:
    return ProgramStateStore(program_module.tsq_db)


async def test_save_commits_the_state_and_mirrors_it_to_redis(task_db, rows, redis_keys):
    user = await rows.user()
    session = await rows.chat_session(user)
    key = redis_keys.track(get_program_state_key(session.session_id))
    state = new_state(session.session_id, requested_hours=2, requirements="Тема")

    saved = await store().save(state)

    assert saved is not None
    assert saved.meta.run_id == state.meta.run_id
    async with rows.session() as db:
        row = await db.get(ChatSession, session.session_id)
        assert row is not None
        assert row.program_state is not None
        assert row.program_state["meta"]["run_id"] == state.meta.run_id
    mirrored = await redis_keys.get(key)
    assert mirrored is not None
    assert state.meta.run_id in mirrored
    async with get_redis_client() as redis:
        ttl = await redis.ttl(key)
    assert 0 < ttl <= PROGRAM_STATE_TTL


async def test_load_prefers_the_committed_row_and_refreshes_the_mirror(task_db, rows, redis_keys):
    user = await rows.user()
    session = await rows.chat_session(user)
    key = redis_keys.track(get_program_state_key(session.session_id))
    state = new_state(session.session_id, requested_hours=2, requirements="Тема")
    await store().save(state)

    loaded = await store().load(session.session_id)

    assert loaded is not None
    assert loaded.meta.run_id == state.meta.run_id
    assert await redis_keys.get(key) is not None


async def test_load_returns_none_for_a_missing_or_empty_programme(task_db, rows, redis_keys):
    user = await rows.user()
    session = await rows.chat_session(user)

    assert await store().load(session.session_id) is None
    assert await store().load(uuid.uuid4()) is None


async def test_load_treats_a_payload_that_no_longer_validates_as_no_state(task_db, rows, redis_keys):
    user = await rows.user()
    session = await rows.chat_session(user)
    async with rows.session() as db:
        row = await db.get(ChatSession, session.session_id)
        row.program_state = {"unexpected": "shape"}
        await db.commit()

    assert await store().load(session.session_id) is None


async def test_save_refuses_a_session_that_does_not_exist(task_db, rows, redis_keys):
    state = new_state(uuid.uuid4(), requested_hours=2, requirements="Тема")

    assert await store().save(state) is None


async def test_save_refuses_a_superseded_run(task_db, rows, redis_keys):
    user = await rows.user()
    session = await rows.chat_session(user)
    state = new_state(session.session_id, requested_hours=2, requirements="Тема", run_id="run-1")
    await store().save(state)

    assert await store().save(state, expected_run_id="run-2") is None


async def test_save_refuses_an_older_version_of_the_same_run(task_db, rows, redis_keys):
    user = await rows.user()
    session = await rows.chat_session(user)
    state = new_state(session.session_id, requested_hours=2, requirements="Тема")
    await store().save(state)
    newer = mark_stage_started(state, Stage.COMPETENCES)
    await store().save(newer)

    result = await store().save(state)

    assert result is not None
    assert result.meta.version == newer.meta.version


async def test_a_new_run_may_start_at_version_zero(task_db, rows, redis_keys):
    user = await rows.user()
    session = await rows.chat_session(user)
    old = new_state(session.session_id, requested_hours=2, requirements="Старая", run_id="old")
    await store().save(old)
    advanced = mark_stage_started(old, Stage.COMPETENCES)
    await store().save(advanced)
    new = new_state(session.session_id, requested_hours=4, requirements="Новая", run_id="new")

    saved = await store().save(new)

    assert saved is not None
    loaded = await store().load(session.session_id)
    assert loaded is not None
    assert loaded.meta.run_id == "new"
    assert loaded.requested_hours == 4


async def test_redis_failure_does_not_fail_the_save(task_db, rows, redis_keys, monkeypatch):
    user = await rows.user()
    session = await rows.chat_session(user)
    state = new_state(session.session_id, requested_hours=2, requirements="Тема")

    @asynccontextmanager
    async def broken_redis():
        raise RuntimeError("redis is down")
        yield  # pragma: no cover

    monkeypatch.setattr("app.agentic.store.get_redis_client", broken_redis)

    saved = await store().save(state)

    assert saved is not None
    async with rows.session() as db:
        row = await db.get(ChatSession, session.session_id)
        assert row is not None
        assert row.program_state is not None


async def test_mirror_writes_an_already_committed_state(task_db, rows, redis_keys):
    user = await rows.user()
    session = await rows.chat_session(user)
    key = redis_keys.track(get_program_state_key(session.session_id))
    state = new_state(session.session_id, requested_hours=2, requirements="Тема")
    async with rows.session() as db:
        row = await db.get(ChatSession, session.session_id)
        row.program_state = state.model_dump(mode="json")
        await db.commit()

    await store().mirror(state)

    assert await redis_keys.get(key) is not None
