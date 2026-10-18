"""Walk a directory of source materials and parse every PDF/DOCX."""

import hashlib
import json
import logging
import shutil
import tempfile
from collections.abc import Iterable
from pathlib import Path

from .models import ParsedDocument
from .parsers import parse_docx, parse_pdf

logger = logging.getLogger(__name__)

SUPPORTED = {".pdf", ".docx"}


def _doc_id_for(rel_path: str, input_root: Path) -> str:
    """Stable id scoped to the input root.

    Including the input root name prevents two different source trees
    with the same relative path from silently overwriting each other
    in a shared output directory.
    """
    key = f"{input_root.resolve().name}/{rel_path}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _iter_files(root: Path) -> Iterable[Path]:
    """Yield supported files, rejecting symlinks and out-of-tree paths."""
    root_resolved = root.resolve()
    for p in sorted(root.rglob("*")):
        if p.suffix.lower() not in SUPPORTED:
            continue
        if p.is_symlink():
            logger.warning("skip symlink: %s", p)
            continue
        if not p.is_file():
            continue
        try:
            p.resolve().relative_to(root_resolved)
        except ValueError:
            logger.warning("skip out-of-tree path: %s", p)
            continue
        yield p


def _md_escape_inline(text: str) -> str:
    """Escape characters that could break out of a markdown heading line."""
    return "".join("\\" + c if c in r"\`*_{}[]()#+-.!" else c for c in text)


def _serialize(doc: ParsedDocument, images_dir: Path | None) -> dict:
    """Build the JSON payload for a parsed document.

    ``ParsedDocument.model_dump`` handles all plain fields
    (``doc_id``, ``source_path``, ``title``, ``text_chunks``, ``tables``,
    ``errors``). ``images`` are excluded from the dump because their raw
    bytes must be written to disk first, and the JSON only carries the
    relative path plus metadata.
    """
    payload = doc.model_dump(mode="python", exclude={"images"})

    images: list[dict] = []
    for img in doc.images:
        saved_rel: str | None = None
        if images_dir is not None:
            images_dir.mkdir(parents=True, exist_ok=True)
            fname = f"{img.ordinal:04d}.{img.ext}"
            (images_dir / fname).write_bytes(img.data)
            saved_rel = str((images_dir / fname).relative_to(images_dir.parent))
        images.append(
            {
                "ordinal": img.ordinal,
                "page_number": img.page_number,
                "ext": img.ext,
                "mime": img.mime,
                "bytes_size": len(img.data),
                "path": saved_rel,
                "metadata": img.metadata,
            }
        )

    payload["images"] = images
    payload["counts"] = {
        "text_chunks": len(doc.text_chunks),
        "tables": len(doc.tables),
        "images": len(doc.images),
    }
    return payload


def _write_preview_md(doc: ParsedDocument, out_path: Path) -> None:
    lines: list[str] = [
        f"# {doc.title}",
        "",
        f"- **doc_id**: `{doc.doc_id}`",
        f"- **source**: `{doc.source_path}`",
        f"- **chunks**: {len(doc.text_chunks)} · " f"**tables**: {len(doc.tables)} · " f"**images**: {len(doc.images)}",
        "",
        "---",
        "",
    ]
    for c in doc.text_chunks:
        head = f"## chunk #{c.ordinal} [{c.kind}]"
        if c.page_number is not None:
            head += f" · page {c.page_number}"
        if c.section_title:
            head += f" · {_md_escape_inline(c.section_title)}"
        lines.extend(
            [
                head,
                "",
                "```text",
                c.text,
                "```",
                "",
            ]
        )
    for t in doc.tables:
        head = f"## table #{t.ordinal}"
        if t.page_number is not None:
            head += f" · page {t.page_number}"
        if t.section_title:
            head += f" · {_md_escape_inline(t.section_title)}"
        lines.extend(
            [
                head,
                "",
                "```markdown",
                t.as_text,
                "```",
                "",
            ]
        )
    out_path.write_text("\n".join(lines), encoding="utf-8")


def _save(doc: ParsedDocument, out_root: Path) -> dict:
    """Write all artifacts atomically via a temp directory."""
    final_dir = out_root / doc.doc_id
    tmp_dir = Path(tempfile.mkdtemp(prefix=f".{doc.doc_id}.", dir=out_root))

    counts = {
        "text_chunks": len(doc.text_chunks),
        "tables": len(doc.tables),
        "images": len(doc.images),
    }

    try:
        payload = _serialize(doc, images_dir=tmp_dir / "images")
        (tmp_dir / "document.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _write_preview_md(doc, tmp_dir / "preview.md")

        if final_dir.exists():
            shutil.rmtree(final_dir)
        tmp_dir.rename(final_dir)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    return {
        "doc_id": doc.doc_id,
        "source_path": doc.source_path,
        "json": str(final_dir / "document.json"),
        "counts": counts,
    }


def _prune_stale(out_root: Path, keep_ids: set[str]) -> None:
    """Remove doc directories that weren't produced by the current run."""
    for entry in out_root.iterdir():
        if entry.name == "manifest.json":
            continue
        if not entry.is_dir():
            continue
        if entry.name in keep_ids:
            continue
        logger.info("pruning stale doc dir: %s", entry.name)
        shutil.rmtree(entry, ignore_errors=True)


def process_directory(
    input_root: Path,
    output_root: Path,
    limit: int | None = None,
    prune_stale: bool = False,
) -> list[dict]:
    """Parse every supported file; never raise on per-file errors.

    Raises ``NotADirectoryError`` if ``input_root`` is missing or is not a
    directory. Pass ``prune_stale=True`` to delete doc dirs that are not
    produced by this run. Only use ``prune_stale`` when a full sweep is
    intended — with ``limit`` set, unrelated dirs would be wiped.
    """
    input_root = input_root.resolve()
    if not input_root.is_dir():
        raise NotADirectoryError(f"input root is not a directory: {input_root}")

    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    files = list(_iter_files(input_root))
    if limit is not None:
        files = files[:limit]

    for i, path in enumerate(files, start=1):
        rel = str(path.relative_to(input_root))
        doc_id = _doc_id_for(rel, input_root)
        logger.info("[%d/%d] %s", i, len(files), rel)
        try:
            if path.suffix.lower() == ".pdf":
                doc = parse_pdf(path, doc_id=doc_id, source_path=rel)
            elif path.suffix.lower() == ".docx":
                doc = parse_docx(path, doc_id=doc_id, source_path=rel)
            else:
                continue
            info = _save(doc, output_root)
            results.append({"ok": True, **info})
        except Exception as exc:
            logger.exception("failed to parse %s", path)
            results.append(
                {
                    "ok": False,
                    "source_path": rel,
                    "doc_id": doc_id,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    manifest = {
        "input_root": str(input_root),
        "output_root": str(output_root),
        "total": len(results),
        "ok": sum(1 for r in results if r["ok"]),
        "failed": sum(1 for r in results if not r["ok"]),
        "results": results,
    }
    (output_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    if prune_stale:
        _prune_stale(output_root, {r["doc_id"] for r in results if r["ok"]})

    return results
