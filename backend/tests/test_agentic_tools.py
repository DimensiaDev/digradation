"""Tests for the agent tool handlers in ``app.agentic.tools``.

What the handlers do to the database is real here: ``open_db`` hands them the
test session, whose commits are savepoints inside the fixture's transaction, so
the stored draft can be read back within the same test. The provider is not
involved — the handlers are called directly through :func:`execute_tool`.
"""

import uuid
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio

from app.agentic.models import ProgramStatus
from app.agentic.tools import ToolContext, execute_tool
from app.models.chat import DEFAULT_CHAT_NAME, ChatSession
from app.models.program import ChatProgram
from app.providers.mock import MOCK_DRAFT_ARGUMENTS
from app.providers.models import FunctionCall


@pytest_asyncio.fixture()
async def chat(db_session, test_user) -> ChatSession:
    """A chat session owned by ``test_user``; ``session_id`` has no default."""
    row = ChatSession(session_id=uuid.uuid4(), user_id=test_user.id, name=DEFAULT_CHAT_NAME)
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row


@pytest_asyncio.fixture()
async def tool_context(db_session, chat) -> ToolContext:
    """A handler context bound to the fixture session and chat."""

    @asynccontextmanager
    async def open_db():
        yield db_session

    return ToolContext(session_id=chat.session_id, open_db=open_db)


def save_call(arguments: dict | None = None) -> FunctionCall:
    return FunctionCall(
        id="call-1",
        name="save_program_draft",
        arguments=arguments if arguments is not None else dict(MOCK_DRAFT_ARGUMENTS),
    )


async def test_save_program_draft_stores_the_draft_and_names_the_chat(tool_context, db_session, chat):
    result = await execute_tool(tool_context, save_call())

    assert result["ok"] is True
    assert result["status"] == "pending_approval"
    assert chat.name == MOCK_DRAFT_ARGUMENTS["title"]
    assert tool_context.renamed_to == MOCK_DRAFT_ARGUMENTS["title"]
    row = await db_session.get(ChatProgram, chat.session_id)
    assert row is not None
    assert row.draft["title"] == MOCK_DRAFT_ARGUMENTS["title"]


async def test_a_failed_commit_leaves_no_rename_behind(tool_context, db_session):
    """The rename in the context must not outlive a draft that was never stored."""

    async def boom():
        raise RuntimeError("commit failed")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(db_session, "commit", boom)
        result = await execute_tool(tool_context, save_call())

    assert result["ok"] is False
    assert tool_context.renamed_to is None


async def test_save_keeps_the_name_the_methodist_chose(tool_context, db_session, chat):
    chat.name = "Моя программа"

    result = await execute_tool(tool_context, save_call())

    assert result["ok"] is True
    assert result["status"] == "pending_approval"
    assert chat.name == "Моя программа"
    assert tool_context.renamed_to is None


async def test_save_program_draft_refuses_to_overwrite_an_approved_draft(tool_context, db_session, chat):
    db_session.add(
        ChatProgram(
            chat_session_id=chat.session_id,
            status=ProgramStatus.APPROVED,
            draft=dict(MOCK_DRAFT_ARGUMENTS),
        )
    )
    await db_session.commit()

    result = await execute_tool(tool_context, save_call())

    assert result["ok"] is False
    assert "утверждён" in result["error"]
    assert tool_context.renamed_to is None
    assert chat.name == DEFAULT_CHAT_NAME
