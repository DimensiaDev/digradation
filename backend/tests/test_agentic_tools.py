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
from structlog.testing import capture_logs

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


def search_call(query: str) -> FunctionCall:
    return FunctionCall(id="call-search", name="search_sources", arguments={"query": query})


def make_draft(**overrides) -> dict:
    """A minimal schema-valid draft the tests can bend."""
    draft = {
        "kind": "general_development",
        "title": "Тестовая программа",
        "summary": {
            "goal": "Научить основам работы с данными",
            "target_audience": "Взрослые слушатели",
            "form": "очно-заочная",
            "issued_document": "сертификат",
        },
        "competencies": [
            {
                "id": "comp-01",
                "statement": "Способен применить знания",
                "knowledge": [{"id": "ro-k-01", "statement": "Знает основы"}],
            }
        ],
        "curriculum": {
            "modules": [
                {
                    "id": "m-01",
                    "title": "Модуль",
                    "topics": [{"id": "t-01.1", "title": "Тема", "hours": {"lecture": 2}}],
                }
            ],
            "schedule": {"days": 1, "hours_per_day": 2},
        },
        "sources": [],
    }
    draft.update(overrides)
    return draft


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


# --------------------------------------------------------------------------------------
# sources: citations are validated against the catalog and the run's provenance
# --------------------------------------------------------------------------------------


async def test_search_sources_reports_an_empty_catalog(tool_context):
    """Materials and search belong to the RAG zone (#11); nothing is found until it lands."""
    result = await execute_tool(tool_context, search_call("образование"))

    assert result["ok"] is True
    assert result["fragments"] == []
    assert "ничего не найдено" in result["note"]
    assert tool_context.known_source_ids == set()


async def test_save_rejects_a_source_id_the_catalog_does_not_know(tool_context):
    draft = make_draft(sources=[{"id": "src-01", "kind": "normative_act", "title": "ФГОС"}])

    result = await execute_tool(tool_context, save_call(draft))

    assert result["ok"] is False
    assert "Неизвестные источники" in result["error"]
    assert "src-01" in result["error"]
    async with tool_context.open_db() as db:
        assert await db.get(ChatProgram, tool_context.session_id) is None


async def test_save_rejects_a_standard_source_id_the_catalog_does_not_know(tool_context):
    draft = make_draft()
    draft["competencies"][0]["standard_source_id"] = "src-01"

    result = await execute_tool(tool_context, save_call(draft))

    assert result["ok"] is False
    assert "Неизвестные источники" in result["error"]
    assert "src-01" in result["error"]


async def test_a_stored_draft_keeps_its_source_ids_known_to_the_run(db_session, chat):
    """A revision run reads the draft back; its citations must stay known to that run."""
    draft = make_draft(sources=[{"id": "src-01", "kind": "normative_act", "title": "ФГОС"}])
    draft["summary"]["basis_source_ids"] = ["src-02"]
    draft["competencies"][0]["standard_source_id"] = "src-03"
    db_session.add(ChatProgram(chat_session_id=chat.session_id, status=ProgramStatus.PENDING_APPROVAL, draft=draft))
    await db_session.commit()

    @asynccontextmanager
    async def open_db():
        yield db_session

    fresh = ToolContext(session_id=chat.session_id, open_db=open_db)
    fetched = await execute_tool(fresh, FunctionCall(name="get_program_draft", arguments={}))

    assert fetched["status"] == ProgramStatus.PENDING_APPROVAL
    assert {"src-01", "src-02", "src-03"} <= fresh.known_source_ids


async def test_the_draft_title_is_not_written_to_the_logs(tool_context):
    """The title is user content; a trace needs the session and the source count."""
    with capture_logs() as logs:
        result = await execute_tool(tool_context, save_call())

    assert result["ok"] is True
    saved = next(entry for entry in logs if entry["event"] == "program_draft_saved")
    assert MOCK_DRAFT_ARGUMENTS["title"] not in str(saved)
