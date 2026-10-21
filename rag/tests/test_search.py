"""Tests for the public search interface."""

import hashlib

import pytest

from edu_rag import (
    ChunkRecord,
    EmbeddingConfig,
    InMemoryVectorStore,
    MockEmbeddingProvider,
)
from edu_rag.search import MIN_SCORE, search_knowledge_base


def _chunk(
    ordinal: int,
    content: str,
    *,
    doc_id: str = "d1",
    kind: str = "text",
    source_type: str = "fgos",
    section: str | None = None,
    page: int | None = None,
) -> ChunkRecord:
    return ChunkRecord(
        doc_id=doc_id,
        ordinal=ordinal,
        kind=kind,
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        section_title=section,
        page_number=page,
        metadata={"source_type": source_type, "document_title": f"title-{doc_id}"},
    )


@pytest.fixture
def provider() -> MockEmbeddingProvider:
    """3-dimensional mock provider, so test vectors match by construction."""
    return MockEmbeddingProvider(EmbeddingConfig(dimension=3))


def _zero_vec() -> list[float]:
    return [0.0, 0.0, 0.0]


# ------------------------------------------------------------ empty
async def test_empty_query_returns_empty(provider):
    store = InMemoryVectorStore()
    result = await search_knowledge_base("", provider=provider, store=store)
    assert result == []


async def test_whitespace_query_returns_empty(provider):
    store = InMemoryVectorStore()
    result = await search_knowledge_base("   \t\n", provider=provider, store=store)
    assert result == []


async def test_top_k_zero_returns_empty(provider):
    store = InMemoryVectorStore()
    result = await search_knowledge_base("query", provider=provider, store=store, top_k=0)
    assert result == []


async def test_empty_store_returns_empty(provider):
    store = InMemoryVectorStore()
    result = await search_knowledge_base("any query", provider=provider, store=store)
    assert result == []


async def test_no_match_above_threshold_returns_empty(provider):
    """Regression: no relevant fragment must not return a random one.

    Calibration reference (2026-10-21, GigaChat Embeddings, 590 fragments):

    - unrelated query top-1 score: 0.16–0.20
    - related query top-1 score: 0.39–0.44
    - MIN_SCORE default 0.30 sits in the gap

    If the default threshold moves below the unrelated top-1 range, this
    test catches it: a zero-vector fragment must remain below MIN_SCORE.
    """
    store = InMemoryVectorStore()
    await store.upsert_chunks("d1", [_chunk(0, "x")], [_zero_vec()], ["fp-0"])
    result = await search_knowledge_base(
        "something else entirely",
        provider=provider,
        store=store,
        min_score=MIN_SCORE,
    )
    assert result == []


# ------------------------------------------------------------ hits
async def test_returns_hits_above_threshold(provider):
    query = "компетенция УК-1"
    [qvec] = await provider.embed([query])
    store = InMemoryVectorStore()
    chunk = _chunk(0, query, source_type="fgos", section="Требования", page=5)
    await store.upsert_chunks("d1", [chunk], [qvec], ["fp-0"])

    result = await search_knowledge_base(query, provider=provider, store=store, min_score=0.0)
    assert len(result) == 1
    hit = result[0]
    assert hit.content == query
    assert hit.section_title == "Требования"
    assert hit.page_number == 5
    assert hit.metadata["source_type"] == "fgos"


async def test_top_k_respected(provider):
    query = "any"
    [qvec] = await provider.embed([query])
    store = InMemoryVectorStore()
    chunks = [_chunk(i, f"text {i}") for i in range(10)]
    vectors = [qvec for _ in range(10)]
    fps = [f"fp-{i}" for i in range(10)]
    await store.upsert_chunks("d1", chunks, vectors, fps)

    result = await search_knowledge_base(query, provider=provider, store=store, top_k=3, min_score=0.0)
    assert len(result) == 3


async def test_min_score_filter(provider):
    query = "test"
    [qvec] = await provider.embed([query])
    store = InMemoryVectorStore()
    c1 = _chunk(0, query)
    c2 = _chunk(1, "unrelated")
    await store.upsert_chunks("d1", [c1, c2], [qvec, _zero_vec()], ["fp-0", "fp-1"])

    result = await search_knowledge_base(query, provider=provider, store=store, top_k=2, min_score=0.5)
    assert len(result) == 1
    assert result[0].content == query


# ------------------------------------------------------------ filter
async def test_source_type_filter(provider):
    query = "text"
    [qvec] = await provider.embed([query])
    store = InMemoryVectorStore()
    fgos = _chunk(0, query, doc_id="d1", source_type="fgos")
    prof = _chunk(0, query, doc_id="d2", source_type="prof_stand")
    await store.upsert_chunks("d1", [fgos], [qvec], ["fp-fgos"])
    await store.upsert_chunks("d2", [prof], [qvec], ["fp-prof"])

    all_hits = await search_knowledge_base(query, provider=provider, store=store, top_k=10, min_score=0.0)
    assert {h.doc_id for h in all_hits} == {"d1", "d2"}

    fgos_hits = await search_knowledge_base(
        query,
        provider=provider,
        store=store,
        source_type="fgos",
        top_k=10,
        min_score=0.0,
    )
    assert {h.doc_id for h in fgos_hits} == {"d1"}

    prof_hits = await search_knowledge_base(
        query,
        provider=provider,
        store=store,
        source_type="prof_stand",
        top_k=10,
        min_score=0.0,
    )
    assert {h.doc_id for h in prof_hits} == {"d2"}


async def test_filter_no_match_returns_empty(provider):
    query = "text"
    [qvec] = await provider.embed([query])
    store = InMemoryVectorStore()
    await store.upsert_chunks("d1", [_chunk(0, query)], [qvec], ["fp-0"])

    result = await search_knowledge_base(
        query,
        provider=provider,
        store=store,
        source_type="edu_programs",
        top_k=10,
        min_score=0.0,
    )
    assert result == []
