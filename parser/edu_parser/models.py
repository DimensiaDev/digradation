"""Data models for RAG document parsing."""

from dataclasses import dataclass, field


@dataclass
class TextChunk:
    doc_id: str
    source_path: str
    section_title: str | None
    page_number: int | None
    ordinal: int
    text: str
    kind: str = "text"  # "text" | "table"
    metadata: dict = field(default_factory=dict)


@dataclass
class ParsedTable:
    doc_id: str
    source_path: str
    section_title: str | None
    page_number: int | None
    ordinal: int
    rows: list[list[str]]
    as_text: str
    metadata: dict = field(default_factory=dict)


@dataclass
class ParsedImage:
    doc_id: str
    source_path: str
    page_number: int | None
    ordinal: int
    data: bytes
    ext: str
    mime: str
    metadata: dict = field(default_factory=dict)


@dataclass
class ParsedDocument:
    doc_id: str
    source_path: str
    title: str
    text_chunks: list[TextChunk] = field(default_factory=list)
    tables: list[ParsedTable] = field(default_factory=list)
    images: list[ParsedImage] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
