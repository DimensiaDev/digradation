"""In-memory vector store — MVP fallback and test double.

No external dependencies (no numpy, no sqlalchemy). Cosine similarity is
computed in pure Python; good enough for thousands of chunks, which is
the scale of the current demo dataset. Production uses PgVectorStore.
"""

import copy
import math
from collections.abc import Iterable
from dataclasses import dataclass

from ..errors import StoreError
from ..models import ChunkRecord, SearchHit


@dataclass
class _Entry:
    chunk: ChunkRecord
    vector: list[float]
    fingerprint: str


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class InMemoryVectorStore:
    """In-memory implementation of :class:`VectorStore`.

    Guarantees:

    - **Atomic upsert**: all inputs are validated before any state is
      mutated. A failed call leaves both the store and the latched
      dimension unchanged.
    - **Defensive copies**: stored chunks and vectors are deep-copied,
      so later mutations by the caller cannot silently rewrite what the
      store returns.
    - **Dimension latching**: the first successful upsert fixes the
      dimension; any later mismatch raises ``StoreError``.
    - **Idempotency**: writing the same ``(doc_id, ordinal, kind)`` key
      twice overwrites the previous entry.
    """

    def __init__(self) -> None:
        self._docs: dict[str, dict[tuple[int, str], _Entry]] = {}
        self._dimension: int | None = None

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
        for chunk in chunks:
            if chunk.doc_id != doc_id:
                raise StoreError(f"chunk.doc_id {chunk.doc_id!r} != doc_id {doc_id!r}")
            key = (chunk.ordinal, chunk.kind)
            if key in seen:
                raise StoreError(f"duplicate key {key} for doc {doc_id!r}; " "deduplicate chunks before upsert")
            seen.add(key)

        first_dim = len(vectors[0])
        if first_dim == 0:
            raise StoreError("embedding vectors must not be empty")

        expected_dim = self._dimension if self._dimension is not None else first_dim
        for i, vec in enumerate(vectors):
            if len(vec) != expected_dim:
                raise StoreError(f"vector[{i}] dimension {len(vec)} " f"!= store dimension {expected_dim}")

        if self._dimension is None:
            self._dimension = first_dim

        bucket = self._docs.setdefault(doc_id, {})
        for chunk, vec, fp in zip(chunks, vectors, fingerprints, strict=True):
            bucket[(chunk.ordinal, chunk.kind)] = _Entry(
                chunk=chunk.model_copy(deep=True),
                vector=list(vec),
                fingerprint=fp,
            )

    async def delete_orphans(
        self,
        doc_id: str,
        keep: Iterable[tuple[int, str]],
    ) -> None:
        bucket = self._docs.get(doc_id)
        if not bucket:
            return
        keep_set = set(keep)
        for key in list(bucket.keys()):
            if key not in keep_set:
                del bucket[key]
        if not bucket:
            del self._docs[doc_id]

    async def get_fingerprints(self, doc_id: str) -> dict[tuple[int, str], str]:
        bucket = self._docs.get(doc_id, {})
        return {key: entry.fingerprint for key, entry in bucket.items()}

    async def search(
        self,
        query_vec: list[float],
        top_k: int = 10,
    ) -> list[SearchHit]:
        if top_k <= 0:
            return []
        if not self._docs or not query_vec or self._dimension is None:
            return []
        if len(query_vec) != self._dimension:
            raise StoreError(f"query vector dimension {len(query_vec)} " f"!= store dimension {self._dimension}")

        scored: list[tuple[float, _Entry]] = []
        for bucket in self._docs.values():
            for entry in bucket.values():
                scored.append((_cosine(query_vec, entry.vector), entry))

        scored.sort(key=lambda item: item[0], reverse=True)

        return [
            SearchHit(
                doc_id=entry.chunk.doc_id,
                ordinal=entry.chunk.ordinal,
                kind=entry.chunk.kind,
                section_title=entry.chunk.section_title,
                page_number=entry.chunk.page_number,
                content=entry.chunk.content,
                score=score,
                metadata=copy.deepcopy(entry.chunk.metadata),
            )
            for score, entry in scored[:top_k]
        ]

    async def list_docs(self) -> list[dict]:
        return [{"doc_id": doc_id, "chunks": len(bucket)} for doc_id, bucket in sorted(self._docs.items())]
