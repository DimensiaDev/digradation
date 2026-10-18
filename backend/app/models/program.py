from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.agentic.models import ProgramStatus
from app.database import Base


class ChatProgram(Base):
    """The programme draft of a chat and its place in the confirmation flow.

    One row per chat, written when the agent saves a draft and updated on every
    revision. The absent row is a state of its own: the agent is still collecting
    the source data, and ``ProgramStatus.DRAFT`` is what the API reports for it.

    Confirmation lives here rather than in the chat history on purpose. A message
    saying «подтверждаю» is text in a transcript; the next stage needs a state it
    can act on, and this row is that state.

    ``status`` reuses the document's own ``ProgramStatus`` so the draft payload,
    the row and the API speak one vocabulary instead of three.
    """

    __tablename__ = "chat_programs"

    chat_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_sessions.session_id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[ProgramStatus] = mapped_column(Enum(ProgramStatus), nullable=False)
    draft: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
