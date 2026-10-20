"""DI helpers for RAG. Kept separate from deps.py so the chat-only
import path does not pull in edu_rag."""

import os
from functools import cache


@cache
def get_embedding_provider():
    """Build the embedding provider for the current scope.

    Only ``openai`` and ``mock`` scopes are supported. PERS/B2B/CORP use
    the GigaChat client for chat, which has no embeddings endpoint — the
    school-hub router is the way to reach ``/embeddings``.
    """
    from app.providers.containers import api_key, scope

    if api_key == "mock" or scope == "mock":
        from edu_rag import EmbeddingConfig
        from edu_rag.embeddings.mock import MockEmbeddingProvider

        return MockEmbeddingProvider(
            EmbeddingConfig(
                model=os.getenv("OPENAI_EMBEDDING_MODEL", "mock-embeddings"),
                dimension=int(os.getenv("OPENAI_EMBEDDING_DIMENSION", "2048")),
            )
        )

    if scope != "openai":
        raise RuntimeError(f"embeddings require SBER_API_SCOPE=openai or mock, got {scope!r}")

    from app.providers.embeddings import OpenAIEmbeddingProvider

    return OpenAIEmbeddingProvider(api_key=api_key)
