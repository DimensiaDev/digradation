"""Tests for the query-side retrieval policy."""

from __future__ import annotations

from edu_rag.models import SearchHit
from edu_rag.retrieval import RagRetriever


class FakeProvider:
    model = "fake"
    dimension = 2

    def __init__(self) -> None:
        self.queries: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.queries.append(texts)
        return [[1.0, 0.0] for _ in texts]


class FakeStore:
    def __init__(self, hits: list[SearchHit]) -> None:
        self.hits = hits
        self.calls: list[tuple[list[float], int, object]] = []

    async def search(self, query_vec, top_k=10, *, kinds=None):
        self.calls.append((query_vec, top_k, kinds))
        return self.hits[:top_k]


def _hit(source_id: str, score: float, ordinal: int = 0) -> SearchHit:
    return SearchHit(
        doc_id=f"doc-{source_id}",
        ordinal=ordinal,
        kind="text",
        source_id=source_id,
        source_kind="normative_act",
        section_title=None,
        page_number=None,
        content="text",
        score=score,
    )


async def test_blank_query_does_not_embed() -> None:
    provider = FakeProvider()
    retriever = RagRetriever(provider, FakeStore([]))
    assert await retriever.retrieve("   ") == []
    assert provider.queries == []


async def test_retrieval_applies_the_score_floor() -> None:
    store = FakeStore([_hit("src-01", 0.9), _hit("src-02", 0.1)])
    retriever = RagRetriever(FakeProvider(), store, min_score=0.5)
    hits = await retriever.retrieve("query", limit=5)
    assert [hit.source_id for hit in hits] == ["src-01"]


async def test_retrieval_caps_each_source_and_respects_limit() -> None:
    store = FakeStore(
        [
            _hit("src-01", 0.9, 0),
            _hit("src-01", 0.8, 1),
            _hit("src-01", 0.7, 2),
            _hit("src-02", 0.6, 0),
            _hit("src-02", 0.5, 1),
        ]
    )
    retriever = RagRetriever(FakeProvider(), store, max_per_source=2)
    hits = await retriever.retrieve("query", limit=3)
    assert [hit.source_id for hit in hits] == ["src-01", "src-01", "src-02"]
    assert len(hits) == 3


async def test_retrieval_oversamples_and_forwards_kinds() -> None:
    store = FakeStore([])
    retriever = RagRetriever(FakeProvider(), store, oversample=3)
    await retriever.retrieve("query", kinds={"professional_standard"}, limit=4)
    assert store.calls == [([1.0, 0.0], 12, {"professional_standard"})]
