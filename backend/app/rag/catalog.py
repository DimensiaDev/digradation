"""The curated source catalog the agent may cite.

The parser's ``doc_id`` is a hash of the source path, which cannot satisfy the
programme schema's ``src-NN`` citation ids. The catalog bridges the two: one
stable id per curated document, plus the path used to match parser output. The
catalog is a JSON file because it is deployment data, not application logic.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.agentic.models import Source, SourceId, SourceKind
from app.rag.config import CATALOG_PATH

_SOURCE_ID = re.compile(r"^src-(\d{1,3})$")


class CatalogEntry(BaseModel):
    """One curated document and the metadata every citation of it carries."""

    model_config = ConfigDict(frozen=True)

    id: SourceId
    path: str = Field(min_length=1)
    kind: SourceKind
    title: str = Field(min_length=1, max_length=2000)
    identifier: str | None = Field(default=None, max_length=1000)
    url: str | None = Field(default=None, max_length=2000)
    trust: float = Field(default=0.5, ge=0.0, le=1.0)
    #: A scanned document may legitimately have no text chunks until OCR runs.
    #: The ingestion CLI reports it without failing the whole index.
    allow_empty: bool = False

    @field_validator("path")
    @classmethod
    def _relative_posix_path(cls, value: str) -> str:
        """Reject absolute paths and ``..`` so a catalog entry cannot leave the corpus."""
        normalized = value.replace("\\", "/")
        if normalized.startswith("/"):
            raise ValueError("path must be relative to the materials root")
        if ".." in normalized.split("/"):
            raise ValueError("path must be relative to the materials root and must not contain '..'")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        if not normalized:
            raise ValueError("path must not be empty")
        return normalized

    @model_validator(mode="after")
    def _canonical_id(self) -> Self:
        """Store the id in the two-digit form the catalog uses."""
        match = _SOURCE_ID.fullmatch(self.id)
        if match is None:  # pragma: no cover - SourceId already guarantees this
            raise ValueError(f"invalid source id: {self.id}")
        canonical = f"src-{int(match.group(1)):02d}"
        if canonical != self.id:
            raise ValueError(f"catalog id {self.id!r} must be zero-padded as {canonical!r}")
        return self

    def to_source(self) -> Source:
        """The schema record the tool returns and the state stores."""
        return Source(
            id=self.id,
            kind=self.kind,
            title=self.title,
            identifier=self.identifier,
            url=self.url,
        )


def canonical_source_id(source_id: str) -> str:
    """Normalise ``src-1`` to ``src-01``; leave anything else untouched."""
    match = _SOURCE_ID.fullmatch(source_id.strip())
    if match is None:
        return source_id
    return f"src-{int(match.group(1)):02d}"


@lru_cache
def _load_catalog(path: str) -> tuple[CatalogEntry, ...]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"source catalog {path} must contain a JSON array")
    entries = tuple(CatalogEntry.model_validate(item) for item in payload)
    ids = [entry.id for entry in entries]
    duplicate_ids = sorted({source_id for source_id in ids if ids.count(source_id) > 1})
    if duplicate_ids:
        raise ValueError(f"duplicate source ids in {path}: {', '.join(duplicate_ids)}")
    paths = [entry.path for entry in entries]
    duplicate_paths = sorted({source_path for source_path in paths if paths.count(source_path) > 1})
    if duplicate_paths:
        raise ValueError(f"duplicate source paths in {path}: {', '.join(duplicate_paths)}")
    return entries


def load_catalog(path: Path | str | None = None) -> tuple[CatalogEntry, ...]:
    """Load and validate the catalog once per process."""
    return _load_catalog(str(Path(path) if path is not None else CATALOG_PATH))


def clear_catalog_cache() -> None:
    """Drop every cached catalog view; tests that write a temporary catalog use it."""
    _load_catalog.cache_clear()
    catalog_by_id.cache_clear()


@lru_cache
def catalog_by_id() -> dict[str, CatalogEntry]:
    """The catalog keyed by canonical source id."""
    return {entry.id: entry for entry in load_catalog()}


def get_catalog_entry(source_id: str) -> CatalogEntry | None:
    return catalog_by_id().get(canonical_source_id(source_id))


def find_catalog_entry(source_path: str, entries: tuple[CatalogEntry, ...] | None = None) -> CatalogEntry | None:
    """Find the entry for a parser ``source_path``.

    The parser records the path relative to the directory it was given. A run
    over ``edu_materials`` and a run over ``edu_materials/fgos`` produce paths
    with and without the ``fgos/`` prefix, so an exact match is tried first and
    a suffix match second. More than one suffix match is an ambiguity and is
    reported rather than guessed.
    """
    normalized = source_path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    candidates = entries if entries is not None else load_catalog()
    exact = [entry for entry in candidates if entry.path == normalized]
    if exact:
        return exact[0]
    suffix = [
        entry for entry in candidates if entry.path.endswith(f"/{normalized}") or normalized.endswith(f"/{entry.path}")
    ]
    if len(suffix) > 1:
        names = ", ".join(entry.path for entry in suffix)
        raise ValueError(f"source path {source_path!r} matches more than one catalog entry: {names}")
    return suffix[0] if suffix else None
