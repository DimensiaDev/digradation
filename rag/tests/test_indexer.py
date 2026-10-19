"""Tests for DocumentIndexer — idempotency, batching, error handling."""

import hashlib

import pytest

from edu_rag import (
    ChunkRecord,
    DocumentIndexer,
    EmbeddingConfig,
    EmbeddingError,
    InMemoryVectorStore,
    MockEmbeddingProvider,
    StoreError,
)


def _h(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _chunk(
    ordinal: int,
    content: str = "default",
    kind: str = "text",
    doc_id: str = "d1",
    section_title: str | None = None,
    page_number: int | None = None,
    metadata: dict | None = None,
) -> ChunkRecord:
    return ChunkRecord(
        doc_id=doc_id,
        ordinal=ordinal,
        kind=kind,
        content=content,
        content_hash=_h(content),
        section_title=section_title,
        page_number=page_number,
        metadata=metadata or {},
    )


class SpyProvider:
    """MockEmbeddingProvider that records every call."""

    def __init__(self, dimension: int = 8) -> None:
        self.model = "Spy"
        self.dimension = dimension
        self.calls: list[list[str]] = []
        self._delegate = MockEmbeddingProvider(EmbeddingConfig(dimension=dimension))

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return await self._delegate.embed(texts)


class ShortProvider:
    """Returns fewer vectors than texts — invalid contract."""

    model = "short"
    dimension = 8

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] * self.dimension for _ in texts[:-1]]


class WrongDimProvider:
    """Returns vectors with a different dimension than declared."""

    model = "wrong"
    dimension = 8

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] * 4 for _ in texts]


class BrokenStore:
    """In-memory store that fails every upsert."""

    def __init__(self) -> None:
        self._delegate = InMemoryVectorStore()

    async def get_fingerprints(self, doc_id):
        return await self._delegate.get_fingerprints(doc_id)

    async def delete_orphans(self, doc_id, keep):
        await self._delegate.delete_orphans(doc_id, keep)

    async def upsert_chunks(self, _doc_id, _chunks, _vectors, _fingerprints):
        raise StoreError("simulated store failure")

    async def search(self, query_vec, top_k=10):
        return await self._delegate.search(query_vec, top_k)

    async def list_docs(self):
        return await self._delegate.list_docs()


def _indexer(provider=None, store=None, batch_size: int = 10) -> DocumentIndexer:
    provider = provider or MockEmbeddingProvider(EmbeddingConfig(dimension=8))
    store = store or InMemoryVectorStore()
    return DocumentIndexer(
        provider=provider,
        store=store,
        config=EmbeddingConfig(dimension=8, batch_size=batch_size),
    )


# --------------------------------------------------------- constructor
def test_constructor_rejects_dimension_mismatch():
    """Regression #4: provider dimension must equal config dimension."""
    with pytest.raises(ValueError, match="dimension"):
        DocumentIndexer(
            provider=MockEmbeddingProvider(EmbeddingConfig(dimension=4)),
            store=InMemoryVectorStore(),
            config=EmbeddingConfig(dimension=8),
        )


# ------------------------------------------------------------ first run
async def test_first_run_indexes_all():
    store = InMemoryVectorStore()
    indexer = _indexer(store=store)
    chunks = [_chunk(i, content=f"c{i}") for i in range(5)]

    result = await indexer.index("d1", chunks)
    assert result.indexed == 5
    assert result.skipped == 0
    assert result.failed == 0
    assert len(await store.get_fingerprints("d1")) == 5


# ----------------------------------------------------- idempotency core
async def test_second_run_skips_all_without_provider_calls():
    """Regression #14: verify 1st run actually indexed before testing 2nd."""
    provider = SpyProvider(dimension=8)
    store = InMemoryVectorStore()
    indexer = _indexer(provider=provider, store=store)
    chunks = [_chunk(i, content=f"c{i}") for i in range(5)]

    first = await indexer.index("d1", chunks)
    assert first.indexed == 5, "first run must actually index"
    assert len(await store.get_fingerprints("d1")) == 5

    provider.calls.clear()
    second = await indexer.index("d1", chunks)

    assert second.indexed == 0
    assert second.skipped == 5
    assert second.failed == 0
    assert provider.calls == []


async def test_changed_chunk_reindexed_only():
    provider = SpyProvider(dimension=8)
    store = InMemoryVectorStore()
    indexer = _indexer(provider=provider, store=store)

    await indexer.index("d1", [_chunk(i, content=f"c{i}") for i in range(3)])
    provider.calls.clear()

    new_chunks = [
        _chunk(0, content="c0"),
        _chunk(1, content="CHANGED"),
        _chunk(2, content="c2"),
    ]
    result = await indexer.index("d1", new_chunks)

    assert result.indexed == 1
    assert result.skipped == 2
    assert provider.calls == [["CHANGED"]]


async def test_changed_metadata_reindexed():
    """Regression #8: metadata change must trigger re-embed."""
    provider = SpyProvider(dimension=8)
    store = InMemoryVectorStore()
    indexer = _indexer(provider=provider, store=store)

    await indexer.index("d1", [_chunk(0, content="x", section_title="A")])
    provider.calls.clear()

    await indexer.index("d1", [_chunk(0, content="x", section_title="B")])
    assert len(provider.calls) == 1


async def test_changed_model_reindexed():
    """Regression #3: switching embedding model must trigger re-embed."""
    store = InMemoryVectorStore()
    provider = MockEmbeddingProvider(EmbeddingConfig(dimension=8))
    config_a = EmbeddingConfig(model="ModelA", dimension=8)
    config_b = EmbeddingConfig(model="ModelB", dimension=8)

    idx_a = DocumentIndexer(provider=provider, store=store, config=config_a)
    await idx_a.index("d1", [_chunk(0, content="x")])

    idx_b = DocumentIndexer(provider=provider, store=store, config=config_b)
    result = await idx_b.index("d1", [_chunk(0, content="x")])
    assert result.indexed == 1
    assert result.skipped == 0


# ---------------------------------------------------------- validation
async def test_rejects_chunk_with_wrong_doc_id():
    """Regression #5: chunk.doc_id must match the doc_id argument."""
    indexer = _indexer()
    with pytest.raises(ValueError, match="doc_id"):
        await indexer.index("d1", [_chunk(0, doc_id="other")])


async def test_rejects_duplicate_keys():
    """Regression #7: duplicate (ordinal, kind) must raise."""
    indexer = _indexer()
    with pytest.raises(ValueError, match="duplicate"):
        await indexer.index(
            "d1",
            [_chunk(0, content="a"), _chunk(0, content="b")],
        )


# ------------------------------------------------------------ orphans
async def test_removed_chunks_pruned():
    store = InMemoryVectorStore()
    indexer = _indexer(store=store)

    await indexer.index("d1", [_chunk(i, content=f"c{i}") for i in range(5)])
    result = await indexer.index(
        "d1",
        [_chunk(0, content="c0"), _chunk(1, content="c1")],
    )
    assert result.deleted == 3
    assert set((await store.get_fingerprints("d1")).keys()) == {
        (0, "text"),
        (1, "text"),
    }


async def test_empty_document_deletes_everything():
    store = InMemoryVectorStore()
    indexer = _indexer(store=store)

    await indexer.index("d1", [_chunk(i, content=f"c{i}") for i in range(3)])
    result = await indexer.index("d1", [])

    assert result.deleted == 3
    assert await store.get_fingerprints("d1") == {}


# ------------------------------------------------------------- batching
async def test_batches_respect_config():
    provider = SpyProvider(dimension=8)
    indexer = _indexer(provider=provider, store=InMemoryVectorStore(), batch_size=2)
    await indexer.index("d1", [_chunk(i, content=f"c{i}") for i in range(5)])
    assert [len(c) for c in provider.calls] == [2, 2, 1]


# ---------------------------------------------------------- error paths
async def test_provider_failure_marks_batch_failed_and_continues():
    provider = SpyProvider(dimension=8)
    store = InMemoryVectorStore()
    indexer = _indexer(provider=provider, store=store, batch_size=2)

    original_embed = provider.embed
    call_count = {"n": 0}

    async def flaky_embed(texts):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise EmbeddingError("transient")
        return await original_embed(texts)

    provider.embed = flaky_embed
    result = await indexer.index("d1", [_chunk(i, content=f"c{i}") for i in range(5)])
    assert result.indexed == 3
    assert result.failed == 2
    assert "transient" in result.errors[0]


async def test_all_batches_fail():
    class AlwaysFailProvider:
        model = "fail"
        dimension = 8

        async def embed(self, _texts):
            raise EmbeddingError("boom")

    indexer = _indexer(provider=AlwaysFailProvider())
    result = await indexer.index("d1", [_chunk(i) for i in range(3)])
    assert result.indexed == 0
    assert result.failed == 3


async def test_provider_returns_wrong_count():
    indexer = _indexer(provider=ShortProvider())
    result = await indexer.index("d1", [_chunk(i) for i in range(3)])
    assert result.indexed == 0
    assert result.failed == 3
    assert "returned 2 vectors for 3 chunks" in result.errors[0]


async def test_provider_returns_wrong_dimension():
    """Regression #4: wrong vector dimension from provider is a batch failure."""
    indexer = _indexer(provider=WrongDimProvider())
    result = await indexer.index("d1", [_chunk(i) for i in range(3)])
    assert result.indexed == 0
    assert result.failed == 3
    assert "wrong dimension" in result.errors[0]


async def test_store_failure_marks_batch_failed():
    indexer = _indexer(store=BrokenStore())
    result = await indexer.index("d1", [_chunk(i) for i in range(3)])
    assert result.indexed == 0
    assert result.failed == 3
    assert "simulated store failure" in result.errors[0]


async def test_unexpected_exception_from_provider_does_not_abort():
    """Regression #6: unexpected exceptions must not crash the run."""

    class ExplodingProvider:
        model = "exploding"
        dimension = 8

        async def embed(self, _texts):
            raise RuntimeError("unexpected failure")

    indexer = _indexer(provider=ExplodingProvider())
    result = await indexer.index("d1", [_chunk(i) for i in range(3)])
    assert result.indexed == 0
    assert result.failed == 3
    assert "RuntimeError" in result.errors[0]


async def test_failed_batch_preserves_previous_version():
    provider = SpyProvider(dimension=8)
    store = InMemoryVectorStore()
    indexer = _indexer(provider=provider, store=store)

    await indexer.index("d1", [_chunk(0, content="old")])

    class FailNext:
        def __init__(self):
            self._called = False

        async def __call__(self, _texts):
            self._called = True
            raise EmbeddingError("oops")

    provider.embed = FailNext()
    result = await indexer.index("d1", [_chunk(0, content="new")])
    assert result.failed == 1
    hits = await store.search([1.0] * 8, top_k=10)
    assert hits[0].content == "old"


# ------------------------------------------------------------ doc_id key
async def test_independent_documents():
    store = InMemoryVectorStore()
    indexer = _indexer(store=store)
    await indexer.index("d1", [_chunk(0, content="a", doc_id="d1")])
    await indexer.index("d2", [_chunk(0, content="b", doc_id="d2")])
    assert len(await store.get_fingerprints("d1")) == 1
    assert len(await store.get_fingerprints("d2")) == 1


class PruneFailStore:
    """Store that succeeds on upsert but fails on delete_orphans."""

    def __init__(self) -> None:
        self._delegate = InMemoryVectorStore()

    async def get_fingerprints(self, doc_id):
        return await self._delegate.get_fingerprints(doc_id)

    async def upsert_chunks(self, doc_id, chunks, vectors, fingerprints):
        await self._delegate.upsert_chunks(doc_id, chunks, vectors, fingerprints)

    async def delete_orphans(self, _doc_id, _keep):
        raise StoreError("prune failure")

    async def search(self, query_vec, top_k=10):
        return await self._delegate.search(query_vec, top_k)

    async def list_docs(self):
        return await self._delegate.list_docs()


async def test_prune_failure_recorded_not_raised():
    """Prune errors must land in IndexResult.errors, not crash the run."""
    indexer = _indexer(store=PruneFailStore())
    result = await indexer.index("d1", [_chunk(0, content="x")])

    assert result.indexed == 1
    assert result.deleted == 0
    assert any("prune" in e for e in result.errors)
    assert "simulated" not in str(result.errors)
