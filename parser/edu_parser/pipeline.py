"""Walk a directory of source materials and parse every PDF/DOCX."""

import hashlib
import json
import logging
from collections.abc import Iterable
from pathlib import Path

from .models import ParsedDocument, ParsedImage
from .parsers import parse_docx, parse_pdf

logger = logging.getLogger(__name__)

SUPPORTED = {".pdf", ".docx"}


def _doc_id_for(rel_path: str) -> str:
    return hashlib.sha1(rel_path.encode("utf-8")).hexdigest()[:16]


def _iter_files(root: Path) -> Iterable[Path]:
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in SUPPORTED:
            yield p


def _serialize(doc: ParsedDocument, images_dir: Path | None) -> dict:
    def _img_dict(img: ParsedImage, saved_rel: str | None) -> dict:
        return {
            "ordinal": img.ordinal,
            "page_number": img.page_number,
            "ext": img.ext,
            "mime": img.mime,
            "bytes_size": len(img.data),
            "path": saved_rel,
            "metadata": img.metadata,
        }

    text = [
        {
            "ordinal": c.ordinal,
            "kind": c.kind,
            "section_title": c.section_title,
            "page_number": c.page_number,
            "text": c.text,
            "metadata": c.metadata,
        }
        for c in doc.text_chunks
    ]
    tables = [
        {
            "ordinal": t.ordinal,
            "section_title": t.section_title,
            "page_number": t.page_number,
            "rows": t.rows,
            "as_text": t.as_text,
            "metadata": t.metadata,
        }
        for t in doc.tables
    ]

    images: list[dict] = []
    for img in doc.images:
        saved_rel: str | None = None
        if images_dir is not None:
            images_dir.mkdir(parents=True, exist_ok=True)
            fname = f"{img.ordinal:04d}.{img.ext}"
            (images_dir / fname).write_bytes(img.data)
            saved_rel = str((images_dir / fname).relative_to(images_dir.parent))
        images.append(_img_dict(img, saved_rel))

    return {
        "doc_id": doc.doc_id,
        "source_path": doc.source_path,
        "title": doc.title,
        "errors": doc.errors,
        "counts": {
            "text_chunks": len(doc.text_chunks),
            "tables": len(doc.tables),
            "images": len(doc.images),
        },
        "text_chunks": text,
        "tables": tables,
        "images": images,
    }


def _write_preview_md(doc: ParsedDocument, out_path: Path) -> None:
    lines = [
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
            head += f" · {c.section_title}"
        lines.append(head)
        lines.append("")
        lines.append(c.text)
        lines.append("")
    for t in doc.tables:
        head = f"## table #{t.ordinal}"
        if t.page_number is not None:
            head += f" · page {t.page_number}"
        if t.section_title:
            head += f" · {t.section_title}"
        lines.append(head)
        lines.append("")
        lines.append(t.as_text)
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def _save(doc: ParsedDocument, out_root: Path) -> dict:
    doc_dir = out_root / doc.doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)
    payload = _serialize(doc, images_dir=doc_dir / "images")
    json_path = doc_dir / "document.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_preview_md(doc, doc_dir / "preview.md")
    return {
        "doc_id": doc.doc_id,
        "source_path": doc.source_path,
        "json": str(json_path),
        "counts": payload["counts"],
    }


def process_directory(input_root: Path, output_root: Path, limit: int | None = None) -> list[dict]:
    """Parse every supported file; never raise — record errors in manifest."""
    input_root = input_root.resolve()
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    files = list(_iter_files(input_root))
    if limit:
        files = files[:limit]

    for i, path in enumerate(files, start=1):
        rel = str(path.relative_to(input_root))
        doc_id = _doc_id_for(rel)
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
    return results
