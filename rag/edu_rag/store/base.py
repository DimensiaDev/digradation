"""Abstract contract for vector stores."""

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from ..models import ChunkRecord, SearchHit


@runtime_checkable
class VectorStore(Protocol):
    """Protocol for vector stores.

    The interface is intentionally storage-agnostic: the same protocol is
    implemented by ``InMemoryVectorStore`` (MVP, tests) and
    ``PgVectorStore`` (production). The indexer and the search endpoint
    depend only on this protocol, so swapping backends does not touch
    callers.

    Idempotency contract: ``upsert_chunks`` may be called repeatedly with
    the same ``(doc_id, ordinal, kind)`` keys; the last call wins. The
    caller (indexer) decides which chunks to re-embed based on
    ``get_existing_hashes``, so re-indexing an unchanged document results
    in no writes.
    """

    async def upsert_chunks(
        self,
        doc_id: str,
        chunks: list[ChunkRecord],
        vectors: list[list[float]],
    ) -> None:
        """Insert or update chunks and their embeddings.

        ``vectors[i]`` must be the embedding of ``chunks[i]``.
        ``chunks[i].doc_id`` must equal ``doc_id`` for every chunk:
        the parameter is the partition key, the field is part of the
        record — mismatches are a caller bug and must raise ``StoreError``.
        Implementations also raise ``StoreError`` on length mismatch or
        vector-dimension mismatch against the latched store dimension.
        """
        ...

    async def delete_orphans(
        self,
        doc_id: str,
        keep: Iterable[tuple[int, str]],
    ) -> None:
        """Delete chunks of ``doc_id`` whose ``(ordinal, kind)`` is not in ``keep``."""
        ...

    async def get_existing_hashes(self, doc_id: str) -> dict[tuple[int, str], str]:
        """Return ``{(ordinal, kind): content_hash}`` for every stored chunk of ``doc_id``."""
        ...

    async def search(
        self,
        query_vec: list[float],
        top_k: int = 10,
    ) -> list[SearchHit]:
        """Return up to ``top_k`` hits, sorted by descending similarity."""
        ...

    async def list_docs(self) -> list[dict]:
        """Return one summary dict per indexed document."""
        ...
