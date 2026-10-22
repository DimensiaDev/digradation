"""Turn parser output into idempotent RAG chunks.

The parser writes one ``document.json`` per source under a flat directory named
by its hash. The catalog names the same sources with stable ``src-NN`` ids, so
ingestion is mostly a join: match a parsed document to a catalog entry, convert
both text chunks and tables into :class:`~edu_rag.models.ChunkRecord`, and hand
the list to :class:`~edu_rag.indexer.DocumentIndexer`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from edu_rag import ChunkRecord, DocumentIndexer, EmbeddingConfig, IndexResult, VectorStore
from edu_rag.embeddings.base import EmbeddingProvider

from app.rag.catalog import CatalogEntry, find_catalog_entry, load_catalog

#: Chunk kinds the parser emits. They are part of the ``(ordinal, kind)`` key,
#: so a text chunk and a table may share an ordinal without colliding.
KIND_TEXT = "text"
KIND_TABLE = "table"


@dataclass
class PlannedSource:
    """One catalog entry joined with the parser document that belongs to it."""

    entry: CatalogEntry
    doc_id: str | None = None
    title: str | None = None
    chunks: list[ChunkRecord] = field(default_factory=list)
    error: str | None = None
    empty: bool = False

    @property
    def actionable(self) -> bool:
        """Whether there is a document to write; an empty allowed document still prunes."""
        return self.doc_id is not None and self.error is None


@dataclass
class IngestPlan:
    """Everything the CLI needs to explain what it will do before it does it."""

    sources: list[PlannedSource]
    unclaimed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def planned(self) -> list[PlannedSource]:
        return [source for source in self.sources if source.actionable]

    @property
    def has_errors(self) -> bool:
        return bool(self.errors) or any(
            source.error or (source.empty and not source.entry.allow_empty) for source in self.sources
        )


@dataclass
class IngestSummary:
    """Aggregate result of one ingestion run."""

    indexed: int = 0
    skipped: int = 0
    deleted: int = 0
    failed: int = 0
    empty: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors) or self.failed > 0


def read_parsed_documents(parsed_root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Read every ``*/document.json`` below ``parsed_root``.

    A malformed document is reported and skipped rather than aborting the run:
    one broken parse must not make the other nine sources unindexable.
    """
    documents: list[dict[str, Any]] = []
    errors: list[str] = []
    root = parsed_root.resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"parsed root is not a directory: {root}")
    for path in sorted(root.glob("*/document.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            errors.append(f"{path}: {type(error).__name__}: {error}")
            continue
        if not isinstance(payload, dict) or not payload.get("doc_id"):
            errors.append(f"{path}: document.json has no doc_id")
            continue
        documents.append(payload)
    return documents, errors


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _metadata(doc: dict[str, Any], entry: CatalogEntry) -> dict[str, Any]:
    return {
        "source_id": entry.id,
        "source_kind": entry.kind.value,
        "source_path": doc.get("source_path"),
        "document_title": doc.get("title"),
        "trust": entry.trust,
    }


def chunks_for_document(doc: dict[str, Any], entry: CatalogEntry) -> list[ChunkRecord]:
    """Convert one parser document into text and table chunks.

    Empty chunks are dropped. A scanned PDF therefore produces an empty list,
    which is a legitimate state for catalog entries marked ``allow_empty``.
    """
    doc_id = str(doc["doc_id"])
    metadata = _metadata(doc, entry)
    chunks: list[ChunkRecord] = []
    seen: set[tuple[int, str]] = set()

    def add(
        *,
        ordinal: Any,
        kind: str,
        content: Any,
        section_title: Any,
        page_number: Any,
    ) -> None:
        text = str(content or "").strip()
        if not text:
            return
        key = (int(ordinal), kind)
        if key in seen:
            raise ValueError(f"document {doc_id} has duplicate chunk key {key}")
        seen.add(key)
        chunks.append(
            ChunkRecord(
                doc_id=doc_id,
                ordinal=key[0],
                kind=kind,
                source_id=entry.id,
                source_kind=entry.kind.value,
                section_title=str(section_title).strip() if section_title else None,
                page_number=int(page_number) if page_number is not None else None,
                content=text,
                content_hash=_content_hash(text),
                metadata=metadata,
            )
        )

    for chunk in doc.get("text_chunks", []):
        add(
            ordinal=chunk.get("ordinal", 0),
            kind=KIND_TEXT,
            content=chunk.get("text"),
            section_title=chunk.get("section_title"),
            page_number=chunk.get("page_number"),
        )
    for table in doc.get("tables", []):
        add(
            ordinal=table.get("ordinal", 0),
            kind=KIND_TABLE,
            content=table.get("as_text"),
            section_title=table.get("section_title"),
            page_number=table.get("page_number"),
        )
    return chunks


def build_ingest_plan(
    parsed_root: Path,
    *,
    catalog_path: Path | str | None = None,
) -> IngestPlan:
    """Join parsed documents with the catalog and report every mismatch."""
    entries = load_catalog(catalog_path)
    documents, errors = read_parsed_documents(parsed_root)
    by_entry: dict[str, list[dict[str, Any]]] = {}
    unclaimed: list[str] = []
    for doc in documents:
        try:
            entry = find_catalog_entry(str(doc.get("source_path", "")), entries)
        except ValueError as error:
            errors.append(str(error))
            continue
        if entry is None:
            unclaimed.append(str(doc.get("source_path") or doc.get("doc_id")))
            continue
        by_entry.setdefault(entry.id, []).append(doc)

    sources: list[PlannedSource] = []
    for entry in entries:
        docs = by_entry.get(entry.id, [])
        if len(docs) > 1:
            paths = ", ".join(str(doc.get("source_path")) for doc in docs)
            sources.append(PlannedSource(entry=entry, error=f"more than one parsed document: {paths}"))
            continue
        if not docs:
            sources.append(PlannedSource(entry=entry, error="no parsed document for this source"))
            continue
        doc = docs[0]
        try:
            chunks = chunks_for_document(doc, entry)
        except (TypeError, ValueError) as error:
            sources.append(
                PlannedSource(
                    entry=entry,
                    doc_id=str(doc.get("doc_id")) if doc.get("doc_id") else None,
                    title=doc.get("title"),
                    error=str(error),
                )
            )
            continue
        sources.append(
            PlannedSource(
                entry=entry,
                doc_id=str(doc["doc_id"]),
                title=str(doc.get("title") or entry.title),
                chunks=chunks,
                empty=not chunks,
            )
        )
    return IngestPlan(sources=sources, unclaimed=unclaimed, errors=errors)


def _merge_result(summary: IngestSummary, result: IndexResult) -> None:
    summary.indexed += result.indexed
    summary.skipped += result.skipped
    summary.deleted += result.deleted
    summary.failed += result.failed
    summary.errors.extend(result.errors)


async def index_plan(
    plan: IngestPlan,
    *,
    provider: EmbeddingProvider,
    store: VectorStore,
    batch_size: int = 20,
    prune: bool = False,
) -> IngestSummary:
    """Embed and write every planned source; never abort on one bad batch."""
    summary = IngestSummary()
    config = EmbeddingConfig(model=provider.model, dimension=provider.dimension, batch_size=batch_size)
    indexer = DocumentIndexer(provider=provider, store=store, config=config)

    for source in plan.sources:
        if source.error is not None:
            summary.errors.append(f"{source.entry.id}: {source.error}")
            continue
        if source.doc_id is None:
            continue
        if source.empty:
            summary.empty.append(source.entry.id)
            if not source.entry.allow_empty:
                # A parser regression must not silently wipe a good index. The
                # source is reported and left untouched; `allow_empty` is the
                # explicit opt-in that lets an empty document prune its chunks.
                summary.errors.append(f"{source.entry.id}: no text chunks (set allow_empty to accept a scanned source)")
                continue
        result = await indexer.index(source.doc_id, source.chunks)
        _merge_result(summary, result)
    summary.errors.extend(plan.errors)
    summary.errors.extend(f"unclaimed parsed document: {path}" for path in plan.unclaimed)

    if prune:
        expected = {source.doc_id for source in plan.planned}
        for doc in await store.list_docs():
            doc_id = str(doc["doc_id"])
            if doc_id not in expected:
                await store.delete_orphans(doc_id, keep=[])
                summary.deleted += int(doc.get("chunks", 0))
    return summary
