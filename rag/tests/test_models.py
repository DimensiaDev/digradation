"""Tests for config, errors and pydantic models."""

import pytest
from pydantic import ValidationError

from edu_rag import (
    ChunkRecord,
    EmbeddingConfig,
    EmbeddingError,
    RagError,
    SearchHit,
    StoreConfig,
    StoreError,
)


def test_embedding_config_defaults():
    cfg = EmbeddingConfig()
    assert cfg.model == "Embeddings"
    assert cfg.dimension == 1024
    assert cfg.batch_size == 20


def test_embedding_config_frozen():
    cfg = EmbeddingConfig()
    with pytest.raises(Exception):
        cfg.model = "Other"  # frozen dataclass


def test_store_config_defaults():
    cfg = StoreConfig()
    assert cfg.table == "rag_chunks"
    assert cfg.hnsw_m == 16


def test_error_hierarchy():
    assert issubclass(EmbeddingError, RagError)
    assert issubclass(StoreError, RagError)


def test_chunk_record_minimal():
    rec = ChunkRecord(
        doc_id="d1",
        ordinal=0,
        kind="text",
        content="hello",
        content_hash="abc123",
    )
    assert rec.doc_id == "d1"
    assert rec.metadata == {}
    assert rec.section_title is None


def test_chunk_record_requires_fields():
    with pytest.raises(ValidationError):
        ChunkRecord(doc_id="d1")  # missing ordinal, kind, content, content_hash


def test_search_hit():
    hit = SearchHit(
        doc_id="d1",
        ordinal=3,
        kind="table",
        section_title="T",
        page_number=2,
        content="| a | b |",
        score=0.87,
    )
    assert hit.score == 0.87
