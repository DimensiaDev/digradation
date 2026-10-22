"""Integration tests for the pgvector-backed store.

These use a real pgvector container because the SQLAlchemy vector operators,
JSONB mapping, and upsert conflict clause cannot be exercised by the in-memory
store. The table is deliberately local to the test: ``edu_rag`` must not import
the backend's ORM model.
"""

from __future__ import annotations

import hashlib

import pytest
import pytest_asyncio
from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from edu_rag import ChunkRecord, PgVectorStore, StoreError

DIMENSION = 4

metadata = MetaData()
rag_chunks = Table(
    "rag_chunks_test",
    metadata,
    Column("doc_id", String(64), primary_key=True),
    Column("ordinal", Integer, primary_key=True),
    Column("kind", String(32), primary_key=True),
    Column("source_id", String(16), nullable=True),
    Column("source_kind", String(32), nullable=True),
    Column("section_title", Text, nullable=True),
    Column("page_number", Integer, nullable=True),
    Column("content", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("metadata", JSONB, nullable=False),
    Column("embedding", HALFVEC(DIMENSION), nullable=False),
    Column("fingerprint", String(64), nullable=False),
    Column("updated_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _chunk(
    ordinal: int,
    *,
    content: str = "text",
    doc_id: str = "doc-1",
    source_id: str | None = "src-01",
    source_kind: str | None = "normative_act",
    kind: str = "text",
) -> ChunkRecord:
    return ChunkRecord(
        doc_id=doc_id,
        ordinal=ordinal,
        kind=kind,
        source_id=source_id,
        source_kind=source_kind,
        section_title=f"Section {ordinal}",
        page_number=ordinal + 1,
        content=content,
        content_hash=_hash(content),
        metadata={"source_id": source_id},
    )


@pytest.fixture(scope="session")
def pgvector_url():
    with PostgresContainer("pgvector/pgvector:pg18", driver="asyncpg") as postgres:
        yield postgres.get_connection_url()


@pytest_asyncio.fixture(scope="session")
async def engine(pgvector_url):
    engine = create_async_engine(pgvector_url)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture()
async def store(engine):
    async with engine.begin() as conn:
        await conn.execute(rag_chunks.delete())
    return PgVectorStore(async_sessionmaker(engine, expire_on_commit=False), rag_chunks)


async def test_upsert_and_search_round_trip(store: PgVectorStore) -> None:
    chunks = [_chunk(0, content="relevant"), _chunk(1, content="less relevant")]
    vectors = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]]
    fingerprints = [chunk.fingerprint("test-model", DIMENSION) for chunk in chunks]

    await store.upsert_chunks("doc-1", chunks, vectors, fingerprints)
    hits = await store.search([1.0, 0.0, 0.0, 0.0], top_k=10)

    assert [hit.ordinal for hit in hits] == [0, 1]
    assert hits[0].source_id == "src-01"
    assert hits[0].source_kind == "normative_act"
    assert hits[0].page_number == 1
    assert hits[0].metadata == {"source_id": "src-01"}
    assert hits[0].score > hits[1].score


async def test_search_filters_by_source_kind(store: PgVectorStore) -> None:
    standard = _chunk(0, content="standard", doc_id="doc-1")
    reference = _chunk(0, content="reference", doc_id="doc-2", source_id="src-08", source_kind="reference")
    for chunk in (standard, reference):
        await store.upsert_chunks(
            chunk.doc_id,
            [chunk],
            [[1.0, 0.0, 0.0, 0.0]],
            [chunk.fingerprint("test-model", DIMENSION)],
        )

    hits = await store.search([1.0, 0.0, 0.0, 0.0], top_k=10, kinds={"normative_act"})
    assert [hit.doc_id for hit in hits] == ["doc-1"]


async def test_upsert_is_idempotent_by_key(store: PgVectorStore) -> None:
    chunk = _chunk(0, content="first")
    await store.upsert_chunks("doc-1", [chunk], [[1.0, 0.0, 0.0, 0.0]], [chunk.fingerprint("m", DIMENSION)])

    updated = _chunk(0, content="updated")
    await store.upsert_chunks("doc-1", [updated], [[0.0, 1.0, 0.0, 0.0]], [updated.fingerprint("m", DIMENSION)])

    hits = await store.search([0.0, 1.0, 0.0, 0.0], top_k=10)
    assert len(hits) == 1
    assert hits[0].content == "updated"
    assert await store.get_fingerprints("doc-1") == {(0, "text"): updated.fingerprint("m", DIMENSION)}


async def test_delete_orphans_keeps_requested_keys(store: PgVectorStore) -> None:
    chunks = [_chunk(0), _chunk(1), _chunk(2)]
    await store.upsert_chunks(
        "doc-1",
        chunks,
        [[1.0, 0.0, 0.0, 0.0]] * 3,
        [chunk.fingerprint("m", DIMENSION) for chunk in chunks],
    )

    await store.delete_orphans("doc-1", keep=[(0, "text"), (2, "text")])
    assert set(await store.get_fingerprints("doc-1")) == {(0, "text"), (2, "text")}

    await store.delete_orphans("doc-1", keep=[])
    assert await store.get_fingerprints("doc-1") == {}


async def test_list_docs_counts_chunks(store: PgVectorStore) -> None:
    chunks = [_chunk(0), _chunk(1)]
    await store.upsert_chunks(
        "doc-1",
        chunks,
        [[1.0, 0.0, 0.0, 0.0]] * 2,
        [chunk.fingerprint("m", DIMENSION) for chunk in chunks],
    )
    assert await store.list_docs() == [{"doc_id": "doc-1", "chunks": 2}]


async def test_dimension_mismatch_is_refused(store: PgVectorStore) -> None:
    chunk = _chunk(0)
    with pytest.raises(StoreError, match="dimension"):
        await store.upsert_chunks("doc-1", [chunk], [[1.0, 0.0]], [chunk.fingerprint("m", DIMENSION)])
