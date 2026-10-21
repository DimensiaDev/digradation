#: Absolute cosine-similarity floor.
#:
#: **Calibrated on GigaChat ``Embeddings`` via the school-hub router**
#: (``ai-sage/Giga-Embeddings-instruct-3B``, 2048 dims, cosine in
#: ``[-1, 1]`` for L2-normalized vectors). Measurements against the
#: 590-fragment ``edu_materials/parsed`` corpus (2026-10-21):
#:
#: ============  =============  ===============  =============
#: query class   example        top-1 score      top-5 range
#: ============  =============  ===============  =============
#: unrelated     "квантовая     0.18             0.16 - 0.18
#:                хромодинамика"
#: related       "навыки        0.44             0.42 - 0.44
#:                разработчика"
#: related       "трудовые      0.44             0.42 - 0.44
#:                функции"
#: ============  =============  ===============  =============
#:
#: Unrelated and related distributions do not overlap: the best junk
#: hit (0.18) is well below the worst relevant one (0.39). ``0.30``
#: sits in the middle with margin on both sides.
#:
#: **With ``MockEmbeddingProvider`` this threshold is meaningless**: the
#: mock's vectors are deterministic but not semantic — unrelated text
#: scores as high as related text. CLI defaults ``--min-score 0.0`` for
#: that reason; pass ``--real`` for meaningful results.
#:
#: **Revisit when:** the embedding model changes, the corpus language
#: changes, or GigaChat publishes a new embeddings model with a
#: different similarity distribution. Re-calibrate by running the CLI
#: with ``--real --min-score 0.0`` on the junk and relevant queries
#: from the table above and re-reading the top-1 scores.

#: Relative floor as a fraction of the top hit. ``0.5`` means "keep
#: hits scoring at least half of the best one". On the current corpus
#: this does not fire — every relevant hit sits above 90% of the top
#: score. It exists as a safety net for the case where the top hit is
#: itself only moderately relevant (e.g. 0.35): a long tail of
#: 0.20 - 0.30 semi-relevant fragments would otherwise leak past
#: ``MIN_SCORE``. ``0.0`` disables.


from __future__ import annotations

from .embeddings.base import EmbeddingProvider
from .models import SearchHit
from .store.base import VectorStore

MIN_SCORE: float = 0.3

MIN_RELATIVE: float = 0.5


async def search_knowledge_base(
    query: str,
    provider: EmbeddingProvider,
    store: VectorStore,
    *,
    source_type: str | None = None,
    top_k: int = 5,
    min_score: float = MIN_SCORE,
    min_relative: float = MIN_RELATIVE,
) -> list[SearchHit]:
    """Return the fragments most relevant to ``query``.

    Args:
        query: Free-text query. Empty or whitespace-only returns ``[]``
            without calling the embedding provider.
        provider: Embedding provider (GigaChat or Mock).
        store: Vector store (PgVectorStore or InMemoryVectorStore).
        source_type: Optional filter, exact match on
            ``chunk.metadata["source_type"]``. ``None`` searches all
            corpora.
        top_k: Maximum number of hits. ``top_k <= 0`` returns ``[]``.
        min_score: Absolute cosine-similarity floor.
        min_relative: Relative floor as a fraction of the top hit's
            score. ``0.0`` disables.

    Returns:
        A list of :class:`SearchHit`, sorted by descending score. Empty
        when no fragment clears ``min_score`` or the relative floor —
        never a "best of a bad bunch" fallback.

        Each hit carries enough for a citation: ``doc_id`` (stable across
        runs), ``metadata["document_title"]`` (human-readable name),
        ``metadata["source_type"]`` (``fgos`` / ``prof_stand`` /
        ``edu_programs``), ``section_title`` and ``page_number``.
        ALA is expected to map these onto its own ``SourceFragment`` DTO;
        the rag module has no visibility into consumer-specific shapes.
    """
    if not query or not query.strip():
        return []
    if top_k <= 0:
        return []

    [query_vec] = await provider.embed([query.strip()])

    filter_dict = {"source_type": source_type} if source_type else None

    if filter_dict is None:
        hits = await store.search(query_vec, top_k=top_k)
    else:
        hits = await store.search(query_vec, top_k=top_k, filter=filter_dict)

    if not hits:
        return []

    top_score = hits[0].score
    floor = top_score * min_relative if min_relative > 0.0 else 0.0

    return [h for h in hits if h.score >= min_score and h.score >= floor]
