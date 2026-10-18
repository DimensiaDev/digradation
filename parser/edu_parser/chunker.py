"""Chunking logic: split text into RAG-friendly fragments, render tables."""

import re

from .models import TextChunk

DEFAULT_MAX_SIZE = 1500
DEFAULT_MIN_SIZE = 200
DEFAULT_OVERLAP = 150


def _safe_tail(text: str, overlap: int) -> str:
    """Take last ~overlap chars of text, snapped forward to a word boundary."""
    if len(text) <= overlap:
        return text
    tail = text[-overlap:]
    m = re.search(r"\s", tail)
    if m:
        tail = tail[m.start() + 1 :]
    return tail


def chunk_paragraphs(
    paragraphs: list[str],
    doc_id: str,
    source_path: str,
    section_title: str | None,
    page_number: int | None,
    start_ordinal: int = 0,
    max_size: int = DEFAULT_MAX_SIZE,
    min_size: int = DEFAULT_MIN_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[TextChunk]:
    """Accumulate paragraph texts into chunks of ~max_size with overlap."""
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

        if buffer and len(buffer) + len(para) + 2 > max_size:
            emit(buffer, ordinal)
            ordinal += 1
            buffer = (_safe_tail(buffer, overlap) + "\n\n" + para).strip()
        else:
            buffer = (buffer + "\n\n" + para).strip() if buffer else para

    if buffer.strip() and (len(buffer.strip()) >= min_size or not chunks):
        emit(buffer, ordinal)

    return chunks


def table_to_markdown(rows: list[list[str]]) -> str:
    """Render a table as GitHub-flavored markdown (safe for embedding)."""
    if not rows:
        return ""

    ncols = max(len(r) for r in rows)
    norm = [[(c or "").strip().replace("\n", " ") for c in (r + [""] * (ncols - len(r)))] for r in rows]
    header = norm[0]
    body = norm[1:] if len(norm) > 1 else []

    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * ncols) + " |",
    ]
    for r in body:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)
