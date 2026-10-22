"""The source catalog and the RAG search the agent tools call.

The curated catalog supplies stable ``src-NN`` citation ids; the pgvector index
supplies the fragments behind them. This module is the seam between the two and
is the only place the rest of ``app.agentic`` touches either.

RAG is feature-flagged: with ``RAG_ENABLED`` unset the search reports nothing and
the pipeline behaves exactly as it did before the index existed. Enabling it
without a reachable embedding provider raises :class:`RagUnavailable`, which the
tool turns into a model-visible note instead of failing the stage.
"""

from __future__ import annotations

import os
from collections.abc import Collection
from functools import lru_cache

import structlog.stdlib
from edu_rag import PgVectorStore, RagRetriever
from pydantic import BaseModel, Field

from app.agentic.models import Source, SourceKind
from app.database import get_session_maker
from app.models.rag import RagChunk
from app.providers.containers import get_embedding_provider
from app.rag.catalog import canonical_source_id, catalog_by_id, get_catalog_entry
from app.rag.config import (
    DEFAULT_MAX_PER_SOURCE,
    DEFAULT_MIN_SCORE,
    DEFAULT_OVERSAMPLE,
    DEFAULT_TOP_K,
    is_rag_enabled,
)

log = structlog.stdlib.get_logger(__name__)


class RagUnavailable(RuntimeError):
    """The index or its embedding provider cannot answer this query."""


class SourceFragment(BaseModel):
    """One retrieved fragment with the document it belongs to."""

    fragment_id: str
    source: Source
    section: str
    text: str
    page: int | None = None
    trust: float = Field(default=0.5, ge=0.0, le=1.0)
    score: float = 0.0


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return default if raw is None or not raw.strip() else int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return default if raw is None or not raw.strip() else float(raw)


@lru_cache
def get_retriever() -> RagRetriever | None:
    """Build the retriever once, or return ``None`` while RAG is disabled.

    Construction is lazy because importing a worker must not require an
    embedding provider, and because the GigaChat-native scope has no embedding
    endpoint at all. A provider error is surfaced as ``RagUnavailable`` so the
    stage can continue without citations rather than dying.
    """
    if not is_rag_enabled():
        return None
    try:
        provider = get_embedding_provider()
        store = PgVectorStore(get_session_maker(), RagChunk.__table__)
        return RagRetriever(
            provider,
            store,
            oversample=_env_int("RAG_OVERSAMPLE", DEFAULT_OVERSAMPLE),
            min_score=_env_float("RAG_MIN_SCORE", DEFAULT_MIN_SCORE),
            max_per_source=_env_int("RAG_MAX_PER_SOURCE", DEFAULT_MAX_PER_SOURCE),
        )
    except Exception as error:  # any construction failure means "no search"
        raise RagUnavailable(str(error)) from error


def clear_retriever_cache() -> None:
    """Drop the cached retriever; tests toggle ``RAG_ENABLED`` between cases."""
    clear = getattr(get_retriever, "cache_clear", None)
    if clear is not None:
        clear()


def get_source(source_id: str) -> Source | None:
    """Return the canonical source record with this id, if the catalog has one."""
    try:
        entry = get_catalog_entry(source_id)
    except Exception as error:  # a broken catalog is a missing source, not a crash
        log.error("source_catalog_failed", error=str(error))
        return None
    return entry.to_source() if entry is not None else None


async def search(
    query: str,
    *,
    kinds: Collection[SourceKind] | None = None,
    limit: int = DEFAULT_TOP_K,
) -> list[SourceFragment]:
    """Return up to ``limit`` indexed fragments most relevant to the query.

    ``RagUnavailable`` is deliberately allowed through: the tool handler turns
    it into a note, while a generic tool failure would abort the stage. An empty
    list means the search worked and found nothing.
    """
    if not query.strip() or limit <= 0:
        return []
    retriever = get_retriever()
    if retriever is None:
        return []

    try:
        hits = await retriever.retrieve(
            query,
            kinds={kind.value for kind in kinds} if kinds is not None else None,
            limit=min(limit, 10),
        )
    except RagUnavailable:
        raise
    except Exception as error:  # provider, database, and pgvector errors all mean "unavailable"
        log.warning("rag_search_failed", error=str(error))
        raise RagUnavailable(str(error)) from error

    try:
        entries = catalog_by_id()
    except Exception as error:  # a broken catalog must not abort the stage
        log.error("source_catalog_failed", error=str(error))
        raise RagUnavailable(str(error)) from error
    fragments: list[SourceFragment] = []
    for hit in hits:
        if not hit.source_id:
            log.warning("rag_hit_without_source", doc_id=hit.doc_id, ordinal=hit.ordinal)
            continue
        entry = entries.get(canonical_source_id(hit.source_id))
        if entry is None:
            log.warning("rag_hit_with_unknown_source", source_id=hit.source_id, doc_id=hit.doc_id)
            continue
        fragments.append(
            SourceFragment(
                fragment_id=f"{hit.doc_id}:{hit.kind}:{hit.ordinal}",
                source=entry.to_source(),
                section=hit.section_title or entry.title,
                text=hit.content,
                page=hit.page_number,
                trust=entry.trust,
                score=hit.score,
            )
        )
    return fragments
