"""Pydantic models used as the public contract of the rag module."""

from pydantic import BaseModel, Field


class ChunkRecord(BaseModel):
    """One chunk ready for embedding + storage.

    Built from a parser ``document.json`` entry (text_chunk or table).
    ``content_hash`` is computed by the caller (indexer helper) and used
    for idempotency: unchanged chunks are skipped on re-index.
    """

    doc_id: str
    ordinal: int
    kind: str
    section_title: str | None = None
    page_number: int | None = None
    content: str
    content_hash: str
    metadata: dict = Field(default_factory=dict)


class SearchHit(BaseModel):
    """One search result returned by a VectorStore."""

    doc_id: str
    ordinal: int
    kind: str
    section_title: str | None
    page_number: int | None
    content: str
    score: float


class IndexResult(BaseModel):
    """Outcome of a single ``DocumentIndexer.index`` call.

    Counts partition the total number of input chunks:
    ``indexed + skipped + failed == len(chunks)`` for a non-empty input.
    ``deleted`` is independent — it reports orphan rows pruned from the
    store, which may include chunks left over from earlier runs.
    """

    doc_id: str
    indexed: int = 0
    skipped: int = 0
    deleted: int = 0
    failed: int = 0
    errors: list[str] = Field(default_factory=list)

    @property
    def total(self) -> int:
        return self.indexed + self.skipped + self.failed
