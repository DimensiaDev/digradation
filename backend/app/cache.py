import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from functools import cache

from redis.asyncio import ConnectionPool, Redis

from app.agentic.models import Stage

#: How long the Redis mirror of a programme state stays readable; PostgreSQL keeps
#: the canonical copy and repopulates the mirror on the next load.
PROGRAM_STATE_TTL = 3600

#: How long one stage may hold its lock. This, not ``STALLED_AFTER``, sets the
#: recovery latency of a killed worker: the cron refuses a locked stage, so the
#: run resumes only after the lock expires.
PROGRAM_STAGE_LOCK_TTL = 900


@cache
def get_redis_pool() -> ConnectionPool:
    return ConnectionPool.from_url("redis://redis:6379/0", max_connections=20, decode_responses=True)


@asynccontextmanager
async def get_redis_client() -> AsyncGenerator[Redis]:
    async with Redis(connection_pool=get_redis_pool()) as conn:
        yield conn


def get_generation_key(user_uuid: uuid.UUID) -> str:
    return f"chat:generation:{user_uuid}"


def get_creation_key(user_uuid: uuid.UUID) -> str:
    return f"chat:creation:{user_uuid}"


def get_deletion_key(session_id: uuid.UUID) -> str:
    return f"chat:deletion:{session_id}"


def get_ratelimit_key(key: str, user_id: int) -> str:
    return f"ratelimit:{key}:{user_id}"


def get_avatar_waiting_key(user_uuid: uuid.UUID) -> str:
    return f"avatar:waiting:{user_uuid}"


def get_avatar_url_key(user_uuid: uuid.UUID) -> str:
    return f"avatar:url:{user_uuid}"


def get_attachment_status_key(attachment_id: int) -> str:
    return f"attachment:{attachment_id}:status"


def get_attachment_ownership_key(session_id: uuid.UUID, attachment_id: int) -> str:
    return f"attachment:ownership:{session_id}:{attachment_id}"


def get_attachment_url_key(attachment_id: int) -> str:
    return f"attachment:url:{attachment_id}"


def get_program_state_key(session_id: uuid.UUID) -> str:
    return f"program:state:{session_id}"


def get_program_stage_lock_key(session_id: uuid.UUID, stage: Stage) -> str:
    return f"program:stage:{session_id}:{stage.value}"
