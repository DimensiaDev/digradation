"""Smoke tests: chunker, parsers, pipeline."""

from pathlib import Path

import pytest
from docx import Document

from edu_parser.chunker import chunk_paragraphs, table_to_markdown
from edu_parser.parsers import parse_docx
from edu_parser.pipeline import _doc_id_for, process_directory


def test_chunk_paragraphs_basic():
    paras = [f"Paragraph number {i} with some body text." for i in range(50)]
    chunks = chunk_paragraphs(
        paragraphs=paras,
        doc_id="d1",
        source_path="x.docx",
        section_title="Section 1",
        page_number=1,
        max_size=300,
        overlap=50,
    )
    assert len(chunks) > 1
    for c in chunks:
        assert c.doc_id == "d1"
        assert c.section_title == "Section 1"
        assert len(c.text) <= 400


def test_chunk_trailing_buffer_emitted():
    """Regression #9: a short trailing buffer must not be dropped."""
    paras = ["A" * 400, "B" * 400, "short tail"]
    chunks = chunk_paragraphs(
        paragraphs=paras,
        doc_id="d1",
        source_path="x.docx",
        section_title=None,
        page_number=None,
        max_size=500,
        overlap=50,
    )
    assert any("short tail" in c.text for c in chunks)


def test_chunk_long_paragraph_split():
    """Regression #10: paragraphs longer than max_size must be split."""
    big = "word " * 1000
    chunks = chunk_paragraphs(
        paragraphs=[big],
        doc_id="d1",
        source_path="x.docx",
        section_title=None,
        page_number=None,
        max_size=500,
        overlap=50,
    )
    assert len(chunks) > 1
    for c in chunks:
        assert len(c.text) <= 600


def test_table_to_markdown():
    md = table_to_markdown([["Competency", "Indicator"], ["UC-1", "..."]])
    assert "| Competency | Indicator |" in md
    assert "| UC-1 | ... |" in md


def test_table_to_markdown_escapes_pipe():
    """Regression #20: pipes inside a cell must be escaped."""
    md = table_to_markdown([["A", "B"], ["x|y", "z"]])
    assert r"x\|y" in md


def test_parse_docx_smoke(tmp_path: Path):
    src = tmp_path / "sample.docx"
    d = Document()
    d.add_heading("Section 1", level=1)
    d.add_paragraph("Body text of the first section.")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "Competency"
    t.cell(0, 1).text = "Indicator"
    t.cell(1, 0).text = "UC-1"
    t.cell(1, 1).text = "Example"
    d.save(src)

    doc = parse_docx(src, doc_id="d1", source_path="sample.docx")
    assert any(c.section_title and "Section 1" in c.section_title for c in doc.text_chunks)
    assert len(doc.tables) == 1
    assert doc.tables[0].rows[0][0] == "Competency"


def test_parse_docx_heading_kept_in_chunks(tmp_path: Path):
    """Regression #11: heading text must not disappear from chunk content."""
    src = tmp_path / "headings.docx"
    d = Document()
    d.add_paragraph("1. Introduction to Python")
    d.add_paragraph("A regular paragraph that follows.")
    d.save(src)

    doc = parse_docx(src, doc_id="d1", source_path="headings.docx")
    joined = "\n".join(c.text for c in doc.text_chunks)
    assert "Introduction to Python" in joined


def test_process_directory_isolates_errors(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    d = Document()
    d.add_paragraph("Hello")
    d.save(src / "ok.docx")
    (src / "broken.pdf").write_bytes(b"not a pdf at all")

    out = tmp_path / "out"
    results = process_directory(src, out)
    assert any(r["ok"] for r in results)
    assert any(not r["ok"] for r in results)
    assert (out / "manifest.json").exists()


def test_process_directory_rejects_missing_input(tmp_path: Path):
    """Regression #13: missing input root must raise, not silently succeed."""
    with pytest.raises(NotADirectoryError):
        process_directory(tmp_path / "does-not-exist", tmp_path / "out")


def test_process_directory_limit_zero(tmp_path: Path):
    """Regression #14: limit=0 must process zero files, not all."""
    src = tmp_path / "src"
    src.mkdir()
    d = Document()
    d.add_paragraph("X")
    d.save(src / "a.docx")

    results = process_directory(src, tmp_path / "out", limit=0)
    assert results == []


def test_process_directory_skips_symlink(tmp_path: Path):
    """Regression #3: symlinks pointing outside the root must be rejected."""
    src = tmp_path / "src"
    src.mkdir()
    real = tmp_path / "outside.docx"
    d = Document()
    d.add_paragraph("secret")
    d.save(real)
    link = src / "link.docx"
    link.symlink_to(real)

    out = tmp_path / "out"
    results = process_directory(src, out)
    assert all("link.docx" not in r["source_path"] for r in results)


def test_doc_id_includes_input_root(tmp_path: Path):
    """Regression #4: same rel path from different roots must not collide."""
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    assert _doc_id_for("x.docx", root_a) != _doc_id_for("x.docx", root_b)


def test_chunk_overlap_zero_does_not_duplicate():
    """Regression: overlap=0 must not carry the whole buffer forward."""
    paras = [f"Paragraph {i} with some body text." for i in range(20)]
    chunks = chunk_paragraphs(
        paragraphs=paras,
        doc_id="d1",
        source_path="x.docx",
        section_title=None,
        page_number=None,
        max_size=200,
        overlap=0,
    )
    assert len(chunks) > 1
    for i in range(1, len(chunks)):
        assert chunks[i - 1].text not in chunks[i].text


def test_chunk_overlap_larger_than_max_is_clamped():
    """Regression: overlap >= max_size must be clamped, not blow up chunks."""
    paras = [f"Para {i}: " + ("x" * 100) for i in range(20)]
    chunks = chunk_paragraphs(
        paragraphs=paras,
        doc_id="d1",
        source_path="x.docx",
        section_title=None,
        page_number=None,
        max_size=300,
        overlap=1000,
    )
    assert len(chunks) > 1
    for c in chunks:
        assert len(c.text) <= 500


def test_chunk_negative_overlap_clamped_to_zero():
    """Regression: negative overlap must not raise and must not duplicate."""
    paras = ["A" * 100, "B" * 100]
    chunks = chunk_paragraphs(
        paragraphs=paras,
        doc_id="d1",
        source_path="x.docx",
        section_title=None,
        page_number=None,
        max_size=150,
        overlap=-50,
    )
    assert len(chunks) >= 1
    joined = "\n".join(c.text for c in chunks)
    assert "A" * 100 in joined
    assert "B" * 100 in joined
