"""Tests for the curated catalog and the RAG search seam."""

from __future__ import annotations

from pathlib import Path

import pytest
from edu_rag.models import SearchHit

from app.agentic import sources
from app.agentic.models import SourceKind
from app.agentic.sources import (
    RagUnavailable,
    corpus_entries,
    get_source,
    search,
)


@pytest.fixture(autouse=True)
def _clear_retriever_cache():
    sources.clear_retriever_cache()
    sources.clear_cache()
    yield
    sources.clear_retriever_cache()
    sources.clear_cache()


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


async def test_the_corpus_answers_while_the_index_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without an index the corpus still answers, so the agent finds its documents."""
    monkeypatch.setenv("RAG_ENABLED", "false")
    sources.clear_retriever_cache()

    fragments = await search("профессиональный стандарт программист")

    assert fragments
    assert fragments[0].source.id == "src-04"


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


async def test_a_hit_without_a_source_is_left_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fragment that belongs to no document cannot be cited."""

    class FakeRetriever:
        async def retrieve(self, *_args, **_kwargs):
            return [
                SearchHit(
                    doc_id="abc123",
                    ordinal=1,
                    kind="text",
                    source_id="",
                    source_kind="normative_act",
                    section_title="Раздел",
                    page_number=1,
                    content="Фрагмент",
                    score=0.9,
                ),
                SearchHit(
                    doc_id="abc123",
                    ordinal=2,
                    kind="text",
                    source_id="src-99",
                    source_kind="normative_act",
                    section_title="Раздел",
                    page_number=2,
                    content="Фрагмент",
                    score=0.8,
                ),
            ]

    monkeypatch.setattr(sources, "get_retriever", lambda: FakeRetriever())

    assert await search("начальное общее образование") == []


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


"""Tests for the source catalogue in ``app.agentic.sources``.

The catalogue holds the documents of the corpus: the federal educational
standards, the professional standards and the programmes of the organisation.
It answers two questions the agent asks — does this document exist, and what
does it say — and the tests pin both, including the honest answer for a query
the corpus does not cover.
"""


def test_the_corpus_holds_the_documents_of_the_corpus() -> None:
    """Every corpus directory contributes its documents."""
    found = {entry.id for entry in corpus_entries()}

    assert "src-03" in found
    assert "src-05" in found
    assert "src-10" in found


def test_a_catalog_entry_carries_what_the_programme_cites() -> None:
    """The document is cited with its order, its origin and how far it may be trusted."""
    record = get_source("src-05")

    assert record is not None
    assert record.kind is SourceKind.PROFESSIONAL_STANDARD
    assert "Профессиональный стандарт" in record.title
    assert record.identifier is not None
    assert "423н" in record.identifier
    assert record.trust is not None
    assert record.origin is not None
    assert record.confirmed is True


def test_a_document_the_corpus_does_not_hold_is_not_in_the_catalog() -> None:
    assert get_source("src-99") is None
    assert get_source("273-ФЗ") is None


async def test_a_search_finds_the_document_that_answers_the_query() -> None:
    """The professional standard named in the query comes first."""
    fragments = await search("профессиональный стандарт программист")

    assert fragments
    assert fragments[0].source.id == "src-04"
    assert fragments[0].source.confirmed is True


async def test_a_search_can_be_narrowed_to_a_kind() -> None:
    fragments = await search("образовательная программа", kinds=[SourceKind.REFERENCE])

    assert fragments
    assert all(fragment.source.kind is SourceKind.REFERENCE for fragment in fragments)


async def test_a_query_the_corpus_does_not_cover_finds_nothing() -> None:
    """The honest answer is an empty list, so the agent leaves the citation out."""
    assert await search("квантовая хромодинамика") == []


async def test_the_fragment_holds_the_lines_that_answer_the_query() -> None:
    fragments = await search("ФГОС среднего общего образования")

    assert fragments
    assert fragments[0].source.id == "src-03"
    assert fragments[0].text


def test_the_corpus_is_read_from_where_the_deployment_puts_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """The corpus path is a setting, not a place the code guesses."""
    monkeypatch.setenv(sources.CORPUS_ENV, "/somewhere/else")

    assert sources.corpus_path() == Path("/somewhere/else")


def test_a_corpus_without_a_description_contributes_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A directory of the corpus without a description is skipped, not an error."""
    empty = tmp_path / "corpus"
    (empty / "fgos").mkdir(parents=True)
    monkeypatch.setenv(sources.CORPUS_ENV, str(empty))
    sources.clear_cache()

    assert corpus_entries() == ()


def test_a_broken_catalogue_does_not_break_the_corpus(monkeypatch: pytest.MonkeyPatch) -> None:
    """The corpus answers on its own when the catalogue cannot be read."""

    def broken():
        raise RuntimeError("каталог не читается")

    monkeypatch.setattr(sources, "catalog_by_id", broken)
    monkeypatch.setattr(sources, "get_catalog_entry", broken)
    sources.clear_cache()

    entries = corpus_entries()

    assert entries
    assert "fgos-noo-2021" in {entry.id for entry in entries}
    # The document is still found, from the corpus rather than the catalogue.
    record = get_source("fgos-noo-2021")
    assert record is not None
    assert record.title


async def test_a_blank_query_never_reaches_the_corpus() -> None:
    """A query of nothing finds nothing."""
    assert await sources.search_corpus("   ") == []


def test_the_retriever_is_built_when_the_index_is_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the RAG on, the retriever is built from the provider and the store."""
    monkeypatch.setenv("RAG_ENABLED", "true")
    monkeypatch.setenv("RAG_OVERSAMPLE", "6")
    monkeypatch.setenv("RAG_MIN_SCORE", "0.42")
    monkeypatch.setenv("RAG_MAX_PER_SOURCE", "2")
    monkeypatch.setattr(sources, "get_session_maker", lambda: "session")
    monkeypatch.setattr(sources, "PgVectorStore", lambda _session, _table: ("store", "table"))
    sources.clear_retriever_cache()

    retriever = sources.get_retriever()

    assert retriever is not None
    assert retriever._min_score == 0.42
    assert retriever._max_per_source == 2
    assert retriever._oversample == 6


def test_a_provider_that_fails_leaves_the_search_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """An index that cannot be built is a note to the model, not a crash."""

    def broken():
        raise RuntimeError("нет эмбеддингов")

    monkeypatch.setenv("RAG_ENABLED", "true")
    monkeypatch.setattr(sources, "get_embedding_provider", broken)
    sources.clear_retriever_cache()

    with pytest.raises(RagUnavailable):
        sources.get_retriever()
