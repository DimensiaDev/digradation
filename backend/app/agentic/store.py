"""Persistence of the programme run state.

PostgreSQL is canonical: ``chat_sessions.program_state`` holds the whole envelope,
so a worker that dies leaves every block it committed behind. Redis holds a copy
with :data:`~app.cache.PROGRAM_STATE_TTL` so an operator can watch a stage work
without querying the database, and the copy is refreshed on every load and save;
nothing reads it as a source of truth.

The store does not import the task layer. It asks ``app.database`` for the session
maker on each call, which is also what lets the test suite point it at its own
container: the fixture replaces the attribute, and a module-level import would
have bound the original function before that.

Concurrency is guarded twice. The PostgreSQL row lock serialises two writers that
save at the same moment, and the version on the envelope refuses one that was
derived from an older copy. Neither stops a worker that loaded the state minutes
ago; the per-stage lock in Redis is what keeps a slow worker from overlapping a
recovery run, and the version check is the second line of defence.
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

from app.agentic.state import ProgramState
from app.cache import PROGRAM_STATE_TTL, get_program_state_key, get_redis_client
from app.models.chat import ChatSession

log = structlog.stdlib.get_logger(__name__)


class MissingChat(RuntimeError):
    """The chat the state belongs to is gone."""


class StaleState(RuntimeError):
    """The row holds another run, or a newer version of this one."""


@asynccontextmanager
async def _session() -> AsyncGenerator[AsyncSession]:
    # Imported per call: see the module docstring.
    from app.database import get_session_maker

    async with get_session_maker()() as db:
        yield db


def parse(raw: Any) -> ProgramState | None:
    """Read a JSONB value, or ``None`` when it is absent or unusable.

    An unreadable value is logged and treated as "this chat has no programme".
    The row keeps it until the next run overwrites it, so the data is not lost by
    the read itself.
    """
    if raw is None:
        return None
    try:
        return ProgramState.model_validate(raw)
    except ValidationError as error:
        log.error("program_state_unreadable", error=str(error))
        return None


def dump(state: ProgramState) -> dict[str, Any]:
    """The JSONB value to store for a state."""
    return state.model_dump(mode="json")


async def mirror(state: ProgramState) -> None:
    """Refresh the Redis copy; a mirror that cannot be written is not fatal.

    The database already committed, and the next load repopulates the mirror, so
    a Redis outage must not fail the stage that just made progress.
    """
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
    """Lock the chat row for the duration of the block.

    Two workers told to run the same stage queue up on the lock, and the second
    one reads what the first wrote rather than both writing a block. The block
    commits on a clean exit, so a caller can write the message a state describes
    in the same transaction as the state itself.
    """
    async with _session() as db:
        row = await db.scalar(select(ChatSession).where(ChatSession.session_id == session_id).with_for_update())
        if row is None:
            raise MissingChat(str(session_id))
        yield db, row
        await db.commit()


def writable(current: ProgramState | None, incoming: ProgramState, expected_run_id: uuid.UUID | None) -> bool:
    """Whether ``incoming`` may replace ``current``.

    A different ``run_id`` is a restart, which is allowed: a user who asks again
    after a failed run is not competing with anything. The same run may only move
    forward, and a caller that names the run it meant to continue is refused when
    the row has already moved to another one.
    """
    if current is None:
        return True
    if expected_run_id is not None and current.run_id != expected_run_id:
        return False
    return current.run_id != incoming.run_id or current.version < incoming.version


async def load(session_id: uuid.UUID) -> ProgramState | None:
    """Read the state from PostgreSQL, refreshing the Redis copy on the way."""
    async with _session() as db:
        raw = await db.scalar(select(ChatSession.program_state).where(ChatSession.session_id == session_id))
    state = parse(raw)
    if state is not None:
        await mirror(state)
    return state


async def save(state: ProgramState, *, expected_run_id: uuid.UUID | None = None) -> ProgramState:
    """Write the state, refusing one that is stale against the row.

    Raises :class:`StaleState` when the row holds a newer version of this run, or
    another run than the caller named, and :class:`MissingChat` when the chat was
    deleted while the stage worked.
    """
    async with locked(state.session_id) as (_, row):
        if not writable(parse(row.program_state), state, expected_run_id):
            raise StaleState(f"run {state.run_id} at version {state.version}")
        row.program_state = dump(state)
    await mirror(state)
    return state
