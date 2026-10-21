"""CLI for manual verification of the search interface.

    python -m edu_rag.cli index  --parsed-dir ../edu_materials/parsed
    python -m edu_rag.cli search --parsed-dir ../edu_materials/parsed \
        --query "профессиональный стандарт" --source-type prof_stand --top-k 3

Without ``--real`` the CLI uses :class:`MockEmbeddingProvider`: fast,
offline, deterministic, but **not semantic** — unrelated text scores as
high as related text, so only the mechanics of the pipeline can be
verified, not retrieval quality.

With ``--real`` it imports
:class:`app.providers.embeddings.OpenAIEmbeddingProvider` from the backend
and calls the school-hub router. That path requires:

    * running from the ``backend/`` poetry env (so ``app`` is importable),
    * ``SBER_API_KEY``, ``SBER_API_SCOPE=openai``, ``OPENAI_URL``,
      ``OPENAI_EMBEDDING_MODEL``, ``OPENAI_EMBEDDING_DIMENSION`` in the
      environment.

The CLI is a spot-check tool, not production: the index is in-memory per
invocation. Production indexing writes to pgvector via a taskiq task.
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from .config import EmbeddingConfig
from .embeddings.base import EmbeddingProvider
from .embeddings.mock import MockEmbeddingProvider
from .indexer import DocumentIndexer
from .loader import load_chunks_from_json
from .search import search_knowledge_base
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


async def _build_index(parsed_dir: Path, provider: EmbeddingProvider) -> InMemoryVectorStore:
    """Load every parsed document and index it into a fresh in-memory store."""
    store = InMemoryVectorStore()
    config = EmbeddingConfig(
        model=provider.model,
        dimension=provider.dimension,
        batch_size=20,
    )
    indexer = DocumentIndexer(provider=provider, store=store, config=config)

    files = _iter_parsed_docs(parsed_dir)
    print(f"indexing {len(files)} document(s) from {parsed_dir}")
    for path in files:
        chunks = load_chunks_from_json(path)
        result = await indexer.index(
            doc_id=chunks[0].doc_id if chunks else path.parent.name,
            chunks=chunks,
        )
        print(f"  {path.parent.name}: indexed={result.indexed} " f"skipped={result.skipped} failed={result.failed}")
    return store


async def _cmd_index(args: argparse.Namespace) -> int:
    parsed_dir = Path(args.parsed_dir).resolve()
    if not parsed_dir.is_dir():
        print(f"error: {parsed_dir} is not a directory", file=sys.stderr)
        return 2

    provider = _make_provider(args.real)
    store = await _build_index(parsed_dir, provider)
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
    store = await _build_index(parsed_dir, provider)

    hits = await search_knowledge_base(
        query=args.query,
        provider=provider,
        store=store,
        source_type=args.source_type,
        top_k=args.top_k,
        min_score=args.min_score,
        min_relative=args.min_relative,
    )

    if not hits:
        print("(no results)")
        return 0

    for i, h in enumerate(hits, 1):
        title = h.metadata.get("document_title", h.doc_id)
        section = (h.section_title or "—").replace("\n", " ").strip()
        page = h.page_number if h.page_number is not None else "—"
        stype = h.metadata.get("source_type", "unknown")
        print(f"\n[{i}] score={h.score:.3f}  {stype}  {title}")
        print(f"    section: {section}    page: {page}")
        preview = h.content[:200]
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
        default=0.0,
        help=(
            "Absolute cosine floor. Default 0.0 because MockEmbeddingProvider "
            "is not semantic; set to 0.3 (or MIN_SCORE from search.py) when "
            "using --real."
        ),
    )
    p_search.add_argument(
        "--min-relative",
        type=float,
        default=0.0,
        help="Fraction of the top score below which hits are dropped (0.0 = off).",
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
