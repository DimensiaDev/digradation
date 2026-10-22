"""Tests for InMemoryVectorStore."""

import hashlib
import math

import pytest

from edu_rag import ChunkRecord, InMemoryVectorStore, SearchHit, StoreError
from edu_rag.store.base import VectorStore
from edu_rag.store.memory import _cosine


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
    source_id: str | None = None,
    source_kind: str | None = None,
) -> ChunkRecord:
    return ChunkRecord(
        doc_id=doc_id,
        ordinal=ordinal,
        kind=kind,
        source_id=source_id,
        source_kind=source_kind,
        content=content,
        content_hash=_h(content),
        section_title=section_title,
        page_number=page_number,
        metadata=metadata or {},
    )


def _unit(*values: float) -> list[float]:
    norm = math.sqrt(sum(v * v for v in values))
    return [v / norm for v in values]


def _fps(chunks: list[ChunkRecord], model: str = "E", dim: int = 3) -> list[str]:
    return [c.fingerprint(model, dim) for c in chunks]


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


def test_cosine_uses_normalization_not_dot():
    """Regression: dot([2,0],[4,0])=8, cosine=1.0 — only cosine is right."""
    assert _cosine([2.0, 0.0], [4.0, 0.0]) == pytest.approx(1.0)


def test_cosine_non_unit_values():
    """Regression: cosine([1,0],[1,1]) = 1/sqrt(2) — not 1, not 1."""
    assert _cosine([1.0, 0.0], [1.0, 1.0]) == pytest.approx(1 / math.sqrt(2))


def test_cosine_anti_parallel():
    assert _cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


# --------------------------------------------------------------- upsert
async def test_upsert_empty_noop(store):
    await store.upsert_chunks("d1", [], [], [])
    assert await store.get_fingerprints("d1") == {}


async def test_upsert_single(store):
    c = _chunk(0, content="abc")
    await store.upsert_chunks("d1", [c], [_unit(1, 0, 0)], _fps([c]))
    assert list((await store.get_fingerprints("d1")).keys()) == [(0, "text")]


async def test_upsert_idempotent(store):
    c1 = _chunk(0, content="old")
    c2 = _chunk(0, content="new")
    await store.upsert_chunks("d1", [c1], [_unit(1, 0, 0)], _fps([c1]))
    await store.upsert_chunks("d1", [c2], [_unit(1, 0, 0)], _fps([c2]))
    assert len(await store.get_fingerprints("d1")) == 1


async def test_upsert_rejects_doc_id_mismatch(store):
    wrong = _chunk(0, doc_id="other")
    with pytest.raises(StoreError, match="doc_id"):
        await store.upsert_chunks("d1", [wrong], [_unit(1, 0, 0)], _fps([wrong]))


async def test_upsert_rejects_duplicate_keys_in_batch(store):
    a = _chunk(0, content="a")
    b = _chunk(0, content="b")
    with pytest.raises(StoreError, match="duplicate"):
        await store.upsert_chunks("d1", [a, b], [_unit(1, 0, 0), _unit(0, 1, 0)], _fps([a, b]))


async def test_upsert_rejects_length_mismatch(store):
    c = _chunk(0)
    with pytest.raises(StoreError, match="length mismatch"):
        await store.upsert_chunks("d1", [c], [], [])


async def test_upsert_rejects_empty_vectors(store):
    c = _chunk(0)
    with pytest.raises(StoreError, match="must not be empty"):
        await store.upsert_chunks("d1", [c], [[]], _fps([c]))


async def test_upsert_no_partial_write_on_late_bad_dimension(store):
    """Regression #1: bad vector in batch must not leave earlier rows persisted."""
    good = _chunk(0, content="g")
    bad = _chunk(1, content="b")
    with pytest.raises(StoreError, match="dimension"):
        await store.upsert_chunks(
            "d1",
            [good, bad],
            [_unit(1, 0, 0), [1.0, 0.0]],
            _fps([good, bad]),
        )
    assert await store.get_fingerprints("d1") == {}
    await store.upsert_chunks("d1", [_chunk(0)], [[1.0, 0.0, 0.0, 0.0]], _fps([_chunk(0)], dim=4))


async def test_upsert_defensive_copy(store):
    """Regression #10: mutating input after upsert must not change stored data."""
    c = _chunk(0, content="original")
    vec = [1.0, 0.0, 0.0]
    await store.upsert_chunks("d1", [c], [vec], _fps([c]))

    c.metadata["hacked"] = True
    vec[0] = 999.0

    hits = await store.search([1.0, 0.0, 0.0], top_k=10)
    [hit] = hits
    assert hit.metadata.get("hacked") is None
    assert hit.score == pytest.approx(1.0)


# ------------------------------------------------------------- orphans
async def test_delete_orphans_removes_unlisted(store):
    cs = [_chunk(0, content="a"), _chunk(1, content="b"), _chunk(2, content="c")]
    await store.upsert_chunks(
        "d1",
        cs,
        [_unit(1, 0, 0), _unit(0, 1, 0), _unit(0, 0, 1)],
        _fps(cs),
    )
    await store.delete_orphans("d1", keep=[(0, "text"), (2, "text")])
    assert set((await store.get_fingerprints("d1")).keys()) == {
        (0, "text"),
        (2, "text"),
    }


async def test_delete_orphans_unknown_doc(store):
    await store.delete_orphans("nope", keep=[(0, "text")])


async def test_delete_orphans_removes_doc_when_empty(store):
    c = _chunk(0)
    await store.upsert_chunks("d1", [c], [_unit(1, 0, 0)], _fps([c]))
    await store.delete_orphans("d1", keep=[])
    assert await store.list_docs() == []


# -------------------------------------------------------------- search
async def test_search_empty_store(store):
    assert await store.search([1.0, 0.0, 0.0]) == []


async def test_search_empty_query(store):
    c = _chunk(0)
    await store.upsert_chunks("d1", [c], [_unit(1, 0, 0)], _fps([c]))
    assert await store.search([]) == []


async def test_search_negative_top_k_returns_empty(store):
    """Regression #12: top_k <= 0 returns [], not scored[:-N]."""
    c = _chunk(0)
    await store.upsert_chunks("d1", [c], [_unit(1, 0, 0)], _fps([c]))
    assert await store.search([1.0, 0.0, 0.0], top_k=-1) == []
    assert await store.search([1.0, 0.0, 0.0], top_k=0) == []


async def test_search_dimension_mismatch(store):
    c = _chunk(0)
    await store.upsert_chunks("d1", [c], [_unit(1, 0, 0)], _fps([c]))
    with pytest.raises(StoreError, match="dimension"):
        await store.search([1.0, 0.0])


async def test_search_ranks_by_similarity_not_insertion(store):
    """Regression #16: insertion order differs from similarity order."""
    cs = [
        _chunk(0, content="low"),
        _chunk(1, content="mid"),
        _chunk(2, content="high"),
    ]
    vecs = [_unit(0, 0, 1), _unit(0, 1, 0), _unit(1, 0, 0)]
    await store.upsert_chunks("d1", cs, vecs, _fps(cs))

    hits = await store.search(_unit(1, 0.1, 0), top_k=3)
    assert [h.ordinal for h in hits] == [2, 1, 0]
    assert hits[0].score > hits[1].score > hits[2].score


async def test_search_top_k_limits(store):
    cs = [_chunk(i, content=f"c{i}") for i in range(5)]
    vecs = [_unit(1, 0, 0)] * 5
    await store.upsert_chunks("d1", cs, vecs, _fps(cs))
    assert len(await store.search(_unit(1, 0, 0), top_k=2)) == 2


async def test_search_result_includes_metadata(store):
    """Regression #13: metadata must flow through to SearchHit."""
    c = _chunk(7, content="x", metadata={"source": "doc.pdf", "chunk_type": "para"})
    await store.upsert_chunks("d1", [c], [_unit(1, 0, 0)], _fps([c]))
    [hit] = await store.search(_unit(1, 0, 0))
    assert isinstance(hit, SearchHit)
    assert hit.metadata == {"source": "doc.pdf", "chunk_type": "para"}


async def test_search_across_docs(store):
    a = _chunk(0, content="a", doc_id="d1")
    b = _chunk(0, content="b", doc_id="d2")
    await store.upsert_chunks("d1", [a], [_unit(1, 0, 0)], _fps([a]))
    await store.upsert_chunks("d2", [b], [_unit(0, 1, 0)], _fps([b]))
    hits = await store.search(_unit(1, 0, 0), top_k=10)
    assert {h.doc_id for h in hits} == {"d1", "d2"}


async def test_search_filters_by_catalog_source_kind(store):
    standard = _chunk(0, content="standard", doc_id="d1", source_id="src-01", source_kind="normative_act")
    reference = _chunk(0, content="reference", doc_id="d2", source_id="src-08", source_kind="reference")
    await store.upsert_chunks("d1", [standard], [_unit(1, 0, 0)], _fps([standard]))
    await store.upsert_chunks("d2", [reference], [_unit(1, 0, 0)], _fps([reference]))

    hits = await store.search(_unit(1, 0, 0), top_k=10, kinds={"normative_act"})
    assert [hit.source_id for hit in hits] == ["src-01"]


# ----------------------------------------------------------- list_docs
async def test_list_docs_counts(store):
    a = _chunk(0, content="a", doc_id="d1")
    b = _chunk(1, content="b", doc_id="d1")
    c = _chunk(0, content="c", doc_id="d2")
    await store.upsert_chunks("d1", [a, b], [_unit(1, 0, 0), _unit(0, 1, 0)], _fps([a, b]))
    await store.upsert_chunks("d2", [c], [_unit(0, 0, 1)], _fps([c]))
    assert await store.list_docs() == [
        {"doc_id": "d1", "chunks": 2},
        {"doc_id": "d2", "chunks": 1},
    ]
