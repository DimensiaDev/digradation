"""Turn ``parser`` output (``document.json``) into :class:`ChunkRecord`s.

Parser writes one JSON file per source document with ``text_chunks`` and
``tables``. This loader maps them to the store's record type, deriving
``source_type`` from the leading path segment (``fgos/...`` →
``source_type="fgos"``) so the search filter can scope by corpus.

The ``source_type`` derivation has two known limits, both surfaced here
rather than hidden:

- **Path separators.** ``source_path`` is a ``str`` from the parser's
  manifest. On a parser run under Windows the separators are ``\\``, so
  the loader normalises both before splitting; otherwise a Windows run
  would store the whole path as ``source_type`` and no corpus filter
  would ever match.

- **Corpus root not in path.** If the parser was run with
  ``--input edu_materials/fgos``, ``source_path`` starts with ``origs/``
  or a filename, not ``fgos/``. The loader falls back to
  ``source_type="unknown"``, which means a filter by corpus silently
  drops those documents. Pass ``source_type=`` explicitly when the
  parser root is a corpus subdirectory; the CLI does not do that yet
  because it always runs on ``edu_materials`` itself.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .models import ChunkRecord


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _source_type_from_path(source_path: str) -> str:
    """Leading path segment, e.g. ``fgos/origs/x.pdf`` → ``fgos``.

    Normalises ``\\`` to ``/`` first so a Windows-parsed manifest does
    not produce a source_type that contains the whole path. Returns
    ``"unknown"`` when the path has no directory part — see the module
    docstring for why that happens.
    """
    normalised = source_path.replace("\\", "/")
    head, _, _ = normalised.partition("/")
    return head or "unknown"


def load_chunks_from_json(
    json_path: Path,
    *,
    source_type: str | None = None,
) -> list[ChunkRecord]:
    """Read one ``document.json`` and return its chunks as records.

    ``source_type`` overrides the value derived from ``source_path``.
    Use it when the parser was pointed at a corpus subdirectory rather
    than at ``edu_materials/`` — otherwise the derived value is
    ``"unknown"`` and corpus filters will not match. See the module
    docstring.
    """
    payload = json.loads(json_path.read_text(encoding="utf-8"))

    doc_id = payload["doc_id"]
    source_path = payload["source_path"]
    document_title = payload["title"]
    effective_source_type = source_type or _source_type_from_path(source_path)

    base_metadata = {
        "source_type": effective_source_type,
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
                metadata={**c.get("metadata", {}), **base_metadata},
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
                metadata={**t.get("metadata", {}), **base_metadata},
            )
        )

    return chunks
