"""Pydantic models used as the public contract of the rag module."""

import hashlib
import json

from pydantic import BaseModel, Field, model_validator


class ChunkRecord(BaseModel):
    """One chunk ready for embedding + storage.

    ``content_hash`` is verified against ``sha256(content)`` — callers
    cannot silently pass a stale hash alongside new content and have
    the chunk skipped as "unchanged".
    """

    doc_id: str
    ordinal: int
    kind: str
    section_title: str | None = None
    page_number: int | None = None
    content: str
    content_hash: str
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _verify_content_hash(self) -> ChunkRecord:
        expected = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        if self.content_hash != expected:
            raise ValueError(
                "content_hash does not match sha256(content); " "recompute it in the caller or omit the field"
            )
        return self

    def fingerprint(self, model: str, dimension: int) -> str:
        """Stable hash of everything that decides whether a chunk is up-to-date.

        Includes content, location, metadata and the embedding model +
        dimension. If any of them changes, the chunk is re-embedded.
        """
        payload = json.dumps(
            {
                "content_hash": self.content_hash,
                "section_title": self.section_title,
                "page_number": self.page_number,
                "metadata": self.metadata,
                "model": model,
                "dimension": dimension,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SearchHit(BaseModel):
    """One search result returned by a VectorStore."""

    doc_id: str
    ordinal: int
    kind: str
    section_title: str | None
    page_number: int | None
    content: str
    score: float
    metadata: dict = Field(default_factory=dict)


class IndexResult(BaseModel):
    """Outcome of a single ``DocumentIndexer.index`` call."""

    doc_id: str
    indexed: int = 0
    skipped: int = 0
    deleted: int = 0
    failed: int = 0
    errors: list[str] = Field(default_factory=list)

    @property
    def total(self) -> int:
        return self.indexed + self.skipped + self.failed
