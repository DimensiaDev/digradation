"""Tests for config, errors and pydantic models."""

import hashlib
from dataclasses import FrozenInstanceError

import pytest
from pydantic import ValidationError

from edu_rag import (
    ChunkRecord,
    EmbeddingConfig,
    EmbeddingError,
    IndexResult,
    RagError,
    SearchHit,
    StoreConfig,
    StoreError,
)


def _h(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# -------------------------------------------------------------- config
def test_embedding_config_defaults():
    cfg = EmbeddingConfig()
    assert cfg.model == "Embeddings"
    assert cfg.dimension == 1024
    assert cfg.batch_size == 20


def test_embedding_config_frozen():
    cfg = EmbeddingConfig()
    with pytest.raises(FrozenInstanceError):
        cfg.model = "Other"


def test_embedding_config_rejects_zero_dimension():
    with pytest.raises(ValueError, match="dimension"):
        EmbeddingConfig(dimension=0)


def test_embedding_config_rejects_negative_batch():
    with pytest.raises(ValueError, match="batch_size"):
        EmbeddingConfig(batch_size=0)


def test_embedding_config_rejects_bad_timeout():
    with pytest.raises(ValueError, match="timeout_s"):
        EmbeddingConfig(timeout_s=0.0)


def test_store_config_defaults():
    cfg = StoreConfig()
    assert cfg.table == "rag_chunks"
    assert cfg.hnsw_m == 16


def test_store_config_frozen():
    cfg = StoreConfig()
    with pytest.raises(FrozenInstanceError):
        cfg.table = "other"


# -------------------------------------------------------------- errors
def test_error_hierarchy():
    assert issubclass(EmbeddingError, RagError)
    assert issubclass(StoreError, RagError)


# -------------------------------------------------------------- chunk
def test_chunk_record_minimal():
    rec = ChunkRecord(
        doc_id="d1",
        ordinal=0,
        kind="text",
        content="hello",
        content_hash=_h("hello"),
    )
    assert rec.doc_id == "d1"
    assert rec.metadata == {}
    assert rec.section_title is None


def test_chunk_record_requires_fields():
    with pytest.raises(ValidationError):
        ChunkRecord(doc_id="d1")


def test_chunk_record_rejects_inconsistent_hash():
    with pytest.raises(ValidationError, match="content_hash"):
        ChunkRecord(
            doc_id="d1",
            ordinal=0,
            kind="text",
            content="new content",
            content_hash=_h("old content"),
        )


def test_chunk_record_fingerprint_stable():
    rec = ChunkRecord(
        doc_id="d1",
        ordinal=0,
        kind="text",
        content="hello",
        content_hash=_h("hello"),
        section_title="Sec",
        page_number=3,
    )
    a = rec.fingerprint("Embeddings", 1024)
    b = rec.fingerprint("Embeddings", 1024)
    assert a == b


def test_chunk_record_fingerprint_changes_with_model():
    rec = ChunkRecord(
        doc_id="d1",
        ordinal=0,
        kind="text",
        content="hello",
        content_hash=_h("hello"),
    )
    a = rec.fingerprint("Embeddings", 1024)
    b = rec.fingerprint("Embeddings-2", 1024)
    c = rec.fingerprint("EmbeddingsGigaR", 2560)
    assert a != b
    assert a != c


def test_chunk_record_fingerprint_changes_with_metadata():
    base = {
        "doc_id": "d1",
        "ordinal": 0,
        "kind": "text",
        "content": "hello",
        "content_hash": _h("hello"),
    }
    a = ChunkRecord(**base, section_title="A", page_number=1).fingerprint("E", 1024)
    b = ChunkRecord(**base, section_title="B", page_number=1).fingerprint("E", 1024)
    c = ChunkRecord(**base, section_title="A", page_number=2).fingerprint("E", 1024)
    assert a != b
    assert a != c


# -------------------------------------------------------- SearchHit
def test_search_hit_has_metadata():
    hit = SearchHit(
        doc_id="d1",
        ordinal=3,
        kind="table",
        section_title="T",
        page_number=2,
        content="| a | b |",
        score=0.87,
        metadata={"source": "x.pdf"},
    )
    assert hit.score == 0.87
    assert hit.metadata == {"source": "x.pdf"}


# ------------------------------------------------------ IndexResult
def test_index_result_total():
    r = IndexResult(doc_id="d1", indexed=2, skipped=3, failed=1)
    assert r.total == 6


def test_index_result_total_only_indexed_is_insufficient():
    """Regression: total must sum all three, not just indexed."""
    r = IndexResult(doc_id="d1", indexed=0, skipped=5, failed=0)
    assert r.total == 5
    r2 = IndexResult(doc_id="d1", indexed=0, skipped=0, failed=7)
    assert r2.total == 7


def test_embedding_config_rejects_empty_model():
    with pytest.raises(ValueError, match="model"):
        EmbeddingConfig(model="")


def test_embedding_config_rejects_nonpositive_max_tokens():
    with pytest.raises(ValueError, match="max_tokens"):
        EmbeddingConfig(max_tokens=0)


def test_embedding_config_rejects_negative_retries():
    with pytest.raises(ValueError, match="retries"):
        EmbeddingConfig(retries=-1)
