"""Tests for DocumentIndexer — idempotency, batching, error handling."""

from edu_rag import (
    ChunkRecord,
    DocumentIndexer,
    EmbeddingConfig,
    EmbeddingError,
    InMemoryVectorStore,
    MockEmbeddingProvider,
    StoreError,
)


# --------------------------------------------------------------- helpers
def _chunk(
    ordinal: int,
    content: str = "default",
    kind: str = "text",
    doc_id: str = "d1",
    content_hash: str | None = None,
) -> ChunkRecord:
    """Build a ChunkRecord; hash defaults to a stable derivation of content."""
    return ChunkRecord(
        doc_id=doc_id,
        ordinal=ordinal,
        kind=kind,
        content=content,
        content_hash=content_hash if content_hash is not None else f"h-{content}",
    )


class SpyProvider:
    """MockEmbeddingProvider that records every call."""

    def __init__(self, dimension: int = 8, fail_next: bool = False) -> None:
        self.model = "spy"
        self.dimension = dimension
        self.calls: list[list[str]] = []
        self._fail_next = fail_next
        self._delegate = MockEmbeddingProvider(EmbeddingConfig(dimension=dimension))

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self._fail_next:
            raise EmbeddingError("simulated provider failure")
        return await self._delegate.embed(texts)


class ShortProvider:
    """Provider that returns fewer vectors than texts — invalid contract."""

    model = "short"
    dimension = 8

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] * self.dimension for _ in texts[:-1]]


class BrokenStore:
    """In-memory store that fails every upsert."""

    def __init__(self) -> None:
        self._delegate = InMemoryVectorStore()

    async def get_existing_hashes(self, doc_id: str):
        return await self._delegate.get_existing_hashes(doc_id)

    async def delete_orphans(self, doc_id, keep):
        await self._delegate.delete_orphans(doc_id, keep)

    async def upsert_chunks(self, _doc_id, _chunks, _vectors):
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


# ------------------------------------------------------------- first run
async def test_first_run_indexes_all():
    store = InMemoryVectorStore()
    indexer = _indexer(store=store)
    chunks = [_chunk(i) for i in range(5)]

    result = await indexer.index("d1", chunks)

    assert result.doc_id == "d1"
    assert result.indexed == 5
    assert result.skipped == 0
    assert result.failed == 0
    assert result.deleted == 0
    assert result.errors == []
    assert len(await store.get_existing_hashes("d1")) == 5


# ----------------------------------------------------- idempotency core
async def test_second_run_skips_all_without_provider_calls():
    """Key acceptance criterion: re-indexing unchanged content makes no API calls."""
    provider = SpyProvider(dimension=8)
    store = InMemoryVectorStore()
    indexer = _indexer(provider=provider, store=store)
    chunks = [_chunk(i) for i in range(5)]

    await indexer.index("d1", chunks)
    provider.calls.clear()

    result = await indexer.index("d1", chunks)

    assert result.indexed == 0
    assert result.skipped == 5
    assert result.failed == 0
    assert result.deleted == 0
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
    assert len(provider.calls) == 1
    assert provider.calls[0] == ["CHANGED"]


async def test_kind_is_part_of_key():
    """A chunk and a table with the same ordinal are distinct."""
    store = InMemoryVectorStore()
    indexer = _indexer(store=store)
    chunks = [
        _chunk(0, content="text-at-0", kind="text"),
        _chunk(0, content="table-at-0", kind="table"),
    ]

    result = await indexer.index("d1", chunks)
    assert result.indexed == 2
    assert len(await store.get_existing_hashes("d1")) == 2


# ------------------------------------------------------------ orphans
async def test_removed_chunks_pruned():
    store = InMemoryVectorStore()
    indexer = _indexer(store=store)

    await indexer.index("d1", [_chunk(i) for i in range(5)])
    result = await indexer.index("d1", [_chunk(0), _chunk(1)])

    assert result.indexed == 0
    assert result.skipped == 2
    assert result.deleted == 3
    assert set((await store.get_existing_hashes("d1")).keys()) == {
        (0, "text"),
        (1, "text"),
    }


async def test_empty_document_deletes_everything():
    store = InMemoryVectorStore()
    indexer = _indexer(store=store)

    await indexer.index("d1", [_chunk(i) for i in range(3)])
    result = await indexer.index("d1", [])

    assert result.indexed == 0
    assert result.skipped == 0
    assert result.deleted == 3
    assert await store.get_existing_hashes("d1") == {}


async def test_empty_document_on_empty_store():
    store = InMemoryVectorStore()
    indexer = _indexer(store=store)

    result = await indexer.index("never-seen", [])
    assert result.deleted == 0
    assert result.indexed == 0


# ------------------------------------------------------------- batching
async def test_batches_respect_config():
    provider = SpyProvider(dimension=8)
    indexer = _indexer(provider=provider, store=InMemoryVectorStore(), batch_size=2)

    await indexer.index("d1", [_chunk(i, content=f"c{i}") for i in range(5)])

    assert len(provider.calls) == 3
    assert [len(c) for c in provider.calls] == [2, 2, 1]


# ---------------------------------------------------------- error paths
async def test_provider_failure_marks_batch_failed_and_continues():
    """A failing batch must not stop the loop — remaining batches still run."""
    provider = SpyProvider(dimension=8)
    store = InMemoryVectorStore()
    indexer = _indexer(provider=provider, store=store, batch_size=2)

    original_embed = provider.embed
    call_count = {"n": 0}

    async def flaky_embed(texts):
        call_count["n"] += 1
        if call_count["n"] == 2:
            provider.calls.append(list(texts))
            raise EmbeddingError("transient")
        return await original_embed(texts)

    provider.embed = flaky_embed

    result = await indexer.index("d1", [_chunk(i, content=f"c{i}") for i in range(5)])

    assert result.indexed == 3
    assert result.failed == 2
    assert len(result.errors) == 1
    assert "transient" in result.errors[0]


async def test_all_batches_fail():
    indexer = _indexer(provider=SpyProvider(fail_next=True))
    result = await indexer.index("d1", [_chunk(i) for i in range(3)])

    assert result.indexed == 0
    assert result.failed == 3
    assert len(result.errors) == 1


async def test_provider_returns_wrong_count():
    indexer = _indexer(provider=ShortProvider())
    result = await indexer.index("d1", [_chunk(i) for i in range(3)])

    assert result.indexed == 0
    assert result.failed == 3
    assert "returned 2 vectors for 3 chunks" in result.errors[0]


async def test_store_failure_marks_batch_failed():
    indexer = _indexer(store=BrokenStore())
    result = await indexer.index("d1", [_chunk(i) for i in range(3)])

    assert result.indexed == 0
    assert result.failed == 3
    assert "simulated store failure" in result.errors[0]


async def test_failed_batch_preserves_previous_version():
    """When re-embedding fails, the old chunk content must stay in the store."""
    provider = SpyProvider(dimension=8)
    store = InMemoryVectorStore()
    indexer = _indexer(provider=provider, store=store, batch_size=10)

    await indexer.index("d1", [_chunk(0, content="old")])

    provider._fail_next = True
    result = await indexer.index("d1", [_chunk(0, content="new")])

    assert result.failed == 1
    assert result.indexed == 0
    [hit] = await store.search([1.0] * 8, top_k=10)
    assert hit.content == "old"


# ------------------------------------------------------------ doc_id key
async def test_independent_documents():
    store = InMemoryVectorStore()
    indexer = _indexer(store=store)

    await indexer.index("d1", [_chunk(0, doc_id="d1")])
    await indexer.index("d2", [_chunk(0, doc_id="d2")])

    assert len(await store.get_existing_hashes("d1")) == 1
    assert len(await store.get_existing_hashes("d2")) == 1
    assert await store.list_docs() == [
        {"doc_id": "d1", "chunks": 1},
        {"doc_id": "d2", "chunks": 1},
    ]


# --------------------------------------------------------------- result
async def test_result_total_property():
    indexer = _indexer()
    result = await indexer.index("d1", [_chunk(i) for i in range(3)])
    assert result.total == 3
