"""DocumentIndexer — orchestrates chunk → embed → store with idempotency.

Design notes
------------

**Idempotency.** Every chunk carries a ``content_hash``. On each call,
the indexer reads the existing hashes for the document from the store
and skips chunks whose hash is unchanged. Re-indexing an unchanged
document therefore results in **zero** calls to the embedding provider
and **zero** writes — the second run returns ``indexed=0, skipped=N``.

**Batch granularity.** The indexer slices the "to embed" list into
``EmbeddingConfig.batch_size`` batches. Each batch is an atomic unit:
if ``provider.embed`` or ``store.upsert_chunks`` raises, that batch's
chunks are counted as ``failed`` and the loop proceeds with the next
batch. This gives partial progress on transient API failures instead
of losing the whole document.

**Orphans.** After all batches, ``delete_orphans`` removes chunks whose
``(ordinal, kind)`` is no longer present in the input. Chunks from a
failed batch keep their previous stored values (they were in ``keep``),
so a transient failure never deletes good data.
"""

import logging

from .config import EmbeddingConfig
from .embeddings.base import EmbeddingProvider
from .errors import EmbeddingError, StoreError
from .models import ChunkRecord, IndexResult
from .store.base import VectorStore

logger = logging.getLogger(__name__)


class DocumentIndexer:
    """Idempotent chunk → embedding → store pipeline."""

    def __init__(
        self,
        provider: EmbeddingProvider,
        store: VectorStore,
        config: EmbeddingConfig,
    ) -> None:
        self._provider = provider
        self._store = store
        self._config = config

    async def index(self, doc_id: str, chunks: list[ChunkRecord]) -> IndexResult:
        """Index ``chunks`` for ``doc_id``; return a per-batch summary."""
        result = IndexResult(doc_id=doc_id)

        if not chunks:
            before = len(await self._store.get_existing_hashes(doc_id))
            await self._store.delete_orphans(doc_id, keep=[])
            result.deleted = before
            return result

        existing = await self._store.get_existing_hashes(doc_id)
        to_embed: list[ChunkRecord] = []
        for chunk in chunks:
            key = (chunk.ordinal, chunk.kind)
            if existing.get(key) == chunk.content_hash:
                result.skipped += 1
            else:
                to_embed.append(chunk)

        batch_size = max(1, self._config.batch_size)
        for start in range(0, len(to_embed), batch_size):
            batch = to_embed[start : start + batch_size]
            try:
                vectors = await self._provider.embed([c.content for c in batch])
            except EmbeddingError as exc:
                result.failed += len(batch)
                result.errors.append(f"embed batch {start}: {exc}")
                logger.warning("embedding failed for %s batch %d: %s", doc_id, start, exc)
                continue

            if len(vectors) != len(batch):
                result.failed += len(batch)
                msg = f"provider returned {len(vectors)} vectors for " f"{len(batch)} chunks"
                result.errors.append(f"embed batch {start}: {msg}")
                logger.warning("%s: %s", doc_id, msg)
                continue

            try:
                await self._store.upsert_chunks(doc_id, batch, vectors)
            except StoreError as exc:
                result.failed += len(batch)
                result.errors.append(f"store batch {start}: {exc}")
                logger.warning("store failed for %s batch %d: %s", doc_id, start, exc)
                continue

            result.indexed += len(batch)

        before_delete = len(await self._store.get_existing_hashes(doc_id))
        keep = [(c.ordinal, c.kind) for c in chunks]
        await self._store.delete_orphans(doc_id, keep=keep)
        after_delete = len(await self._store.get_existing_hashes(doc_id))
        result.deleted = before_delete - after_delete

        return result
