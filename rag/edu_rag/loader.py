"""Turn ``parser`` output (``document.json``) into :class:`ChunkRecord`s.

Parser writes one JSON file per source document with ``text_chunks`` and
``tables``. This loader maps them to the store's record type, deriving
``source_type`` from the leading path segment (``fgos/...`` →
``source_type="fgos"``) so the search filter can scope by corpus.

The ``source_type`` derivation is a whitelist check, not a "take the
first segment" heuristic. Two known failure modes are avoided:

- **Parser run against a corpus subdirectory.** If ``--input`` was
  ``edu_materials/fgos``, ``source_path`` is ``origs/x.pdf`` and the
  first segment is ``origs`` — not a corpus name. A whitelist returns
  ``"unknown"`` instead of storing ``"origs"`` as source_type, which
  would silently match nothing under a corpus filter.

- **Windows path separators.** ``source_path`` from a Windows run
  contains ``\\``. The loader normalises both before splitting.

When ``source_type`` is ``"unknown"``, a caller filtering by corpus will
not see that document. Pass ``source_type=`` explicitly to the loader
when parsing a corpus subdirectory.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .models import ChunkRecord

_KNOWN_CORPORA = frozenset({"fgos", "prof_stand", "edu_programs"})


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _source_type_from_path(source_path: str) -> str:
    """Corpus name from ``source_path``, or ``"unknown"`` if not a corpus.

    Normalises ``\\`` to ``/`` first, then takes the leading segment and
    checks it against :data:`_KNOWN_CORPORA`. Anything else — a filename
    with no directory part, a ``origs/`` prefix from a parser run
    against a corpus subdirectory, an unexpected top-level folder — is
    ``"unknown"``. This deliberately avoids returning a segment that
    looks like a corpus but is not one.
    """
    normalised = source_path.replace("\\", "/")
    head, _, _ = normalised.partition("/")
    return head if head in _KNOWN_CORPORA else "unknown"


def load_chunks_from_json(
    json_path: Path,
    *,
    source_type: str | None = None,
) -> list[ChunkRecord]:
    """Read one ``document.json`` and return its chunks as records.

    ``source_type`` overrides the value derived from ``source_path``. Use
    it when the parser was pointed at a corpus subdirectory — the
    derived value will be ``"unknown"`` and corpus filters would not
    match. See the module docstring.
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
