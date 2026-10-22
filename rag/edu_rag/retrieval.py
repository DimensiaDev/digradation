"""Query-side retrieval on top of a vector store.

The store returns nearest neighbours; this layer applies the policy the agent
needs: a similarity floor, a diversity cap per source, and the catalog ``kinds``
filter. It deliberately does not know the catalogue itself — the caller maps a
``SearchHit.source_id`` to the citation record.
"""

from __future__ import annotations

from collections.abc import Collection

from .embeddings.base import EmbeddingProvider
from .models import SearchHit
from .store.base import VectorStore


class RagRetriever:
    """Embed one query and select the hits the programme stage may cite."""

    def __init__(
        self,
        provider: EmbeddingProvider,
        store: VectorStore,
        *,
        oversample: int = 4,
        min_score: float = 0.0,
        max_per_source: int = 3,
    ) -> None:
        if oversample < 1:
            raise ValueError("oversample must be >= 1")
        if max_per_source < 1:
            raise ValueError("max_per_source must be >= 1")
        self._provider = provider
        self._store = store
        self._oversample = oversample
        self._min_score = min_score
        self._max_per_source = max_per_source

    async def retrieve(
        self,
        query: str,
        *,
        kinds: Collection[str] | None = None,
        limit: int = 5,
    ) -> list[SearchHit]:
        if not query.strip() or limit <= 0:
            return []
        vectors = await self._provider.embed([query])
        if not vectors:
            return []
        hits = await self._store.search(
            vectors[0],
            top_k=max(limit * self._oversample, limit),
            kinds=kinds,
        )
        selected: list[SearchHit] = []
        per_source: dict[str, int] = {}
        for hit in hits:
            if hit.score < self._min_score:
                continue
            key = hit.source_id or hit.doc_id
            if per_source.get(key, 0) >= self._max_per_source:
                continue
            selected.append(hit)
            per_source[key] = per_source.get(key, 0) + 1
            if len(selected) >= limit:
                break
        return selected
