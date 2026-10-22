"""Configuration dataclasses for the rag module."""

from dataclasses import dataclass


@dataclass(frozen=True)
class EmbeddingConfig:
    """Embedding provider configuration.

    Defaults match the school-hub embedding model used by this project. The
    backend stores these vectors in a ``halfvec(2048)`` column, so changing the
    default dimension needs a migration rather than only a config edit. All
    numeric fields are validated in ``__post_init__`` — a nonsensical config
    fails at construction, not mid-indexing.
    """

    model: str = "ai-sage/Giga-Embeddings-instruct-3B"
    dimension: int = 2048
    batch_size: int = 20
    max_tokens: int = 512
    retries: int = 5
    timeout_s: float = 30.0

    def __post_init__(self) -> None:
        if not self.model:
            raise ValueError("model must be non-empty")
        if self.dimension <= 0:
            raise ValueError(f"dimension must be positive, got {self.dimension}")
        if self.batch_size <= 0:
            raise ValueError(f"batch_size must be positive, got {self.batch_size}")
        if self.max_tokens <= 0:
            raise ValueError(f"max_tokens must be positive, got {self.max_tokens}")
        if self.retries < 0:
            raise ValueError(f"retries must be >= 0, got {self.retries}")
        if self.timeout_s <= 0:
            raise ValueError(f"timeout_s must be positive, got {self.timeout_s}")


@dataclass(frozen=True)
class StoreConfig:
    """Vector store configuration."""

    table: str = "rag_chunks"
    hnsw_m: int = 16
    hnsw_ef_construction: int = 64
