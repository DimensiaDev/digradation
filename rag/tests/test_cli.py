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
            "--min-score",
            "0.99",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "(no results)" in out
    assert "score=" not in out


def test_missing_dir_returns_2(tmp_path):
    code = main(["index", "--parsed-dir", str(tmp_path / "nope")])
    assert code == 2


def test_empty_dir_returns_2(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    code = main(["search", "--parsed-dir", str(empty), "--query", "x"])
    assert code == 2


def test_index_returns_2_on_indexer_failure(tmp_path, monkeypatch, capsys):
    """Regression: a failed index batch must not silently succeed."""
    parsed = tmp_path / "parsed"
    _write_doc(parsed, "d1", "hello")

    from edu_rag import cli as cli_module

    async def failing_build(_dir, _provider):
        raise RuntimeError("indexing failed for 3 chunk(s); first error: boom")

    monkeypatch.setattr(cli_module, "_build_index", failing_build)

    code = main(["index", "--parsed-dir", str(parsed)])
    assert code == 2
    err = capsys.readouterr().err
    assert "indexing failed" in err


def test_search_returns_2_when_dir_has_no_documents(tmp_path, capsys):
    """Regression: a directory with no document.json must exit 2, not crash."""
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "not-a-document").mkdir()

    code = main(["search", "--parsed-dir", str(empty), "--query", "x"])
    assert code == 2
    err = capsys.readouterr().err
    assert "no document.json" in err
