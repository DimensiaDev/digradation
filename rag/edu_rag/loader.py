"""Turn ``parser`` output (``document.json``) into :class:`ChunkRecord`s.

Parser writes one JSON file per source document with ``text_chunks`` and
``tables``. This loader maps them to the store's record type, deriving
``source_type`` from the leading path segment (``fgos/...`` →
``source_type="fgos"``) so the search filter can scope by corpus.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .models import ChunkRecord


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _source_type_from_path(source_path: str) -> str:
    """Leading path segment, e.g. ``fgos/origs/x.pdf`` → ``fgos``."""
    head = source_path.split("/", 1)[0]
    return head or "unknown"


def load_chunks_from_json(json_path: Path) -> list[ChunkRecord]:
    """Read one ``document.json`` and return its chunks as records."""
    payload = json.loads(json_path.read_text(encoding="utf-8"))

    doc_id = payload["doc_id"]
    source_path = payload["source_path"]
    document_title = payload["title"]
    source_type = _source_type_from_path(source_path)

    base_metadata = {
        "source_type": source_type,
        "source_path": source_path,
        "document_title": document_title,
    }

    chunks: list[ChunkRecord] = []

    for c in payload.get("text_chunks", []):
        content = c["text"]
        chunks.append(
            ChunkRecord(
                doc_id=doc_id,
                ordinal=c["ordinal"],
                kind="text",
                section_title=c.get("section_title"),
                page_number=c.get("page_number"),
                content=content,
                content_hash=_content_hash(content),
                metadata={**base_metadata, **c.get("metadata", {})},
            )
        )

    for t in payload.get("tables", []):
        content = t["as_text"]
        chunks.append(
            ChunkRecord(
                doc_id=doc_id,
                ordinal=t["ordinal"],
                kind="table",
                section_title=t.get("section_title"),
                page_number=t.get("page_number"),
                content=content,
                content_hash=_content_hash(content),
                metadata={**base_metadata, **t.get("metadata", {})},
            )
        )

    return chunks
