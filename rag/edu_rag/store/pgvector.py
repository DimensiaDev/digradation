"""PostgreSQL + pgvector implementation of the vector store.

The store is deliberately table-agnostic: the backend owns the SQLAlchemy model
and passes ``RagChunk.__table__`` here, while ``edu_rag`` stays free of any
backend import. That keeps the dependency direction backend -> edu_rag and lets
the same store be tested against a small throwaway table.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from typing import Any

from sqlalchemy import Table, delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..errors import StoreError
from ..models import ChunkRecord, SearchHit


class PgVectorStore:
    """Vector store backed by a PostgreSQL table with a ``pgvector`` column."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        table: Table,
    ) -> None:
        self._session_factory = session_factory
        self._table = table
        embedding_type = table.c.embedding.type
        dimension = getattr(embedding_type, "dim", None)
        if not isinstance(dimension, int) or dimension <= 0:
            raise StoreError(f"table {table.name!r} has no fixed pgvector dimension")
        self._dimension = dimension

    async def upsert_chunks(
        self,
        doc_id: str,
        chunks: list[ChunkRecord],
        vectors: list[list[float]],
        fingerprints: list[str],
    ) -> None:
        if len(chunks) != len(vectors) or len(chunks) != len(fingerprints):
            raise StoreError(
                f"length mismatch: chunks={len(chunks)}, " f"vectors={len(vectors)}, fingerprints={len(fingerprints)}"
            )
        if not chunks:
            return

        seen: set[tuple[int, str]] = set()
        rows: list[dict[str, Any]] = []
        for chunk, vector, fingerprint in zip(chunks, vectors, fingerprints, strict=True):
            if chunk.doc_id != doc_id:
                raise StoreError(f"chunk.doc_id {chunk.doc_id!r} != doc_id {doc_id!r}")
            key = (chunk.ordinal, chunk.kind)
            if key in seen:
                raise StoreError(f"duplicate key {key} for doc {doc_id!r}; deduplicate chunks before upsert")
            seen.add(key)
            if len(vector) != self._dimension:
                raise StoreError(f"vector dimension {len(vector)} != store dimension {self._dimension}")
            rows.append(
                {
                    "doc_id": doc_id,
                    "ordinal": chunk.ordinal,
                    "kind": chunk.kind,
                    "source_id": chunk.source_id,
                    "source_kind": chunk.source_kind,
                    "section_title": chunk.section_title,
                    "page_number": chunk.page_number,
                    "content": chunk.content,
                    "content_hash": chunk.content_hash,
                    "metadata": chunk.metadata,
                    "embedding": vector,
                    "fingerprint": fingerprint,
                }
            )

        statement = pg_insert(self._table).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=[self._table.c.doc_id, self._table.c.ordinal, self._table.c.kind],
            set_={
                "source_id": statement.excluded.source_id,
                "source_kind": statement.excluded.source_kind,
                "section_title": statement.excluded.section_title,
                "page_number": statement.excluded.page_number,
                "content": statement.excluded.content,
                "content_hash": statement.excluded.content_hash,
                "metadata": statement.excluded.metadata,
                "embedding": statement.excluded.embedding,
                "fingerprint": statement.excluded.fingerprint,
                "updated_at": func.now(),
            },
        )
        async with self._session_factory() as session:
            await session.execute(statement)
            await session.commit()

    async def delete_orphans(self, doc_id: str, keep: Iterable[tuple[int, str]]) -> None:
        statement = delete(self._table).where(self._table.c.doc_id == doc_id)
        keep_list = list(keep)
        if keep_list:
            statement = statement.where(tuple_(self._table.c.ordinal, self._table.c.kind).not_in(keep_list))
        async with self._session_factory() as session:
            await session.execute(statement)
            await session.commit()

    async def get_fingerprints(self, doc_id: str) -> dict[tuple[int, str], str]:
        statement = select(
            self._table.c.ordinal,
            self._table.c.kind,
            self._table.c.fingerprint,
        ).where(self._table.c.doc_id == doc_id)
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).all()
        return {(int(row.ordinal), str(row.kind)): str(row.fingerprint) for row in rows}

    async def search(
        self,
        query_vec: list[float],
        top_k: int = 10,
        *,
        kinds: Collection[str] | None = None,
    ) -> list[SearchHit]:
        if top_k <= 0:
            return []
        if len(query_vec) != self._dimension:
            raise StoreError(f"query vector dimension {len(query_vec)} " f"!= store dimension {self._dimension}")

        distance = self._table.c.embedding.cosine_distance(query_vec)
        score = (1 - distance).label("score")
        statement = select(self._table, score).order_by(distance).limit(top_k)
        if kinds is not None:
            statement = statement.where(self._table.c.source_kind.in_(list(kinds)))
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).all()

        return [
            SearchHit(
                doc_id=str(row.doc_id),
                ordinal=int(row.ordinal),
                kind=str(row.kind),
                source_id=row.source_id,
                source_kind=row.source_kind,
                section_title=row.section_title,
                page_number=row.page_number,
                content=str(row.content),
                score=float(row.score),
                metadata=row._mapping["metadata"] or {},
            )
            for row in rows
        ]

    async def list_docs(self) -> list[dict]:
        statement = (
            select(self._table.c.doc_id, func.count().label("chunks"))
            .group_by(self._table.c.doc_id)
            .order_by(self._table.c.doc_id)
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).all()
        return [{"doc_id": str(row.doc_id), "chunks": int(row.chunks)} for row in rows]
