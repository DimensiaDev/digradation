"""Tests for the manual search CLI."""

import json
from pathlib import Path

from edu_rag.cli import main


def _write_doc(parsed_dir: Path, doc_id: str, text: str) -> None:
    doc_dir = parsed_dir / doc_id
    doc_dir.mkdir(parents=True)
    payload = {
        "doc_id": doc_id,
        "source_path": f"fgos/origs/{doc_id}.pdf",
        "title": f"title-{doc_id}",
        "text_chunks": [
            {
                "ordinal": 0,
                "text": text,
                "section_title": "Section",
                "page_number": 1,
                "metadata": {},
            }
        ],
        "tables": [],
    }
    (doc_dir / "document.json").write_text(json.dumps(payload), encoding="utf-8")


def test_index_command_runs(tmp_path, capsys):
    parsed = tmp_path / "parsed"
    _write_doc(parsed, "d1", "hello")
    code = main(["index", "--parsed-dir", str(parsed)])
    assert code == 0
    out = capsys.readouterr().out
    assert "indexed=1" in out


def test_search_command_prints_hits(tmp_path, capsys):
    parsed = tmp_path / "parsed"
    _write_doc(parsed, "d1", "hello")
    code = main(
        [
            "search",
            "--parsed-dir",
            str(parsed),
            "--query",
            "hello",
            "--top-k",
            "1",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "score=" in out
    assert "hello" in out


def test_search_command_no_results(tmp_path, capsys):
    parsed = tmp_path / "parsed"
    _write_doc(parsed, "d1", "short")
    code = main(
        [
            "search",
            "--parsed-dir",
            str(parsed),
            "--query",
            "абракадабраззз",
            "--top-k",
            "1",
        ]
    )
    assert code == 0


def test_missing_dir_returns_2(tmp_path):
    code = main(["index", "--parsed-dir", str(tmp_path / "nope")])
    assert code == 2


def test_empty_dir_returns_2(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    code = main(["search", "--parsed-dir", str(empty), "--query", "x"])
    assert code == 2
