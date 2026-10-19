"""Configuration dataclasses for the rag module.

No environment reading here — the caller (backend) owns config.
The module accepts these dataclasses as arguments.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class EmbeddingConfig:
    """Embedding provider configuration.

    Defaults match GigaChat Embeddings (1024 dims, 512 tokens context).
    The backend overrides these from RAG_EMBEDDING_* settings.
    """

    model: str = "Embeddings"
    dimension: int = 1024
    batch_size: int = 20
    max_tokens: int = 512
    retries: int = 5
    timeout_s: float = 30.0


@dataclass(frozen=True)
class StoreConfig:
    """Vector store configuration."""

    table: str = "rag_chunks"
    hnsw_m: int = 16
    hnsw_ef_construction: int = 64
