"""DocumentIndexer — orchestrates chunk → embed → store with idempotency.

Design notes
------------

**Fingerprint-based idempotency.** Each chunk is compared against the
store via ``ChunkRecord.fingerprint(model, dimension)`` — a hash that
covers content, location fields, metadata, and the embedding model +
dimension. Change any of these and the chunk is re-embedded. This
covers model/dimension migrations, metadata updates, and content
changes with a single comparison.

**Batch granularity.** The "to embed" list is sliced into batches of
``EmbeddingConfig.batch_size``. Each batch is an atomic unit: any
failure (embed, wrong count, wrong dimension, store) marks the batch as
failed and the loop proceeds. Previous versions of the affected chunks
remain in the store, so transient failures never delete good data.

**Defensive error handling.** Any exception from provider or store —
not just ``EmbeddingError`` / ``StoreError`` — is captured into
``IndexResult.errors``. One broken batch never aborts the whole run.

**Concurrency.** This class does not serialise concurrent ``index()``
calls for the same ``doc_id``. Two overlapping calls can race. In a
single-process task setting this is fine; if you need parallel indexing,
wrap the call in a per-document lock (Redis / filelock) at the caller.
"""

import logging

from .config import EmbeddingConfig
from .embeddings.base import EmbeddingProvider
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
        if provider.dimension != config.dimension:
            raise ValueError(
                f"provider.dimension ({provider.dimension}) "
                f"!= config.dimension ({config.dimension}); "
                "indexing with mismatched dimensions would silently corrupt the store"
            )
        self._provider = provider
        self._store = store
        self._config = config

    async def index(self, doc_id: str, chunks: list[ChunkRecord]) -> IndexResult:
        """Index ``chunks`` for ``doc_id``; return a per-batch summary."""
        result = IndexResult(doc_id=doc_id)

        for chunk in chunks:
            if chunk.doc_id != doc_id:
                raise ValueError(f"chunk.doc_id {chunk.doc_id!r} != doc_id {doc_id!r}")
        keys = [(c.ordinal, c.kind) for c in chunks]
        if len(keys) != len(set(keys)):
            raise ValueError(f"duplicate (ordinal, kind) keys in input for doc {doc_id!r}")

        if not chunks:
            before = len(await self._store.get_fingerprints(doc_id))
            await self._store.delete_orphans(doc_id, keep=[])
            result.deleted = before
            return result

        existing = await self._store.get_fingerprints(doc_id)
        model = self._config.model
        dim = self._config.dimension

        to_embed: list[tuple[ChunkRecord, str]] = []
        for chunk in chunks:
            key = (chunk.ordinal, chunk.kind)
            fp = chunk.fingerprint(model, dim)
            if existing.get(key) == fp:
                result.skipped += 1
            else:
                to_embed.append((chunk, fp))

        batch_size = max(1, self._config.batch_size)
        for start in range(0, len(to_embed), batch_size):
            batch = to_embed[start : start + batch_size]
            batch_chunks = [c for c, _ in batch]
            batch_fps = [fp for _, fp in batch]

            try:
                vectors = await self._provider.embed([c.content for c in batch_chunks])
            except Exception as exc:
                result.failed += len(batch_chunks)
                result.errors.append(f"embed batch {start}: {type(exc).__name__}: {exc}")
                logger.warning("embedding failed for %s batch %d: %s", doc_id, start, exc)
                continue

            if len(vectors) != len(batch_chunks):
                result.failed += len(batch_chunks)
                msg = f"provider returned {len(vectors)} vectors " f"for {len(batch_chunks)} chunks"
                result.errors.append(f"embed batch {start}: {msg}")
                logger.warning("%s: %s", doc_id, msg)
                continue

            bad = [i for i, v in enumerate(vectors) if len(v) != dim]
            if bad:
                result.failed += len(batch_chunks)
                msg = f"provider returned vectors with wrong dimension " f"(expected {dim}) at indices {bad}"
                result.errors.append(f"embed batch {start}: {msg}")
                logger.warning("%s: %s", doc_id, msg)
                continue

            try:
                await self._store.upsert_chunks(doc_id, batch_chunks, vectors, batch_fps)
            except Exception as exc:
                result.failed += len(batch_chunks)
                result.errors.append(f"store batch {start}: {type(exc).__name__}: {exc}")
                logger.warning("store failed for %s batch %d: %s", doc_id, start, exc)
                continue

            result.indexed += len(batch_chunks)

        try:
            before_delete = len(await self._store.get_fingerprints(doc_id))
            keep = [(c.ordinal, c.kind) for c in chunks]
            await self._store.delete_orphans(doc_id, keep=keep)
            after_delete = len(await self._store.get_fingerprints(doc_id))
            result.deleted = before_delete - after_delete
        except Exception as exc:
            result.errors.append(f"prune orphans: {type(exc).__name__}: {exc}")
            logger.warning("prune failed for %s: %s", doc_id, exc)

        return result
