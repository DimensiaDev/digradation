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

#: The names of the LLM presets the container understands. A deployment picks
#: one with ``LLM_PROVIDER``; leaving it unset keeps the historical behaviour,
#: where ``SBER_API_SCOPE`` decides.
LLM_PROVIDER_ENV = "LLM_PROVIDER"
LLM_PROVIDERS = frozenset({"sber", "openai", "cloudru", "mock"})

#: Cloud.ru's OpenAI-compatible endpoint and a documented Foundation Models id.
#: Both can be overridden with ``CLOUDRU_URL`` and ``CLOUDRU_MODEL``. The default
#: is the GigaChat model on Cloud.ru that advertises function calling and
#: structured output, which is what the stage tools need:
#: https://cloud.ru/docs/foundation-models/ug/topics/overview__available__models
CLOUDRU_URL = "https://foundation-models.api.cloud.ru/v1"
CLOUDRU_DEFAULT_MODEL = "ai-sage/GigaChat3.5-432B-A28B"


def _preset() -> str | None:
    """The preset the deployment asked for, or ``None`` to derive it from scope."""
    raw = os.getenv(LLM_PROVIDER_ENV, "").strip().lower()
    return raw if raw in LLM_PROVIDERS else None


def _cloudru_preset() -> None:
    """Point the OpenAI-compatible client at Cloud.ru.

    Both ``OPENAI_URL`` and ``OPENAI_MODEL`` are defaulted, not overwritten: an
    operator who set them explicitly keeps their value, and the demo path works
    with ``LLM_PROVIDER=cloudru`` alone.
    """
    os.environ.setdefault("OPENAI_URL", os.getenv("CLOUDRU_URL", CLOUDRU_URL).strip())
    os.environ.setdefault("OPENAI_MODEL", os.getenv("CLOUDRU_MODEL", CLOUDRU_DEFAULT_MODEL).strip())


api_key = os.environ["SBER_API_KEY"]
scope = os.environ["SBER_API_SCOPE"]

TOOLS: list[Tool] = []

preset = _preset()
if api_key == MOCK_API_KEY or scope == "mock" or preset == "mock":
    provider: AIClient = MockProvider(api_key=api_key, scope=scope, tools=TOOLS)
elif preset == "cloudru":
    _cloudru_preset()
    provider = OpenAIProvider(api_key=api_key, scope=scope, tools=TOOLS)
elif preset == "openai" or scope == "openai":
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
    scope, matching the chat-provider rule above. The mock's dimension is the
    index's dimension, so a test that indexes with the mock can be searched
    with the mock.

    The OpenAI path refuses a configured dimension that is not the index's.
    The ``rag_chunks.embedding`` column is ``HALFVEC(EMBEDDING_DIMENSION)``,
    and a different size would be rejected at insert time, after the first
    search, with a message that names pgvector rather than the setting. A
    different dimension is a migration, not an environment change.
    """
    if api_key == MOCK_API_KEY or scope == "mock":
        return MockEmbeddingProvider(model="mock-embeddings", dimension=EMBEDDING_DIMENSION)

    if preset == "cloudru":
        _cloudru_preset()
    if scope != "openai" and preset != "cloudru":
        raise RuntimeError(f"embeddings require SBER_API_SCOPE=openai or LLM_PROVIDER=cloudru, got {scope!r}")

    dimension = int(os.environ["OPENAI_EMBEDDING_DIMENSION"])
    if dimension != EMBEDDING_DIMENSION:
        raise RuntimeError(
            f"OPENAI_EMBEDDING_DIMENSION={dimension} does not match the index dimension "
            f"{EMBEDDING_DIMENSION}; a different size needs a migration, not an env change"
        )
    return OpenAIEmbeddingProvider(
        api_key=api_key,
        model=os.environ["OPENAI_EMBEDDING_MODEL"],
        dimension=dimension,
        base_url=os.environ["OPENAI_URL"],
    )
