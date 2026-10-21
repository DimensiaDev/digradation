"""Abstract contract for vector stores."""

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from ..models import ChunkRecord, SearchHit


@runtime_checkable
class VectorStore(Protocol):
    """Protocol for vector stores.

    Storage-agnostic: the same protocol is implemented by
    ``InMemoryVectorStore`` (MVP, tests) and ``PgVectorStore`` (production).
    """

    async def upsert_chunks(
        self,
        doc_id: str,
        chunks: list[ChunkRecord],
        vectors: list[list[float]],
        fingerprints: list[str],
    ) -> None:
        """Insert or update chunks with their embeddings and fingerprints.

        - ``vectors[i]`` is the embedding of ``chunks[i]``.
        - ``fingerprints[i]`` is ``chunks[i].fingerprint(model, dimension)``
          — the store records it verbatim so that ``get_fingerprints``
          can be served on subsequent runs.

        Implementations must validate everything *before* mutating state:
        length mismatch, ``chunk.doc_id != doc_id``, duplicate
        ``(ordinal, kind)`` keys, non-positive or inconsistent vector
        dimensions. A failed call leaves the store unchanged.
        """
        ...

    async def delete_orphans(
        self,
        doc_id: str,
        keep: Iterable[tuple[int, str]],
    ) -> None: ...

    async def get_fingerprints(self, doc_id: str) -> dict[tuple[int, str], str]:
        """Return ``{(ordinal, kind): fingerprint}`` for every stored chunk."""
        ...

    async def search(
        self,
        query_vec: list[float],
        top_k: int = 10,
        filter: dict | None = None,
    ) -> list[SearchHit]:
        """Return up to ``top_k`` hits sorted by descending similarity.

        ``filter`` is a dict of metadata key → expected value, applied to
        ``chunk.metadata`` before ranking. ``None`` disables filtering; an
        empty dict is equivalent to ``None``.

        ``top_k <= 0`` returns an empty list.
        """
        ...

    async def list_docs(self) -> list[dict]:
        """Return one summary dict per indexed document."""
        ...
