"""Edge-case tests for the chunker."""

from edu_parser.chunker import _safe_tail, _split_long, chunk_paragraphs


def test_safe_tail_zero_returns_empty():
    assert _safe_tail("some text here", 0) == ""


def test_safe_tail_negative_returns_empty():
    assert _safe_tail("some text here", -5) == ""


def test_safe_tail_short_text_returns_whole():
    assert _safe_tail("short", 100) == "short"


def test_safe_tail_snaps_to_word_boundary():
    tail = _safe_tail("Hello world foobar", 10)
    assert "foobar" in tail
    assert not tail.startswith("o")


def test_split_long_single_chunk():
    assert _split_long("short text", 100) == ["short text"]


def test_split_long_multiple_chunks():
    text = "word " * 100
    parts = _split_long(text, 100)
    assert len(parts) > 1
    for p in parts:
        assert len(p) <= 100


def test_chunk_paragraphs_empty_input():
    chunks = chunk_paragraphs(
        paragraphs=[],
        doc_id="d1",
        source_path="x.docx",
        section_title=None,
        page_number=None,
    )
    assert chunks == []


def test_chunk_paragraphs_whitespace_only():
    chunks = chunk_paragraphs(
        paragraphs=["", "  ", "\n\n"],
        doc_id="d1",
        source_path="x.docx",
        section_title=None,
        page_number=None,
    )
    assert chunks == []


def test_split_long_exact_multiple():
    """Regression for coverage: text that splits exactly, no remainder."""
    text = "a" * 100
    parts = _split_long(text, 100)
    assert parts == [text]


def test_split_long_with_remainder():
    """Cover the `if text: parts.append(text)` branch."""
    text = "word " * 25
    parts = _split_long(text, 100)
    assert len(parts) == 2
    joined = " ".join(parts)
    assert joined.count("word") == 25


def test_chunk_paragraphs_overlap_tail_used():
    """Cover the `_safe_tail` call path when overlap > 0 and buffer is long."""
    paras = ["A" * 250, "B" * 250, "C" * 250]
    chunks = chunk_paragraphs(
        paragraphs=paras,
        doc_id="d1",
        source_path="x.docx",
        section_title=None,
        page_number=None,
        max_size=300,
        overlap=100,
    )
    assert len(chunks) >= 2
