"""How a chat gets its name.

One rule, one place: a programme names itself, and a chat without a programme
takes its name from the first thing the methodist said. The name is written at
most twice — once from the first message, and once from the programme title,
which supersedes it — and never touches a name the chat already carries.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import DEFAULT_CHAT_NAME, MAX_CHAT_NAME_LENGTH, ChatMessage, UserRole


def _trimmed(name: str) -> str:
    """A name that fits the column."""
    trimmed = name.strip()
    if len(trimmed) <= MAX_CHAT_NAME_LENGTH:
        return trimmed
    return trimmed[: MAX_CHAT_NAME_LENGTH - 3].rstrip() + "..."


def resolve_chat_name(
    current: str,
    *,
    programme_title: str | None = None,
    first_user_message: str | None = None,
) -> str | None:
    """The name a chat should carry, or ``None`` to leave it as it is.

    A programme's own title is the best name a chat can have, so it wins over
    the first message. A chat that already carries a name of its own keeps it.
    """
    derived = _trimmed(first_user_message) if first_user_message and first_user_message.strip() else None
    titled = _trimmed(programme_title) if programme_title and programme_title.strip() else None
    if titled and current in (DEFAULT_CHAT_NAME, derived):
        return titled
    if derived and current == DEFAULT_CHAT_NAME:
        return derived
    return None


async def first_user_message(db: AsyncSession, session_id: uuid.UUID) -> str | None:
    """The first thing the methodist said in this chat, if anything."""
    return await db.scalar(
        select(ChatMessage.content)
        .where(ChatMessage.chat_session_id == session_id, ChatMessage.role == UserRole.USER)
        .order_by(ChatMessage.id)
        .limit(1)
    )
