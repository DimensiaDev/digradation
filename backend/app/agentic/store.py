"""Persistence of :class:`~app.agentic.state.ProgramState`.

PostgreSQL is the source of truth: a stage task reads the state from
``chat_sessions.program_state``, writes every block there, and only then mirrors
the same JSON to Redis with a one-hour TTL. The mirror is for a live worker and
for operators; a process that dies between the commit and the Redis write loses
nothing, because the next load reads PostgreSQL and repopulates the mirror.

The store takes its session factory as a callable rather than importing the
worker's ``tsq_db``. That keeps the agent package free of a dependency on
``app.tasks`` and lets tests bind it to the same database the worker uses.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime

import structlog.stdlib
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agentic.state import ProgramState
from app.cache import PROGRAM_STATE_TTL, get_program_state_key, get_redis_client
from app.models.chat import ChatSession

log = structlog.stdlib.get_logger(__name__)

OpenDb = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class ProgramStateStore:
    """Read and write the pipeline state, DB first, Redis second."""

    def __init__(self, open_db: OpenDb):
        self._open_db = open_db

    async def load(self, session_id: uuid.UUID) -> ProgramState | None:
        """Load the canonical state and refresh the Redis mirror.

        A missing row, a chat with no programme, and a payload that no longer
        matches the current schema all read as "no state": the caller then
        treats the session as an ordinary one instead of crashing a worker on
        data it cannot use.
        """
        async with self._open_db() as db:
            row = await db.get(ChatSession, session_id)
            raw = row.program_state if row is not None else None
        if raw is None:
            return None
        try:
            state = ProgramState.model_validate(raw)
        except ValidationError as error:
            log.error("program_state_invalid", chat_session_id=str(session_id), exc=error)
            return None
        await self._cache_set(state)
        return state

    async def save(self, state: ProgramState, *, expected_run_id: str | None = None) -> ProgramState | None:
        """Commit a state, refusing to overwrite a newer run or version.

        The row is locked for the short transaction, so two workers that somehow
        both reach the same stage cannot interleave their writes. ``expected_run_id``
        is the stronger guard: a run that has been superseded must not write at
        all, even if its version looks newer.
        """
        saved = state.model_copy(update={"meta": state.meta.model_copy(update={"updated_at": datetime.now(UTC)})})
        async with self._open_db() as db:
            row = await db.scalar(
                select(ChatSession).where(ChatSession.session_id == state.meta.session_id).with_for_update()
            )
            if row is None:
                log.warning("program_state_session_missing", chat_session_id=str(state.meta.session_id))
                return None
            current = None
            if row.program_state is not None:
                try:
                    current = ProgramState.model_validate(row.program_state)
                except ValidationError:
                    current = None
            if expected_run_id is not None and (current is None or current.meta.run_id != expected_run_id):
                log.warning(
                    "program_state_stale_run",
                    chat_session_id=str(state.meta.session_id),
                    expected_run_id=expected_run_id,
                )
                return None
            if (
                current is not None
                and current.meta.run_id == saved.meta.run_id
                and current.meta.version > saved.meta.version
            ):
                log.warning(
                    "program_state_stale_version",
                    chat_session_id=str(state.meta.session_id),
                    current=current.meta.version,
                    incoming=saved.meta.version,
                )
                return current
            row.program_state = saved.model_dump(mode="json")
            await db.commit()

        await self._cache_set(saved)
        return saved

    async def mirror(self, state: ProgramState) -> None:
        """Refresh the Redis mirror of an already-committed state."""
        await self._cache_set(state)

    async def _cache_set(self, state: ProgramState) -> None:
        """Mirror the state to Redis; a cache failure must not fail the task."""
        try:
            async with get_redis_client() as redis:
                await redis.set(
                    get_program_state_key(state.meta.session_id),
                    state.model_dump_json(),
                    ex=PROGRAM_STATE_TTL,
                )
        except Exception as error:
            log.warning(
                "program_state_cache_failed",
                chat_session_id=str(state.meta.session_id),
                exc=error,
            )
