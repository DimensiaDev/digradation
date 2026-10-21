"""CLI for manual verification of the search interface.

    python -m edu_rag.cli index  --parsed-dir ../edu_materials/parsed
    python -m edu_rag.cli search --parsed-dir ../edu_materials/parsed \
        --query "профессиональный стандарт" --source-type prof_stand --top-k 3

Without ``--real`` the CLI uses :class:`MockEmbeddingProvider`: fast,
offline, deterministic, but **not semantic** — unrelated text scores as
high as related text, so only the mechanics of the pipeline can be
verified, not retrieval quality. With ``--min-score``/``--min-relative``
left at their defaults the CLI uses 0.0 (no filtering) for the mock.

With ``--real`` it imports
:class:`app.providers.embeddings.OpenAIEmbeddingProvider` from the
backend and calls the school-hub router. That path requires the backend
poetry env (so ``app`` is importable) and the ``SBER_API_KEY`` /
``OPENAI_*`` variables set. For ``--real`` the thresholds default to
``MIN_SCORE``/``MIN_RELATIVE`` from ``search.py`` unless overridden, so a
casual run does not show unrelated fragments.

The CLI is a spot-check tool, not production: the index is in-memory per
invocation.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from .config import EmbeddingConfig
from .embeddings.base import EmbeddingProvider
from .embeddings.mock import MockEmbeddingProvider
from .indexer import DocumentIndexer
from .loader import load_chunks_from_json
from .search import MIN_RELATIVE, MIN_SCORE, search_knowledge_base
from .store.memory import InMemoryVectorStore


def _iter_parsed_docs(parsed_dir: Path) -> list[Path]:
    return sorted(parsed_dir.glob("*/document.json"))


def _make_provider(real: bool) -> EmbeddingProvider:
    """Return the mock or the backend's OpenAI-compatible provider."""
    if real:
        return _make_real_provider()
    return MockEmbeddingProvider(EmbeddingConfig(dimension=1024, batch_size=20))


def _make_real_provider() -> EmbeddingProvider:  # pragma: no cover - live path
    """Import the backend's embedding provider; requires the backend poetry env."""
    from app.providers.embeddings import OpenAIEmbeddingProvider

    provider = OpenAIEmbeddingProvider(
        api_key=os.environ["SBER_API_KEY"],
        model=os.environ["OPENAI_EMBEDDING_MODEL"],
        dimension=int(os.environ["OPENAI_EMBEDDING_DIMENSION"]),
        base_url=os.environ["OPENAI_URL"],
    )
    print(f"using real provider: model={provider.model} dim={provider.dimension}")
    return provider


async def _build_index(
    parsed_dir: Path,
    provider: EmbeddingProvider,
) -> InMemoryVectorStore:
    """Load every parsed document and index it into a fresh in-memory store.

    Raises ``RuntimeError`` when any document cannot be loaded (invalid
    JSON, missing file — the parser may be mid-write when this runs) or
    when any indexing batch fails. Continuing to search a partial index
    silently returns incomplete answers, which is worse than failing
    loudly. Both ``_cmd_index`` and ``_cmd_search`` translate the
    ``RuntimeError`` into exit 2.
    """
    store = InMemoryVectorStore()
    config = EmbeddingConfig(
        model=provider.model,
        dimension=provider.dimension,
        batch_size=20,
    )
    indexer = DocumentIndexer(provider=provider, store=store, config=config)

    files = _iter_parsed_docs(parsed_dir)
    print(f"indexing {len(files)} document(s) from {parsed_dir}")

    failed_total = 0
    error_lines: list[str] = []

    for path in files:
        try:
            chunks = load_chunks_from_json(path)
        except (json.JSONDecodeError, FileNotFoundError, OSError) as exc:
            raise RuntimeError(f"failed to load {path}: {exc}") from exc

        result = await indexer.index(
            doc_id=chunks[0].doc_id if chunks else path.parent.name,
            chunks=chunks,
        )
        failed_total += result.failed
        error_lines.extend(result.errors)
        print(f"  {path.parent.name}: indexed={result.indexed} " f"skipped={result.skipped} failed={result.failed}")

    if failed_total:
        raise RuntimeError(
            f"indexing failed for {failed_total} chunk(s); "
            f"first error: {error_lines[0] if error_lines else 'unknown'}"
        )
    return store


def _sanitise(text: str) -> str:
    """Strip control characters except tab and newline.

    Parsed PDFs can carry terminal escape sequences. The CLI prints
    document titles, section names and content, so a hostile PDF could
    alter the operator's terminal. ``str.isprintable`` is True for
    ordinary Unicode and False for C0/C1 controls and format chars.
    """
    return "".join(ch for ch in text if ch in ("\n", "\t") or ch.isprintable())


def _resolve_thresholds(real: bool, min_score: float | None, min_relative: float | None) -> tuple[float, float]:
    """Effective thresholds: explicit if given, else per-mode defaults.

    For the mock provider any threshold is meaningless (see module
    docstring); 0.0 matches the mock's purpose (verify the pipeline, not
    retrieval). For the real provider the defaults are the calibrated
    ``MIN_SCORE``/``MIN_RELATIVE`` from ``search.py`` so a casual
    ``--real`` run does not show unrelated fragments.
    """
    if min_score is None:
        min_score = MIN_SCORE if real else 0.0
    if min_relative is None:
        min_relative = MIN_RELATIVE if real else 0.0
    return min_score, min_relative


async def _cmd_index(args: argparse.Namespace) -> int:
    parsed_dir = Path(args.parsed_dir).resolve()
    if not parsed_dir.is_dir():
        print(f"error: {parsed_dir} is not a directory", file=sys.stderr)
        return 2

    provider = _make_provider(args.real)
    try:
        store = await _build_index(parsed_dir, provider)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"store: {await store.list_docs()}")
    return 0


async def _cmd_search(args: argparse.Namespace) -> int:
    parsed_dir = Path(args.parsed_dir).resolve()
    if not parsed_dir.is_dir():
        print(f"error: {parsed_dir} is not a directory", file=sys.stderr)
        return 2

    files = _iter_parsed_docs(parsed_dir)
    if not files:
        print(f"error: no document.json under {parsed_dir}", file=sys.stderr)
        return 2

    provider = _make_provider(args.real)
    try:
        store = await _build_index(parsed_dir, provider)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    min_score, min_relative = _resolve_thresholds(args.real, args.min_score, args.min_relative)

    hits = await search_knowledge_base(
        query=args.query,
        provider=provider,
        store=store,
        source_type=args.source_type,
        top_k=args.top_k,
        min_score=min_score,
        min_relative=min_relative,
    )

    if not hits:
        print("(no results)")
        return 0

    for i, h in enumerate(hits, 1):
        title = _sanitise(h.metadata.get("document_title", h.doc_id))
        section = _sanitise((h.section_title or "—").replace("\n", " ").strip())
        page = h.page_number if h.page_number is not None else "—"
        stype = _sanitise(h.metadata.get("source_type", "unknown"))
        print(f"\n[{i}] score={h.score:.3f}  {stype}  {title}")
        print(f"    section: {section}    page: {page}")
        preview = _sanitise(h.content[:200])
        ellipsis = "…" if len(h.content) > 200 else ""
        print(f"    {preview}{ellipsis}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Manual search over parser output.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_index = sub.add_parser("index", help="Index every parsed document.")
    p_index.add_argument("--parsed-dir", required=True, metavar="DIR")
    p_index.add_argument(
        "--real",
        action="store_true",
        help="Use the backend's OpenAI-compatible provider (run from backend/).",
    )
    p_index.set_defaults(func=_cmd_index)

    p_search = sub.add_parser("search", help="Search the in-memory index.")
    p_search.add_argument("--parsed-dir", required=True, metavar="DIR")
    p_search.add_argument("--query", required=True)
    p_search.add_argument(
        "--source-type",
        default=None,
        choices=["fgos", "prof_stand", "edu_programs"],
    )
    p_search.add_argument("--top-k", type=int, default=5)
    p_search.add_argument(
        "--min-score",
        type=float,
        default=None,
        help=(
            "Absolute cosine floor. Default: MIN_SCORE (0.3) with --real, " "0.0 (no filter) with the mock provider."
        ),
    )
    p_search.add_argument(
        "--min-relative",
        type=float,
        default=None,
        help=(
            "Fraction of the top score below which hits are dropped. "
            "Default: MIN_RELATIVE (0.5) with --real, 0.0 with the mock."
        ),
    )
    p_search.add_argument(
        "--real",
        action="store_true",
        help="Use the backend's OpenAI-compatible provider (run from backend/).",
    )
    p_search.set_defaults(func=_cmd_search)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    return asyncio.run(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
