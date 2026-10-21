"""Tests for :mod:`app.providers.embeddings`.

The mock provider is exercised directly — no SDK mocking, no network.
The OpenAI provider's tests swap its ``_client`` for a small fake whose
``embeddings.create`` is an ``AsyncMock``, matching the pattern used in
``test_providers_openai.py``.
"""

from __future__ import annotations

import math
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
    EmbeddingProvider,
    MockEmbeddingProvider,
    OpenAIEmbeddingProvider,
)

# ---------------------------------------------------------------------------
# Mock provider
# ---------------------------------------------------------------------------


def test_mock_implements_interface():
    assert isinstance(MockEmbeddingProvider(), EmbeddingProvider)


def test_mock_defaults():
    p = MockEmbeddingProvider()
    assert p.model == "mock-embeddings"
    assert p.dimension == 1024


def test_mock_rejects_nonpositive_dimension():
    with pytest.raises(ValueError, match="dimension"):
        MockEmbeddingProvider(dimension=0)


@pytest.mark.asyncio
async def test_mock_returns_empty_for_empty_input():
    assert await MockEmbeddingProvider().embed([]) == []


@pytest.mark.asyncio
async def test_mock_returns_one_per_text():
    p = MockEmbeddingProvider(dimension=8)
    vectors = await p.embed(["a", "b", "c"])
    assert len(vectors) == 3
    assert all(len(v) == 8 for v in vectors)


@pytest.mark.asyncio
async def test_mock_is_deterministic():
    p = MockEmbeddingProvider(dimension=8)
    a = await p.embed(["hello"])
    b = await p.embed(["hello"])
    assert a == b


@pytest.mark.asyncio
async def test_mock_vectors_are_normalized():
    p = MockEmbeddingProvider(dimension=8)
    [vec] = await p.embed(["hello"])
    norm = math.sqrt(sum(v * v for v in vec))
    assert math.isclose(norm, 1.0, rel_tol=1e-6)


@pytest.mark.asyncio
async def test_mock_empty_text_gives_zero_vector():
    p = MockEmbeddingProvider(dimension=8)
    [vec] = await p.embed([""])
    assert vec == [0.0] * 8


@pytest.mark.asyncio
async def test_mock_distinct_texts_distinct_vectors():
    p = MockEmbeddingProvider(dimension=8)
    [a] = await p.embed(["hello"])
    [b] = await p.embed(["world"])
    assert a != b


@pytest.mark.asyncio
async def test_mock_close_is_noop():
    await MockEmbeddingProvider().close()


# ---------------------------------------------------------------------------
# OpenAI provider — constructor
# ---------------------------------------------------------------------------


def _vec(dim: int = 8) -> list[float]:
    return [0.5] * dim


def _response(vectors: list[list[float]], indices: list[int] | None = None):
    items = [
        SimpleNamespace(embedding=vec, index=idx, object="embedding")
        for idx, vec in enumerate(vectors)
        if indices is None
    ]
    if indices is not None:
        items = [
            SimpleNamespace(embedding=v, index=i, object="embedding") for v, i in zip(vectors, indices, strict=True)
        ]
    return SimpleNamespace(data=items, model="m", usage=SimpleNamespace())


def _make_openai(
    *,
    model: str = "test-model",
    dimension: int = 8,
    max_batch_size: int = 32,
) -> OpenAIEmbeddingProvider:
    return OpenAIEmbeddingProvider(
        api_key="key",
        model=model,
        dimension=dimension,
        base_url="https://hub.example.test/v1",
        max_batch_size=max_batch_size,
        max_retries=0,
    )


def test_openai_rejects_empty_model():
    with pytest.raises(ValueError, match="model"):
        _make_openai(model="")


def test_openai_rejects_nonpositive_dimension():
    with pytest.raises(ValueError, match="dimension"):
        _make_openai(dimension=0)


def test_openai_rejects_empty_base_url():
    with pytest.raises(ValueError, match="base_url"):
        OpenAIEmbeddingProvider(api_key="k", model="m", dimension=8, base_url="")


def test_openai_implements_interface():
    assert isinstance(_make_openai(), EmbeddingProvider)


# ---------------------------------------------------------------------------
# OpenAI provider — embed
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def patched_openai():
    """Factory fixture: returns (provider, mock_client) with SDK swapped."""
    originals = []

    def _make(response):
        provider = _make_openai()
        originals.append(provider._client)
        client = MagicMock()
        client.embeddings.create = AsyncMock(return_value=response)
        client.close = AsyncMock()
        provider._client = client
        return provider, client

    yield _make

    for c in originals:
        await c.close()


@pytest.mark.asyncio
async def test_openai_embed_empty_noop(patched_openai):
    provider, client = patched_openai(_response([_vec()]))
    assert await provider.embed([]) == []
    client.embeddings.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_openai_embed_single(patched_openai):
    provider, client = patched_openai(_response([_vec()]))
    result = await provider.embed(["hello"])
    assert result == [_vec()]
    assert client.embeddings.create.call_args.kwargs == {
        "model": "test-model",
        "input": ["hello"],
    }


@pytest.mark.asyncio
async def test_openai_reorders_by_index(patched_openai):
    v0 = [0.0] * 8
    v1 = [1.0] * 8
    v2 = [2.0] * 8
    response = _response([v2, v0, v1], indices=[2, 0, 1])
    provider, _ = patched_openai(response)
    assert await provider.embed(["a", "b", "c"]) == [v0, v1, v2]


@pytest.mark.asyncio
async def test_openai_rejects_duplicate_indices(patched_openai):
    response = _response([_vec(), _vec()], indices=[0, 0])
    provider, _ = patched_openai(response)
    with pytest.raises(EmbeddingError, match="indices"):
        await provider.embed(["a", "b"])


@pytest.mark.asyncio
async def test_openai_rejects_out_of_range_indices(patched_openai):
    response = _response([_vec(), _vec()], indices=[0, 5])
    provider, _ = patched_openai(response)
    with pytest.raises(EmbeddingError, match="indices"):
        await provider.embed(["a", "b"])


@pytest.mark.asyncio
async def test_openai_rejects_wrong_count(patched_openai):
    provider, _ = patched_openai(_response([_vec()]))
    with pytest.raises(EmbeddingError, match="indices"):
        await provider.embed(["a", "b"])


@pytest.mark.asyncio
async def test_openai_rejects_wrong_dimension(patched_openai):
    provider = _make_openai(dimension=8)
    response = _response([_vec(dim=4)])
    client = MagicMock()
    client.embeddings.create = AsyncMock(return_value=response)
    client.close = AsyncMock()
    provider._client = client
    with pytest.raises(EmbeddingError, match="dimension 4, expected 8"):
        await provider.embed(["a"])
    await client.close()


@pytest.mark.asyncio
async def test_openai_splits_into_batches(patched_openai):
    provider = _make_openai(max_batch_size=2)
    client = MagicMock()
    client.embeddings.create = AsyncMock(
        side_effect=[
            _response([_vec(), _vec()]),
            _response([_vec(), _vec()]),
            _response([_vec()]),
        ]
    )
    client.close = AsyncMock()
    provider._client = client
    vectors = await provider.embed(["a", "b", "c", "d", "e"])
    assert len(vectors) == 5
    assert client.embeddings.create.await_count == 3
    await client.close()


# ---------------------------------------------------------------------------
# OpenAI provider — error mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_auth_error(patched_openai):
    provider, client = patched_openai(_response([_vec()]))
    client.embeddings.create.side_effect = AuthenticationError("x", response=MagicMock(status_code=401), body=None)
    with pytest.raises(EmbeddingError, match="auth failed"):
        await provider.embed(["a"])


@pytest.mark.asyncio
async def test_openai_rate_limit_error(patched_openai):
    provider, client = patched_openai(_response([_vec()]))
    client.embeddings.create.side_effect = RateLimitError("x", response=MagicMock(status_code=429), body=None)
    with pytest.raises(EmbeddingError, match="transient failure"):
        await provider.embed(["a"])


@pytest.mark.asyncio
async def test_openai_bad_request_error(patched_openai):
    provider, client = patched_openai(_response([_vec()]))
    client.embeddings.create.side_effect = BadRequestError("x", response=MagicMock(status_code=400), body=None)
    with pytest.raises(EmbeddingError, match="bad request"):
        await provider.embed(["a"])


@pytest.mark.asyncio
async def test_openai_connection_error(patched_openai):
    provider, client = patched_openai(_response([_vec()]))
    client.embeddings.create.side_effect = APIConnectionError(request=MagicMock())
    with pytest.raises(EmbeddingError, match="transient failure"):
        await provider.embed(["a"])


@pytest.mark.asyncio
async def test_openai_unexpected_error(patched_openai):
    provider, client = patched_openai(_response([_vec()]))
    client.embeddings.create.side_effect = RuntimeError("boom")
    with pytest.raises(EmbeddingError, match="embedding failed"):
        await provider.embed(["a"])


@pytest.mark.asyncio
async def test_openai_close(patched_openai):
    provider, client = patched_openai(_response([_vec()]))
    await provider.close()
    client.close.assert_awaited_once()
