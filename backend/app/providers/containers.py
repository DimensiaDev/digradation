import os
from functools import cache

from app.providers.client import AIClient
from app.providers.embeddings import (
    EmbeddingProvider,
    MockEmbeddingProvider,
    OpenAIEmbeddingProvider,
)
from app.providers.mock import MOCK_API_KEY, MockProvider
from app.providers.models import Tool
from app.providers.openai import OpenAIProvider
from app.providers.sber import SberProvider
from app.rag.config import EMBEDDING_DIMENSION

api_key = os.environ["SBER_API_KEY"]
scope = os.environ["SBER_API_SCOPE"]

TOOLS: list[Tool] = []

provider: AIClient
if api_key == MOCK_API_KEY or scope == "mock":
    provider = MockProvider(api_key=api_key, scope=scope, tools=TOOLS)
elif scope == "openai":
    provider = OpenAIProvider(api_key=api_key, scope=scope, tools=TOOLS)
else:
    provider = SberProvider(api_key=api_key, scope=scope, tools=TOOLS)


@cache
def get_embedding_provider() -> EmbeddingProvider:
    """Build (once) the embedding provider for the current scope.

    Lazy rather than eager: PERS/B2B/CORP deployments use the chat provider
    and never touch embeddings, so importing ``app.providers.containers``
    must not require ``OPENAI_*`` variables to be set. When the embedding
    path is reached under a non-openai scope, the error is immediate and
    clear.

    Mock is selected first: ``SBER_API_KEY=mock`` must override a leftover
    scope, matching the chat-provider rule above. The mock uses the same
    dimension as the pgvector column, so indexing tests exercise the real
    shape rather than a smaller parallel one.
    """
    if api_key == MOCK_API_KEY or scope == "mock":
        return MockEmbeddingProvider(model="mock-embeddings", dimension=EMBEDDING_DIMENSION)

    if scope != "openai":
        raise RuntimeError(f"embeddings require SBER_API_SCOPE=openai or mock, got {scope!r}")

    dimension = int(os.environ["OPENAI_EMBEDDING_DIMENSION"])
    if dimension != EMBEDDING_DIMENSION:
        raise RuntimeError(
            f"OPENAI_EMBEDDING_DIMENSION must be {EMBEDDING_DIMENSION} to match the rag_chunks column, got {dimension}"
        )
    return OpenAIEmbeddingProvider(
        api_key=api_key,
        model=os.environ["OPENAI_EMBEDDING_MODEL"],
        dimension=dimension,
        base_url=os.environ["OPENAI_URL"],
    )
