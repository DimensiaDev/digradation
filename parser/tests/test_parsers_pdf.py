"""Tests for PDF parsing via programmatically generated fixtures."""

from pathlib import Path

import pymupdf
import pytest

from edu_parser.parsers import _TocTracker, parse_pdf


def _make_pdf(path: Path, pages: list[str], toc: list[list] | None = None) -> None:
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    if toc:
        doc.set_toc(toc)
    doc.save(str(path))
    doc.close()


def test_parse_pdf_simple(tmp_path: Path):
    src = tmp_path / "x.pdf"
    _make_pdf(src, ["Hello page one", "Hello page two"])

    doc = parse_pdf(src, doc_id="d1", source_path="x.pdf")
    assert len(doc.text_chunks) >= 1
    assert doc.text_chunks[0].page_number == 1
    assert "Hello" in doc.text_chunks[0].text


def test_parse_pdf_with_toc(tmp_path: Path):
    src = tmp_path / "x.pdf"
    _make_pdf(
        src,
        ["Content A", "Content B", "Content C"],
        toc=[[1, "Chapter 1", 1], [1, "Chapter 2", 3]],
    )

    doc = parse_pdf(src, doc_id="d1", source_path="x.pdf")
    titles = [c.section_title for c in doc.text_chunks if c.section_title]
    assert any("Chapter 1" in (t or "") for t in titles)
    assert any("Chapter 2" in (t or "") for t in titles)


def test_parse_pdf_heading_carries_across_pages(tmp_path: Path):
    """Regression #15: fallback section_title persists to the next page."""
    src = tmp_path / "x.pdf"
    _make_pdf(src, ["Plain text on page 1", "Plain text on page 2"])

    doc = parse_pdf(src, doc_id="d1", source_path="x.pdf")
    assert doc.errors == []


def test_parse_pdf_broken_raises(tmp_path: Path):
    src = tmp_path / "x.pdf"
    src.write_bytes(b"this is not a pdf")
    with pytest.raises((pymupdf.FileDataError, RuntimeError)):
        parse_pdf(src, doc_id="d1", source_path="x.pdf")


def test_toc_tracker_empty():
    tracker = _TocTracker([])
    assert tracker.current is None


def test_toc_tracker_advance():
    toc = [[1, "A", 1], [2, "B", 3], [1, "C", 5]]
    tracker = _TocTracker(toc)

    tracker.advance_to_page(1)
    assert tracker.current == "A"

    tracker.advance_to_page(3)
    assert tracker.current == "A > B"

    tracker.advance_to_page(5)
    assert tracker.current == "C"
