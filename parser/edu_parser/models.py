"""Data models for RAG document parsing."""

from pydantic import BaseModel, Field


class TextChunk(BaseModel):
    doc_id: str
    source_path: str
    section_title: str | None = None
    page_number: int | None = None
    ordinal: int
    text: str
    kind: str = "text"
    metadata: dict = Field(default_factory=dict)


class ParsedTable(BaseModel):
    doc_id: str
    source_path: str
    section_title: str | None = None
    page_number: int | None = None
    ordinal: int
    rows: list[list[str]]
    as_text: str
    metadata: dict = Field(default_factory=dict)


class ParsedImage(BaseModel):
    doc_id: str
    source_path: str
    page_number: int | None = None
    ordinal: int
    data: bytes
    ext: str
    mime: str
    metadata: dict = Field(default_factory=dict)


class ParsedDocument(BaseModel):
    doc_id: str
    source_path: str
    title: str
    text_chunks: list[TextChunk] = Field(default_factory=list)
    tables: list[ParsedTable] = Field(default_factory=list)
    images: list[ParsedImage] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
