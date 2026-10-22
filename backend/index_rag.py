"""Build the pgvector RAG index from parser output.

Run this from the backend image after parsing the material corpus:

    docker compose run --rm \\
        -v "$PWD/edu_materials:/materials:ro" \\
        backend python index_rag.py --parsed /materials/parsed

The command is idempotent: unchanged chunks are skipped by the indexer's
fingerprint, changed ones are re-embedded, and ``--prune`` removes documents
that are no longer in the catalog. It is deliberately a one-off CLI rather than
an HTTP endpoint until the corpus and the hub are proven in a deployment.
"""

from __future__ import annotations

# ruff: noqa: T201 - this is an operator CLI; printing the plan is its job.
import argparse
import asyncio
import sys
from pathlib import Path

from edu_rag import PgVectorStore

from app.database import get_session_maker
from app.models.rag import RagChunk
from app.providers.containers import get_embedding_provider
from app.rag.catalog import CATALOG_PATH
from app.rag.ingest import IngestPlan, IngestSummary, build_ingest_plan, index_plan

DEFAULT_PARSED = Path("../edu_materials/parsed")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalog",
        type=Path,
        default=CATALOG_PATH,
        help="curated source catalog JSON (must match the catalog the backend serves)",
    )
    parser.add_argument("--parsed", type=Path, default=DEFAULT_PARSED, help="directory of parsed source documents")
    parser.add_argument("--batch-size", type=int, default=20, help="chunks embedded per provider request")
    parser.add_argument("--prune", action="store_true", help="delete documents that are not in the catalog")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero on a missing, failed or unclaimed source, or an empty one without allow_empty",
    )
    parser.add_argument("--dry-run", action="store_true", help="print the plan without embedding or writing")
    return parser.parse_args(argv)


def _print_plan(plan: IngestPlan) -> None:
    for source in plan.sources:
        state = "error" if source.error else ("empty" if source.empty else f"{len(source.chunks)} chunks")
        print(f"{source.entry.id}  {state:<12}  {source.entry.title}")
        if source.error:
            print(f"    {source.error}")
    for path in plan.unclaimed:
        print(f"unclaimed  {path}")
    for error in plan.errors:
        print(f"error     {error}")


def _print_summary(summary: IngestSummary) -> None:
    print(f"indexed={summary.indexed} skipped={summary.skipped} deleted={summary.deleted} failed={summary.failed}")
    if summary.empty:
        print("empty sources: " + ", ".join(summary.empty))
    for error in summary.errors:
        print(f"error: {error}")


async def _index(
    plan: IngestPlan,
    *,
    batch_size: int,
    prune: bool,
) -> IngestSummary:
    provider = get_embedding_provider()
    store = PgVectorStore(get_session_maker(), RagChunk.__table__)
    try:
        return await index_plan(plan, provider=provider, store=store, batch_size=batch_size, prune=prune)
    finally:
        await provider.close()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.batch_size <= 0:
        print("--batch-size must be positive", file=sys.stderr)
        return 2
    try:
        plan = build_ingest_plan(args.parsed, catalog_path=args.catalog)
    except (OSError, ValueError) as error:
        print(f"cannot build ingestion plan: {error}", file=sys.stderr)
        return 1

    _print_plan(plan)
    plan_failed = plan.has_errors or bool(plan.unclaimed)
    if args.dry_run:
        return 1 if args.strict and plan_failed else 0

    try:
        summary = asyncio.run(_index(plan, batch_size=args.batch_size, prune=args.prune))
    except Exception as error:  # provider or database construction failed
        print(f"indexing failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1

    _print_summary(summary)
    summary_failed = summary.has_errors or plan_failed
    return 1 if args.strict and summary_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
