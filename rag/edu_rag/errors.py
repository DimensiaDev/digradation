"""Exception hierarchy for the rag module."""


class RagError(Exception):
    """Base class for all rag-module errors."""


class EmbeddingError(RagError):
    """Embedding provider failed (4xx, exhausted retries, dimension mismatch)."""


class StoreError(RagError):
    """Vector store operation failed."""
