"""Tests for InMemoryVectorStore."""

import math

import pytest

from edu_rag import ChunkRecord, InMemoryVectorStore, SearchHit, StoreError
from edu_rag.store.base import VectorStore
from edu_rag.store.memory import _cosine


def _chunk(
    ordinal: int,
    kind: str = "text",
    hash_: str = "h",
    doc_id: str = "d1",
) -> ChunkRecord:
    return ChunkRecord(
        doc_id=doc_id,
        ordinal=ordinal,
        kind=kind,
        content=f"content-{ordinal}",
        content_hash=hash_,
        section_title=f"section-{ordinal}",
        page_number=ordinal + 1,
    )


def _unit(*values: float) -> list[float]:
    """L2-normalized vector from raw components."""
    norm = math.sqrt(sum(v * v for v in values))
    return [v / norm for v in values]


@pytest.fixture
def store() -> InMemoryVectorStore:
    return InMemoryVectorStore()


def test_satisfies_protocol(store):
    assert isinstance(store, VectorStore)


# ------------------------------------------------------------- _cosine
def test_cosine_identical():
    v = _unit(1, 2, 3)
    assert _cosine(v, v) == pytest.approx(1.0)


def test_cosine_orthogonal():
    assert _cosine(_unit(1, 0, 0), _unit(0, 1, 0)) == pytest.approx(0.0)


def test_cosine_dimension_mismatch():
    assert _cosine([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0


def test_cosine_zero_vector():
    assert _cosine([0.0, 0.0, 0.0], [1.0, 0.0, 0.0]) == 0.0


# --------------------------------------------------------------- upsert
async def test_upsert_empty_noop(store):
    await store.upsert_chunks("d1", [], [])
    assert await store.get_existing_hashes("d1") == {}


async def test_upsert_single(store):
    await store.upsert_chunks("d1", [_chunk(0, hash_="abc")], [_unit(1, 0, 0)])
    assert await store.get_existing_hashes("d1") == {(0, "text"): "abc"}


async def test_upsert_multiple(store):
    chunks = [_chunk(0, hash_="a"), _chunk(1, hash_="b"), _chunk(2, hash_="c")]
    vectors = [_unit(1, 0, 0), _unit(0, 1, 0), _unit(0, 0, 1)]
    await store.upsert_chunks("d1", chunks, vectors)

    assert await store.get_existing_hashes("d1") == {
        (0, "text"): "a",
        (1, "text"): "b",
        (2, "text"): "c",
    }


async def test_upsert_idempotent(store):
    """Writing the same key twice must not create duplicates."""
    c1 = _chunk(0, hash_="old")
    c2 = _chunk(0, hash_="new")
    await store.upsert_chunks("d1", [c1], [_unit(1, 0, 0)])
    await store.upsert_chunks("d1", [c2], [_unit(1, 0, 0)])

    assert await store.get_existing_hashes("d1") == {(0, "text"): "new"}


async def test_upsert_separates_kinds(store):
    """Same ordinal but different kind is a different key."""
    c_text = _chunk(0, kind="text", hash_="t")
    c_table = _chunk(0, kind="table", hash_="x")
    await store.upsert_chunks("d1", [c_text, c_table], [_unit(1, 0, 0), _unit(0, 1, 0)])

    assert await store.get_existing_hashes("d1") == {
        (0, "text"): "t",
        (0, "table"): "x",
    }


async def test_upsert_count_mismatch(store):
    with pytest.raises(StoreError, match="length mismatch"):
        await store.upsert_chunks("d1", [_chunk(0)], [])


async def test_upsert_rejects_doc_id_mismatch(store):
    """A chunk whose doc_id differs from the param is a caller bug."""
    wrong = _chunk(0, doc_id="other")
    with pytest.raises(StoreError, match="doc_id"):
        await store.upsert_chunks("d1", [wrong], [_unit(1, 0, 0)])


async def test_upsert_dimension_mismatch_across_calls(store):
    await store.upsert_chunks("d1", [_chunk(0)], [_unit(1, 0, 0)])
    with pytest.raises(StoreError, match="dimension"):
        await store.upsert_chunks("d1", [_chunk(0)], [_unit(1, 0)])


async def test_upsert_mixed_dimensions_in_one_call(store):
    """First vector latches dimension; a later mismatched vector raises."""
    await store.upsert_chunks("d1", [_chunk(0)], [_unit(1, 0, 0)])
    with pytest.raises(StoreError, match="dimension"):
        await store.upsert_chunks(
            "d1",
            [_chunk(1), _chunk(2)],
            [_unit(1, 0, 0), [1.0, 0.0]],
        )


async def test_upsert_rejects_empty_vectors(store):
    with pytest.raises(StoreError, match="must not be empty"):
        await store.upsert_chunks("d1", [_chunk(0)], [[]])


async def test_get_existing_hashes_unknown_doc(store):
    assert await store.get_existing_hashes("nope") == {}


# ------------------------------------------------------------- orphans
async def test_delete_orphans_removes_unlisted(store):
    chunks = [_chunk(0, hash_="a"), _chunk(1, hash_="b"), _chunk(2, hash_="c")]
    vectors = [_unit(1, 0, 0), _unit(0, 1, 0), _unit(0, 0, 1)]
    await store.upsert_chunks("d1", chunks, vectors)

    await store.delete_orphans("d1", keep=[(0, "text"), (2, "text")])
    assert await store.get_existing_hashes("d1") == {
        (0, "text"): "a",
        (2, "text"): "c",
    }


async def test_delete_orphans_unknown_doc(store):
    await store.delete_orphans("nope", keep=[(0, "text")])


async def test_delete_orphans_removes_doc_when_empty(store):
    await store.upsert_chunks("d1", [_chunk(0)], [_unit(1, 0, 0)])
    await store.delete_orphans("d1", keep=[])
    assert await store.list_docs() == []


# -------------------------------------------------------------- search
async def test_search_empty_store(store):
    assert await store.search([1.0, 0.0, 0.0]) == []


async def test_search_empty_query(store):
    await store.upsert_chunks("d1", [_chunk(0)], [_unit(1, 0, 0)])
    assert await store.search([]) == []


async def test_search_dimension_mismatch(store):
    await store.upsert_chunks("d1", [_chunk(0)], [_unit(1, 0, 0)])
    with pytest.raises(StoreError, match="dimension"):
        await store.search([1.0, 0.0])


async def test_search_ranks_by_similarity(store):
    chunks = [_chunk(0, hash_="a"), _chunk(1, hash_="b"), _chunk(2, hash_="c")]
    vectors = [_unit(1, 0, 0), _unit(0, 1, 0), _unit(0, 0, 1)]
    await store.upsert_chunks("d1", chunks, vectors)

    hits = await store.search(_unit(1, 0.1, 0), top_k=3)
    assert [h.ordinal for h in hits] == [0, 1, 2]
    assert hits[0].score > hits[1].score > hits[2].score


async def test_search_top_k_limits(store):
    chunks = [_chunk(i) for i in range(5)]
    vectors = [_unit(1, 0, 0) for _ in range(5)]
    await store.upsert_chunks("d1", chunks, vectors)

    hits = await store.search(_unit(1, 0, 0), top_k=2)
    assert len(hits) == 2


async def test_search_result_fields(store):
    chunk = _chunk(7, hash_="x")
    await store.upsert_chunks("d1", [chunk], [_unit(1, 0, 0)])

    [hit] = await store.search(_unit(1, 0, 0))
    assert isinstance(hit, SearchHit)
    assert hit.doc_id == "d1"
    assert hit.ordinal == 7
    assert hit.kind == "text"
    assert hit.section_title == "section-7"
    assert hit.page_number == 8
    assert hit.content == "content-7"
    assert 0.99 < hit.score <= 1.0


async def test_search_across_docs(store):
    await store.upsert_chunks("d1", [_chunk(0, doc_id="d1")], [_unit(1, 0, 0)])
    await store.upsert_chunks("d2", [_chunk(0, doc_id="d2")], [_unit(0, 1, 0)])

    hits = await store.search(_unit(1, 0, 0), top_k=10)
    assert {h.doc_id for h in hits} == {"d1", "d2"}


# ----------------------------------------------------------- list_docs
async def test_list_docs_empty(store):
    assert await store.list_docs() == []


async def test_list_docs_counts(store):
    await store.upsert_chunks(
        "d1",
        [_chunk(0, doc_id="d1"), _chunk(1, doc_id="d1")],
        [_unit(1, 0, 0), _unit(0, 1, 0)],
    )
    await store.upsert_chunks("d2", [_chunk(0, doc_id="d2")], [_unit(0, 0, 1)])

    docs = await store.list_docs()
    assert docs == [
        {"doc_id": "d1", "chunks": 2},
        {"doc_id": "d2", "chunks": 1},
    ]
