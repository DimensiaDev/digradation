"""In-memory vector store — MVP fallback and test double.

Uses numpy for cosine similarity and for the ``search`` hot path: all
stored embeddings are stacked into an ``(N, D)`` matrix and scored with a
single ``matrix @ query`` matmul. For the current dataset (~250 chunks)
this is sub-millisecond; it also mirrors what ``pgvector`` does under
the hood.
"""

import copy
from collections.abc import Collection, Iterable
from dataclasses import dataclass

import numpy as np

from ..errors import StoreError
from ..models import ChunkRecord, SearchHit


@dataclass
class _Entry:
    chunk: ChunkRecord
    vector: np.ndarray
    fingerprint: str


def _cosine(a: list[float] | np.ndarray, b: list[float] | np.ndarray) -> float:
    """Cosine similarity between two equal-length vectors.

    Reference implementation for a single pair. ``search`` uses a
    vectorised matmul over the whole matrix, but this helper is kept for
    direct testing and for callers that need a one-off score.
    """
    va = np.asarray(a, dtype=np.float64).ravel()
    vb = np.asarray(b, dtype=np.float64).ravel()
    if va.shape != vb.shape:
        return 0.0
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


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
                vector=np.array(vec, dtype=np.float64),
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
        *,
        kinds: Collection[str] | None = None,
    ) -> list[SearchHit]:
        if top_k <= 0:
            return []
        if not self._docs or not query_vec or self._dimension is None:
            return []
        if len(query_vec) != self._dimension:
            raise StoreError(f"query vector dimension {len(query_vec)} " f"!= store dimension {self._dimension}")

        q = np.asarray(query_vec, dtype=np.float64)
        q_norm = float(np.linalg.norm(q))
        if q_norm == 0.0:
            return []

        entries: list[_Entry] = []
        for bucket in self._docs.values():
            entries.extend(bucket.values())
        if kinds is not None:
            wanted = set(kinds)
            entries = [entry for entry in entries if entry.chunk.source_kind in wanted]
        if not entries:
            return []

        matrix = np.stack([e.vector for e in entries])
        norms = np.linalg.norm(matrix, axis=1)
        denom = norms * q_norm

        scores = np.zeros(len(entries), dtype=np.float64)
        nz = denom > 0
        scores[nz] = (matrix[nz] @ q) / denom[nz]

        order = np.argsort(-scores)[:top_k]

        return [
            SearchHit(
                doc_id=entries[i].chunk.doc_id,
                ordinal=entries[i].chunk.ordinal,
                kind=entries[i].chunk.kind,
                source_id=entries[i].chunk.source_id,
                source_kind=entries[i].chunk.source_kind,
                section_title=entries[i].chunk.section_title,
                page_number=entries[i].chunk.page_number,
                content=entries[i].chunk.content,
                score=float(scores[i]),
                metadata=copy.deepcopy(entries[i].chunk.metadata),
            )
            for i in order
        ]

    async def list_docs(self) -> list[dict]:
        return [{"doc_id": doc_id, "chunks": len(bucket)} for doc_id, bucket in sorted(self._docs.items())]
