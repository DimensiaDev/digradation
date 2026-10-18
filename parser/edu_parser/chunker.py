"""Chunking logic: split text into RAG-friendly fragments, render tables."""

import re

from .models import TextChunk

DEFAULT_MAX_SIZE = 1500
DEFAULT_OVERLAP = 150


def _safe_tail(text: str, overlap: int) -> str:
    """Take last ~overlap chars of text, snapped forward to a word boundary.

    Returns an empty string when ``overlap <= 0``. Without this guard,
    ``text[-0:]`` would equal ``text[0:]`` (the whole string) and the
    entire buffer would be carried into the next chunk, duplicating
    content and blowing past ``max_size``.

    ``strip()`` at the end trims trailing whitespace that survives the
    word-boundary snap. It does *not* replace the regex: cutting the
    tail at the first space is what prevents a truncated leading word
    ("...интеллек" -> "интеллекта") from leaking into the next chunk.
    """
    if overlap <= 0:
        return ""
    if len(text) <= overlap:
        return text.strip()
    tail = text[-overlap:]
    m = re.search(r"\s", tail)
    if m:
        tail = tail[m.start() + 1 :]
    return tail.strip()


def _split_long(text: str, max_size: int) -> list[str]:
    """Split a single paragraph that exceeds max_size at whitespace."""
    if len(text) <= max_size:
        return [text]

    parts: list[str] = []
    while len(text) > max_size:
        cut = text.rfind(" ", 0, max_size)
        if cut <= 0:
            cut = max_size
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        parts.append(text)
    return parts


def chunk_paragraphs(
    paragraphs: list[str],
    doc_id: str,
    source_path: str,
    section_title: str | None,
    page_number: int | None,
    start_ordinal: int = 0,
    max_size: int = DEFAULT_MAX_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[TextChunk]:
    """Accumulate paragraph texts into chunks of ~max_size with overlap.

    Every non-empty trailing buffer is always emitted.

    ``overlap`` is clamped to ``[0, max_size // 2]``. This prevents two
    pathological cases:

    * ``overlap == 0`` would cause ``_safe_tail`` to return the whole
      buffer (``text[-0:] == text[0:]``), duplicating content.
    * ``overlap >= max_size`` would cause every chunk after the first to
      exceed ``max_size``, breaking the size contract.
    """
    if overlap < 0:
        overlap = 0
    elif overlap >= max_size:
        overlap = max(0, max_size // 2)

    chunks: list[TextChunk] = []
    buffer = ""
    ordinal = start_ordinal

    def emit(text: str, ord_: int) -> None:
        chunks.append(
            TextChunk(
                doc_id=doc_id,
                source_path=source_path,
                section_title=section_title,
                page_number=page_number,
                ordinal=ord_,
                text=text.strip(),
                kind="text",
            )
        )

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        pieces = _split_long(para, max_size) if len(para) > max_size else [para]

        for piece in pieces:
            if buffer and len(buffer) + len(piece) + 2 > max_size:
                emit(buffer, ordinal)
                ordinal += 1
                tail = _safe_tail(buffer, overlap)
                buffer = (tail + "\n\n" + piece).strip() if tail else piece
            else:
                buffer = (buffer + "\n\n" + piece).strip() if buffer else piece

    if buffer.strip():
        emit(buffer, ordinal)

    return chunks


def _escape_cell(value: str) -> str:
    """Escape pipes and normalize whitespace inside a markdown table cell."""
    return value.replace("\n", " ").replace("|", r"\|").strip()


def table_to_markdown(rows: list[list[str]]) -> str:
    """Render a table as GitHub-flavored markdown (safe for embedding)."""
    if not rows:
        return ""

    ncols = max(len(r) for r in rows)
    norm = [[_escape_cell(c or "") for c in (r + [""] * (ncols - len(r)))] for r in rows]
    header = norm[0]
    body = norm[1:] if len(norm) > 1 else []

    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * ncols) + " |",
    ]
    for r in body:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)
