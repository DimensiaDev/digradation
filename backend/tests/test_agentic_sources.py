"""Tests for the curated catalog and the RAG search seam."""

from __future__ import annotations

import pytest
from edu_rag.models import SearchHit

from app.agentic import sources
from app.agentic.models import SourceKind
from app.agentic.sources import RagUnavailable, get_source, search


@pytest.fixture(autouse=True)
def _clear_retriever_cache():
    sources.clear_retriever_cache()
    yield
    sources.clear_retriever_cache()


def test_the_catalog_has_the_curated_sources() -> None:
    source = get_source("src-01")
    assert source is not None
    assert source.id == "src-01"
    assert source.kind is SourceKind.NORMATIVE_ACT
    assert "начального общего" in source.title


def test_source_ids_are_canonicalised() -> None:
    source = get_source("src-1")
    assert source is not None
    assert source.id == "src-01"


def test_an_unknown_source_is_refused() -> None:
    assert get_source("src-99") is None
    assert get_source("not-a-source") is None


def test_rag_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_ENABLED", "false")
    assert sources.get_retriever() is None


async def test_search_finds_nothing_while_rag_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_ENABLED", "false")
    sources.clear_retriever_cache()
    assert await search("профстандарт аналитика") == []


async def test_a_blank_query_never_reaches_the_retriever(monkeypatch: pytest.MonkeyPatch) -> None:
    class Boom:
        async def retrieve(self, *_args, **_kwargs):
            raise AssertionError("blank query reached the retriever")

    monkeypatch.setattr(sources, "get_retriever", lambda: Boom())
    assert await search("   ") == []


async def test_search_maps_hits_to_catalog_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeRetriever:
        async def retrieve(self, query: str, *, kinds=None, limit: int = 5):
            assert query == "начальное общее образование"
            assert kinds == {"normative_act"}
            assert limit == 3
            return [
                SearchHit(
                    doc_id="abc123",
                    ordinal=4,
                    kind="text",
                    source_id="src-1",
                    source_kind="normative_act",
                    section_title="Общие положения",
                    page_number=7,
                    content="Фрагмент стандарта",
                    score=0.91,
                )
            ]

    monkeypatch.setattr(sources, "get_retriever", lambda: FakeRetriever())
    fragments = await search(
        "начальное общее образование",
        kinds=[SourceKind.NORMATIVE_ACT],
        limit=3,
    )

    assert len(fragments) == 1
    fragment = fragments[0]
    assert fragment.fragment_id == "abc123:text:4"
    assert fragment.source.id == "src-01"
    assert fragment.section == "Общие положения"
    assert fragment.page == 7
    assert fragment.trust == 1.0
    assert fragment.score == 0.91


async def test_a_broken_catalog_is_reported_as_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeRetriever:
        async def retrieve(self, *_args, **_kwargs):
            return [
                SearchHit(
                    doc_id="abc123",
                    ordinal=1,
                    kind="text",
                    source_id="src-01",
                    source_kind="normative_act",
                    section_title=None,
                    page_number=None,
                    content="Фрагмент",
                    score=0.9,
                )
            ]

    monkeypatch.setattr(sources, "get_retriever", lambda: FakeRetriever())

    def broken_catalog():
        raise ValueError("catalog is missing")

    monkeypatch.setattr(sources, "catalog_by_id", broken_catalog)
    with pytest.raises(RagUnavailable, match="catalog is missing"):
        await search("anything")


async def test_a_retriever_failure_is_reported_as_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenRetriever:
        async def retrieve(self, *_args, **_kwargs):
            raise RuntimeError("embedding endpoint is down")

    monkeypatch.setattr(sources, "get_retriever", lambda: BrokenRetriever())
    with pytest.raises(RagUnavailable, match="embedding endpoint is down"):
        await search("anything")
