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

    def to_payload(self, saved_rel: str | None = None) -> dict:
        """Serialize to the ``document.json`` image entry.

        - ``data`` is excluded: raw bytes live in ``images/0000.png`` and
          the JSON carries only ``bytes_size`` and the relative ``path``.
        - ``doc_id`` and ``source_path`` are excluded: they are already
          present at the top level of the parent document payload, so
          repeating them per image would just bloat ``document.json``.
        - ``bytes_size`` and ``path`` are derived on the fly: the first
          from ``len(self.data)``, the second from the caller (``None``
          when the image was not written to disk).
        """
        payload = self.model_dump(exclude={"data", "doc_id", "source_path"})
        payload["bytes_size"] = len(self.data)
        payload["path"] = saved_rel
        return payload


class ParsedDocument(BaseModel):
    doc_id: str
    source_path: str
    title: str
    text_chunks: list[TextChunk] = Field(default_factory=list)
    tables: list[ParsedTable] = Field(default_factory=list)
    images: list[ParsedImage] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
