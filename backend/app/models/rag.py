"""pgvector-backed chunks of the curated source corpus.

One row is one retrievable fragment. ``(doc_id, ordinal, kind)`` is the key the
indexer uses for idempotency; ``source_id`` is the catalog id the programme
schema cites and ``source_kind`` is denormalised so retrieval can filter by it
without a join against the JSON catalog.
"""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import DateTime, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.rag.config import EMBEDDING_DIMENSION

MAX_DOC_ID_LENGTH = 64
MAX_SOURCE_ID_LENGTH = 16
MAX_KIND_LENGTH = 32
MAX_HASH_LENGTH = 64


class RagChunk(Base):
    __tablename__ = "rag_chunks"

    doc_id: Mapped[str] = mapped_column(String(MAX_DOC_ID_LENGTH), primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(MAX_KIND_LENGTH), primary_key=True)
    source_id: Mapped[str | None] = mapped_column(String(MAX_SOURCE_ID_LENGTH), nullable=True)
    source_kind: Mapped[str | None] = mapped_column(String(MAX_KIND_LENGTH), nullable=True)
    section_title: Mapped[str | None] = mapped_column(Text(), nullable=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content: Mapped[str] = mapped_column(Text(), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(MAX_HASH_LENGTH), nullable=False)
    chunk_metadata: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    embedding: Mapped[list[float]] = mapped_column(HALFVEC(EMBEDDING_DIMENSION), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(MAX_HASH_LENGTH), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index("ix_rag_chunks_source", "source_id", "source_kind"),
        Index(
            "ix_rag_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "halfvec_cosine_ops"},
        ),
    )
