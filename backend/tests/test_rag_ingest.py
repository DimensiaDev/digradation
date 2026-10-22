"""Tests for parser-output ingestion and the one-off indexer plan."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from edu_rag import EmbeddingConfig, InMemoryVectorStore, MockEmbeddingProvider

from app.agentic.models import SourceKind
from app.rag.catalog import CatalogEntry, clear_catalog_cache
from app.rag.ingest import build_ingest_plan, chunks_for_document, index_plan


def _entry(
    source_id: str = "src-01",
    path: str = "fgos/origs/fgos-noo-2021.pdf",
    *,
    allow_empty: bool = False,
) -> CatalogEntry:
    return CatalogEntry(
        id=source_id,
        path=path,
        kind=SourceKind.NORMATIVE_ACT,
        title="ФГОС",
        identifier="Приказ № 286",
        trust=1.0,
        allow_empty=allow_empty,
    )


def _document(
    *,
    doc_id: str = "doc-01",
    source_path: str = "fgos/origs/fgos-noo-2021.pdf",
    title: str = "ФГОС НОО",
    text: str = "Текст документа",
    table: str = "Таблица",
) -> dict:
    return {
        "doc_id": doc_id,
        "source_path": source_path,
        "title": title,
        "text_chunks": [
            {
                "doc_id": doc_id,
                "source_path": source_path,
                "ordinal": 0,
                "text": text,
                "section_title": "Раздел 1",
                "page_number": 3,
            }
        ],
        "tables": [
            {
                "doc_id": doc_id,
                "source_path": source_path,
                "ordinal": 0,
                "as_text": table,
                "section_title": "Таблица 1",
                "page_number": 4,
            }
        ],
    }


def _write_document(parsed_root: Path, document: dict) -> None:
    directory = parsed_root / document["doc_id"]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "document.json").write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")


def test_chunks_carry_the_catalog_identity() -> None:
    chunks = chunks_for_document(_document(), _entry())

    assert [(chunk.kind, chunk.ordinal) for chunk in chunks] == [("text", 0), ("table", 0)]
    assert {chunk.source_id for chunk in chunks} == {"src-01"}
    assert {chunk.source_kind for chunk in chunks} == {"normative_act"}
    assert chunks[0].section_title == "Раздел 1"
    assert chunks[0].page_number == 3
    assert chunks[0].metadata["trust"] == 1.0
    assert chunks[1].content == "Таблица"


def test_empty_chunks_are_dropped() -> None:
    document = _document(text="  ", table="")
    assert chunks_for_document(document, _entry(allow_empty=True)) == []


def test_duplicate_chunk_keys_are_rejected() -> None:
    document = _document()
    document["text_chunks"].append({**document["text_chunks"][0]})
    with pytest.raises(ValueError, match="duplicate chunk key"):
        chunks_for_document(document, _entry())


def test_plan_joins_parser_output_to_the_catalog(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps([_entry().model_dump(mode="json")]), encoding="utf-8")
    parsed = tmp_path / "parsed"
    _write_document(parsed, _document())

    plan = build_ingest_plan(parsed, catalog_path=catalog)

    assert not plan.has_errors
    assert len(plan.sources) == 1
    source = plan.sources[0]
    assert source.entry.id == "src-01"
    assert source.doc_id == "doc-01"
    assert len(source.chunks) == 2


def test_plan_reports_a_missing_document(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps([_entry().model_dump(mode="json")]), encoding="utf-8")
    parsed = tmp_path / "parsed"
    parsed.mkdir()

    plan = build_ingest_plan(parsed, catalog_path=catalog)

    assert plan.has_errors
    assert "no parsed document" in (plan.sources[0].error or "")


def test_plan_accepts_an_explicitly_empty_source(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps([_entry(allow_empty=True).model_dump(mode="json")]), encoding="utf-8")
    parsed = tmp_path / "parsed"
    _write_document(parsed, _document(text="", table=""))

    plan = build_ingest_plan(parsed, catalog_path=catalog)

    assert not plan.has_errors
    assert plan.sources[0].empty is True
    assert plan.sources[0].chunks == []


def test_plan_reports_an_unexpectedly_empty_source(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps([_entry().model_dump(mode="json")]), encoding="utf-8")
    parsed = tmp_path / "parsed"
    _write_document(parsed, _document(text="", table=""))

    plan = build_ingest_plan(parsed, catalog_path=catalog)

    assert plan.has_errors
    assert plan.sources[0].empty is True


def test_plan_reports_an_unclaimed_document(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps([_entry().model_dump(mode="json")]), encoding="utf-8")
    parsed = tmp_path / "parsed"
    _write_document(parsed, _document(doc_id="doc-02", source_path="other.pdf"))

    plan = build_ingest_plan(parsed, catalog_path=catalog)

    assert plan.unclaimed == ["other.pdf"]
    assert plan.sources[0].error == "no parsed document for this source"


async def test_index_plan_writes_and_prunes(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps([_entry().model_dump(mode="json")]), encoding="utf-8")
    parsed = tmp_path / "parsed"
    _write_document(parsed, _document())
    plan = build_ingest_plan(parsed, catalog_path=catalog)

    store = InMemoryVectorStore()
    provider = MockEmbeddingProvider(EmbeddingConfig(model="mock", dimension=8))
    summary = await index_plan(plan, provider=provider, store=store, batch_size=2)

    assert not summary.has_errors
    assert summary.indexed == 2
    hits = await store.search([1.0] + [0.0] * 7, top_k=10)
    assert {hit.source_id for hit in hits} == {"src-01"}


async def test_index_plan_keeps_the_index_when_an_unexpected_empty_parse_appears(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps([_entry().model_dump(mode="json")]), encoding="utf-8")
    parsed = tmp_path / "parsed"
    _write_document(parsed, _document())
    store = InMemoryVectorStore()
    provider = MockEmbeddingProvider(EmbeddingConfig(model="mock", dimension=8))
    await index_plan(build_ingest_plan(parsed, catalog_path=catalog), provider=provider, store=store)

    _write_document(parsed, _document(text="", table=""))
    summary = await index_plan(build_ingest_plan(parsed, catalog_path=catalog), provider=provider, store=store)

    assert summary.has_errors
    assert summary.empty == ["src-01"]
    assert summary.deleted == 0
    assert await store.list_docs() == [{"doc_id": "doc-01", "chunks": 2}]


async def test_index_plan_prunes_an_allowed_empty_source(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps([_entry(allow_empty=True).model_dump(mode="json")]), encoding="utf-8")
    parsed = tmp_path / "parsed"
    _write_document(parsed, _document())
    store = InMemoryVectorStore()
    provider = MockEmbeddingProvider(EmbeddingConfig(model="mock", dimension=8))
    await index_plan(build_ingest_plan(parsed, catalog_path=catalog), provider=provider, store=store)

    _write_document(parsed, _document(text="", table=""))
    summary = await index_plan(build_ingest_plan(parsed, catalog_path=catalog), provider=provider, store=store)

    assert not summary.has_errors
    assert summary.deleted == 2
    assert await store.list_docs() == []


@pytest.fixture(autouse=True)
def _clear_catalog_cache():
    clear_catalog_cache()
    yield
    clear_catalog_cache()
