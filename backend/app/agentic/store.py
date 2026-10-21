"""Persistence of the programme run state.

PostgreSQL is canonical: ``chat_sessions.program_state`` holds the whole envelope,
so a worker that dies leaves every committed block behind. Redis holds a copy
with a TTL, and the copy is what the state endpoint reads: it is written on
every save, so it is as fresh as the row it mirrors, and a client that polls
the programme's state does not touch PostgreSQL at all.

The one gap is a mirror write that fails while Redis is still up: the endpoint
then serves the last state that was mirrored, until the next successful write
or the TTL runs out. The response carries the state's ``updated_at`` and
``version``, so a client can see that it is looking at an older state.

Concurrency is guarded twice: the row lock serialises two writers, and the
version on the envelope refuses one derived from an older copy. The per-stage
lock in Redis keeps a slow worker from overlapping a recovery run.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import structlog.stdlib
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agentic.errors import MissingChat, StaleState
from app.agentic.state import ProgramState
from app.cache import PROGRAM_STATE_TTL, get_program_state_key, get_redis_client
from app.database import db_session
from app.models.chat import ChatSession

log = structlog.stdlib.get_logger(__name__)


def parse(raw: Any) -> ProgramState | None:
    """Read a JSONB value, or ``None`` when it is absent or unusable."""
    if raw is None:
        return None
    try:
        return ProgramState.model_validate(raw)
    except ValidationError as error:
        log.error("program_state_unreadable", error=str(error))
        return None


def dump(state: ProgramState) -> dict[str, Any]:
    return state.model_dump(mode="json")


async def mirror(state: ProgramState) -> None:
    """Refresh the Redis copy; a mirror that cannot be written is not fatal."""
    try:
        async with get_redis_client() as redis:
            await redis.set(
                get_program_state_key(state.session_id),
                json.dumps(dump(state), ensure_ascii=False),
                ex=PROGRAM_STATE_TTL,
            )
    except Exception as error:
        log.warning("program_state_mirror_failed", session_id=str(state.session_id), error=str(error))


@asynccontextmanager
async def locked(session_id: uuid.UUID) -> AsyncGenerator[tuple[AsyncSession, ChatSession]]:
    """Lock the chat row for the duration of the block, committing on a clean exit."""
    async with db_session() as db, db.begin():
        row = await db.scalar(select(ChatSession).where(ChatSession.session_id == session_id).with_for_update())
        if row is None:
            raise MissingChat(str(session_id))
        yield db, row


def writable(current: ProgramState | None, incoming: ProgramState, expected_run_id: uuid.UUID | None) -> bool:
    """Whether ``incoming`` may replace ``current``.

    Another ``run_id`` is a restart and is allowed; the same run may only move
    forward. A caller that names the run it meant to continue is refused when the
    row already holds another one.
    """
    if current is None:
        return True
    if expected_run_id is not None and current.run_id != expected_run_id:
        return False
    return current.run_id != incoming.run_id or current.version < incoming.version


async def load(session_id: uuid.UUID) -> ProgramState | None:
    """Read the state from PostgreSQL, refreshing the Redis copy on the way."""
    async with db_session() as db:
        raw = await db.scalar(select(ChatSession.program_state).where(ChatSession.session_id == session_id))
    state = parse(raw)
    if state is not None:
        await mirror(state)
    return state


async def load_mirror(session_id: uuid.UUID) -> ProgramState | None:
    """Read the state from the Redis mirror, or ``None`` when it is not there.

    The mirror is written on every save, so it is as fresh as the row it
    mirrors; a read that only has to tell a client where the programme stands
    does not need a database round trip.
    """
    try:
        async with get_redis_client() as redis:
            raw = await redis.get(get_program_state_key(session_id))
    except Exception as error:
        log.warning("program_state_mirror_unreadable", session_id=str(session_id), error=str(error))
        return None
    if raw is None:
        return None
    try:
        return parse(json.loads(raw))
    except ValueError as error:
        log.error("program_state_mirror_unreadable", session_id=str(session_id), error=str(error))
        return None


async def load_cached(db: AsyncSession, session_id: uuid.UUID) -> ProgramState | None:
    """The state from the Redis mirror, or from the row when the mirror missed.

    The mirror is written on every save, so a client that polls the state does
    not need a database round trip. A miss falls back to the row, and the row
    is mirrored again on the way out.

    The row is read through the caller's session, because a route reads inside
    its own transaction and a session of its own would not see it.
    """
    state = await load_mirror(session_id)
    if state is not None:
        return state
    raw = await db.scalar(select(ChatSession.program_state).where(ChatSession.session_id == session_id))
    state = parse(raw)
    if state is not None:
        await mirror(state)
    return state


async def save(state: ProgramState, *, expected_run_id: uuid.UUID | None = None) -> ProgramState:
    """Write the state, refusing one that is stale against the row."""
    async with locked(state.session_id) as (_, row):
        if not writable(parse(row.program_state), state, expected_run_id):
            raise StaleState(f"run {state.run_id} at version {state.version}")
        row.program_state = dump(state)
    await mirror(state)
    return state
