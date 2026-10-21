"""Tests for the parser-output loader."""

import json
from pathlib import Path

from edu_rag.loader import (
    _content_hash,
    _source_type_from_path,
    load_chunks_from_json,
)


def _write_doc(tmp_path: Path, payload: dict) -> Path:
    doc_dir = tmp_path / payload["doc_id"]
    doc_dir.mkdir(parents=True, exist_ok=True)
    path = doc_dir / "document.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _make_payload(
    doc_id: str = "abc123",
    source_path: str = "fgos/origs/fgos-ooo-2021.pdf",
    title: str = "fgos-ooo-2021",
    text_chunks: list[dict] | None = None,
    tables: list[dict] | None = None,
) -> dict:
    return {
        "doc_id": doc_id,
        "source_path": source_path,
        "title": title,
        "text_chunks": text_chunks if text_chunks is not None else [],
        "tables": tables if tables is not None else [],
    }


# ------------------------------------------------------------ helpers
def test_content_hash_is_stable():
    a = _content_hash("hello")
    b = _content_hash("hello")
    assert a == b
    assert len(a) == 64


def test_content_hash_differs():
    assert _content_hash("hello") != _content_hash("world")


def test_source_type_from_path_first_segment():
    assert _source_type_from_path("fgos/origs/x.pdf") == "fgos"
    assert _source_type_from_path("prof_stand/origs/x.pdf") == "prof_stand"
    assert _source_type_from_path("edu_programs/origs/x.docx") == "edu_programs"


def test_source_type_from_bare_filename():
    assert _source_type_from_path("document.pdf") == "document.pdf"


def test_source_type_empty_path():
    assert _source_type_from_path("") == "unknown"


# ------------------------------------------------------------ load
def test_load_empty_document(tmp_path: Path):
    path = _write_doc(tmp_path, _make_payload())
    assert load_chunks_from_json(path) == []


def test_load_text_chunks(tmp_path: Path):
    payload = _make_payload(
        text_chunks=[
            {
                "ordinal": 0,
                "text": "Компетенция УК-1",
                "section_title": "Требования",
                "page_number": 5,
                "metadata": {},
            },
            {
                "ordinal": 1,
                "text": "Другая секция",
                "section_title": None,
                "page_number": None,
                "metadata": {},
            },
        ]
    )
    path = _write_doc(tmp_path, payload)
    chunks = load_chunks_from_json(path)

    assert len(chunks) == 2
    assert chunks[0].doc_id == "abc123"
    assert chunks[0].kind == "text"
    assert chunks[0].ordinal == 0
    assert chunks[0].content == "Компетенция УК-1"
    assert chunks[0].section_title == "Требования"
    assert chunks[0].page_number == 5
    assert chunks[0].metadata["source_type"] == "fgos"
    assert chunks[0].metadata["document_title"] == "fgos-ooo-2021"
    assert chunks[0].metadata["source_path"] == "fgos/origs/fgos-ooo-2021.pdf"


def test_load_tables(tmp_path: Path):
    payload = _make_payload(
        tables=[
            {
                "ordinal": 0,
                "as_text": "| A | B |\n| --- | --- |",
                "section_title": "Учебный план",
                "page_number": 12,
                "metadata": {"rows": 2},
            }
        ]
    )
    path = _write_doc(tmp_path, payload)
    chunks = load_chunks_from_json(path)

    assert len(chunks) == 1
    assert chunks[0].kind == "table"
    assert chunks[0].content.startswith("| A | B |")
    assert chunks[0].metadata["source_type"] == "fgos"
    assert chunks[0].metadata["rows"] == 2


def test_load_preserves_metadata_keys(tmp_path: Path):
    payload = _make_payload(
        text_chunks=[
            {
                "ordinal": 0,
                "text": "x",
                "section_title": None,
                "page_number": None,
                "metadata": {"custom": "value"},
            }
        ]
    )
    path = _write_doc(tmp_path, payload)
    [chunk] = load_chunks_from_json(path)
    assert chunk.metadata["custom"] == "value"
    assert chunk.metadata["source_type"] == "fgos"


def test_load_content_hash_matches_content(tmp_path: Path):
    payload = _make_payload(
        text_chunks=[
            {
                "ordinal": 0,
                "text": "abc",
                "section_title": None,
                "page_number": None,
                "metadata": {},
            }
        ]
    )
    path = _write_doc(tmp_path, payload)
    [chunk] = load_chunks_from_json(path)
    assert chunk.content_hash == _content_hash("abc")
