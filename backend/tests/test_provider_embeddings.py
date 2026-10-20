"""Unit tests for :mod:`app.providers.embeddings`.

Nothing here touches the network. The provider builds a real
``AsyncOpenAI``, and every test swaps ``provider._client`` for a small
fake whose ``embeddings.create`` is an ``AsyncMock`` returning a shape
that matches what the SDK returns. That keeps the mapping under test
against the SDK's own attribute names without opening a socket.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from edu_rag.errors import EmbeddingError
from openai import (
    APIConnectionError,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)

from app.providers.embeddings import (
    DEFAULT_EMBEDDING_DIMENSION,
    DEFAULT_EMBEDDING_MODEL,
    OpenAIEmbeddingProvider,
)


def make_response(vectors: list[list[float]], *, swap: bool = False) -> SimpleNamespace:
    """Build a CreateEmbeddingResponse-shaped object.

    ``swap`` reverses the ``data`` order without changing the indices, to
    simulate an API that returns results out of order.
    """
    items = [SimpleNamespace(embedding=vec, index=i, object="embedding") for i, vec in enumerate(vectors)]
    if swap:
        items = list(reversed(items))
    return SimpleNamespace(
        data=items,
        model="ai-sage/Giga-Embeddings-instruct-3B",
        usage=SimpleNamespace(prompt_tokens=10, total_tokens=10),
    )


def vec(dim: int = 8, fill: float = 0.5) -> list[float]:
    return [fill] * dim


@pytest_asyncio.fixture
async def make_provider():
    """Build an ``OpenAIEmbeddingProvider`` with a fake SDK client.

    The real ``AsyncOpenAI`` built by ``__init__`` is never used, but it
    owns an http client, so it is closed on the way out.
    """
    originals = []

    def _make(
        response: SimpleNamespace | None = None,
        *,
        model: str | None = None,
        dimension: int = 8,
        max_retries: int = 0,
    ) -> tuple[OpenAIEmbeddingProvider, MagicMock]:
        provider = OpenAIEmbeddingProvider(
            api_key="key",
            model=model,
            dimension=dimension,
            base_url="https://hub.example.test/v1",
            max_retries=max_retries,
        )
        originals.append(provider._client)
        client = MagicMock()
        client.embeddings.create = AsyncMock(return_value=response)
        client.close = AsyncMock()
        provider._client = client
        return provider, client

    yield _make

    for client in originals:
        await client.close()


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


def test_defaults_come_from_constants(monkeypatch):
    monkeypatch.delenv("OPENAI_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_EMBEDDING_DIMENSION", raising=False)
    provider = OpenAIEmbeddingProvider(api_key="key", base_url="https://x.test/v1")

    assert provider.model == DEFAULT_EMBEDDING_MODEL
    assert provider.dimension == DEFAULT_EMBEDDING_DIMENSION


def test_env_overrides_defaults(monkeypatch):
    monkeypatch.setenv("OPENAI_EMBEDDING_MODEL", "custom-model")
    monkeypatch.setenv("OPENAI_EMBEDDING_DIMENSION", "1024")
    provider = OpenAIEmbeddingProvider(api_key="key", base_url="https://x.test/v1")

    assert provider.model == "custom-model"
    assert provider.dimension == 1024


def test_explicit_args_win_over_env(monkeypatch):
    monkeypatch.setenv("OPENAI_EMBEDDING_MODEL", "env-model")
    monkeypatch.setenv("OPENAI_EMBEDDING_DIMENSION", "1024")
    provider = OpenAIEmbeddingProvider(
        api_key="key",
        model="explicit-model",
        dimension=512,
        base_url="https://x.test/v1",
    )

    assert provider.model == "explicit-model"
    assert provider.dimension == 512


def test_rejects_nonpositive_dimension():
    with pytest.raises(ValueError, match="dimension"):
        OpenAIEmbeddingProvider(
            api_key="key",
            dimension=0,
            base_url="https://x.test/v1",
        )


# ---------------------------------------------------------------------------
# embed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_empty_returns_empty_without_call(make_provider):
    provider, client = make_provider()

    result = await provider.embed([])

    assert result == []
    client.embeddings.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_embed_single(make_provider):
    provider, client = make_provider(make_response([vec()]))

    result = await provider.embed(["hello"])

    assert result == [vec()]
    kwargs = client.embeddings.create.call_args.kwargs
    assert kwargs["model"] == provider.model
    assert kwargs["input"] == ["hello"]


@pytest.mark.asyncio
async def test_embed_preserves_order_when_response_is_swapped(make_provider):
    # Three vectors distinguished by their first coordinate.
    v0 = [0.0] * 8
    v1 = [1.0] * 8
    v2 = [2.0] * 8
    provider, _ = make_provider(make_response([v0, v1, v2], swap=True))

    result = await provider.embed(["a", "b", "c"])

    assert result == [v0, v1, v2]


@pytest.mark.asyncio
async def test_embed_raises_on_wrong_count(make_provider):
    provider, _ = make_provider(make_response([vec()]))

    with pytest.raises(EmbeddingError, match="1 embeddings for 2 inputs"):
        await provider.embed(["a", "b"])


@pytest.mark.asyncio
async def test_embed_raises_on_wrong_dimension(make_provider):
    provider, _ = make_provider(make_response([vec(dim=4)]), dimension=8)

    with pytest.raises(EmbeddingError, match="dimension 4, expected 8"):
        await provider.embed(["a"])


@pytest.mark.asyncio
async def test_embed_wraps_authentication_error(make_provider):
    provider, client = make_provider()
    client.embeddings.create.side_effect = AuthenticationError("no", response=MagicMock(status_code=401), body=None)

    with pytest.raises(EmbeddingError, match="auth failed"):
        await provider.embed(["a"])


@pytest.mark.asyncio
async def test_embed_wraps_rate_limit_error(make_provider):
    provider, client = make_provider()
    client.embeddings.create.side_effect = RateLimitError("slow down", response=MagicMock(status_code=429), body=None)

    with pytest.raises(EmbeddingError, match="transient failure"):
        await provider.embed(["a"])


@pytest.mark.asyncio
async def test_embed_wraps_bad_request_error(make_provider):
    provider, client = make_provider()
    client.embeddings.create.side_effect = BadRequestError("bad", response=MagicMock(status_code=400), body=None)

    with pytest.raises(EmbeddingError, match="bad request"):
        await provider.embed(["a"])


@pytest.mark.asyncio
async def test_embed_wraps_connection_error(make_provider):
    provider, client = make_provider()
    client.embeddings.create.side_effect = APIConnectionError(request=MagicMock())

    with pytest.raises(EmbeddingError, match="transient failure"):
        await provider.embed(["a"])


@pytest.mark.asyncio
async def test_embed_wraps_unexpected_error(make_provider):
    provider, client = make_provider()
    client.embeddings.create.side_effect = RuntimeError("boom")

    with pytest.raises(EmbeddingError, match="embedding failed"):
        await provider.embed(["a"])


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_closes_the_client(make_provider):
    provider, client = make_provider()

    await provider.close()

    client.close.assert_awaited_once()
