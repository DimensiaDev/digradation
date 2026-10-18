"""CLI entrypoint: python -m edu_parser.cli --input ... --output ..."""

import argparse
import logging
import sys
from pathlib import Path

from .pipeline import process_directory


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Parse PDF and DOCX files into RAG-friendly chunks.",
    )
    p.add_argument(
        "--input",
        required=True,
        metavar="DIR",
        help="Root directory with source materials (e.g. ../edu_materials).",
    )
    p.add_argument(
        "--output",
        required=True,
        metavar="DIR",
        help="Directory where parsed artifacts are written.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Process only the first N files (useful for quick checks).",
    )
    p.add_argument(
        "--prune",
        action="store_true",
        help=(
            "After a full run, delete stale doc directories in the output "
            "root that are not produced by this run. Do not combine with "
            "--limit — unrelated dirs will be removed."
        ),
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        results = process_directory(
            Path(args.input),
            Path(args.output),
            limit=args.limit,
            prune_stale=args.prune,
        )
    except NotADirectoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    ok = sum(1 for r in results if r["ok"])
    failed = len(results) - ok
    print(f"\nParsed: {ok} OK, {failed} failed, total {len(results)}")
    for r in results:
        if not r["ok"]:
            print(f"  ✗ {r['source_path']}: {r['error']}")
    print(f"\nManifest: {Path(args.output).resolve() / 'manifest.json'}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
