"""Tests for :mod:`app.providers.embeddings`.

The mock provider is exercised directly — no SDK mocking, no network.
The OpenAI provider tests swap ``_client`` for a small fake once, in a
fixture; individual tests set the response on that fake's
``embeddings.create`` mock rather than calling a provider factory.
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
# helpers
# ---------------------------------------------------------------------------


def vec(dim: int = 8, fill: float = 0.5) -> list[float]:
    return [fill] * dim


def response(
    vectors: list[list[float]],
    indices: list[int] | None = None,
) -> SimpleNamespace:
    """Build a CreateEmbeddingResponse-shaped object.

    ``indices`` lets a test produce a malformed response (duplicates,
    out-of-range, wrong count) without touching the vectors themselves.
    """
    if indices is None:
        items = [SimpleNamespace(embedding=v, index=i, object="embedding") for i, v in enumerate(vectors)]
    else:
        items = [
            SimpleNamespace(embedding=v, index=i, object="embedding") for v, i in zip(vectors, indices, strict=True)
        ]
    return SimpleNamespace(
        data=items,
        model="test-model",
        usage=SimpleNamespace(),
    )


def make_openai(
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


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(dimension=8)


@pytest_asyncio.fixture
async def openai_provider() -> OpenAIEmbeddingProvider:
    """OpenAI provider with a fake SDK client.

    The real ``AsyncOpenAI`` built by ``__init__`` owns an http client;
    it is closed on the way out. ``provider._client.embeddings.create``
    is an ``AsyncMock`` that individual tests configure with
    ``.return_value`` or ``.side_effect``.
    """
    provider = make_openai()
    real_client = provider._client

    client = MagicMock()
    client.embeddings.create = AsyncMock()
    client.close = AsyncMock()
    provider._client = client

    try:
        yield provider
    finally:
        await real_client.close()


# ---------------------------------------------------------------------------
# Mock provider
# ---------------------------------------------------------------------------


def test_mock_implements_interface(mock_provider):
    assert isinstance(mock_provider, EmbeddingProvider)


def test_mock_defaults():
    p = MockEmbeddingProvider()
    assert p.model == "mock-embeddings"
    assert p.dimension == 2048


def test_mock_rejects_nonpositive_dimension():
    with pytest.raises(ValueError, match="dimension"):
        MockEmbeddingProvider(dimension=0)


async def test_mock_returns_empty_for_empty_input(mock_provider):
    assert await mock_provider.embed([]) == []


async def test_mock_returns_one_per_text(mock_provider):
    vectors = await mock_provider.embed(["a", "b", "c"])
    assert len(vectors) == 3
    assert all(len(v) == 8 for v in vectors)


async def test_mock_is_deterministic(mock_provider):
    a = await mock_provider.embed(["hello"])
    b = await mock_provider.embed(["hello"])
    assert a == b


async def test_mock_vectors_are_normalized(mock_provider):
    [v] = await mock_provider.embed(["hello"])
    norm = math.sqrt(sum(x * x for x in v))
    assert math.isclose(norm, 1.0, rel_tol=1e-6)


async def test_mock_empty_text_gives_zero_vector(mock_provider):
    [v] = await mock_provider.embed([""])
    assert v == [0.0] * 8


async def test_mock_distinct_texts_distinct_vectors(mock_provider):
    [a] = await mock_provider.embed(["hello"])
    [b] = await mock_provider.embed(["world"])
    assert a != b


async def test_mock_close_is_noop(mock_provider):
    await mock_provider.close()


# ---------------------------------------------------------------------------
# OpenAI provider — constructor
# ---------------------------------------------------------------------------


def test_openai_rejects_empty_model():
    with pytest.raises(ValueError, match="model"):
        make_openai(model="")


def test_openai_rejects_nonpositive_dimension():
    with pytest.raises(ValueError, match="dimension"):
        make_openai(dimension=0)


def test_openai_rejects_empty_base_url():
    with pytest.raises(ValueError, match="base_url"):
        OpenAIEmbeddingProvider(api_key="k", model="m", dimension=8, base_url="")


async def test_openai_implements_interface(openai_provider):
    assert isinstance(openai_provider, EmbeddingProvider)


# ---------------------------------------------------------------------------
# OpenAI provider — embed
# ---------------------------------------------------------------------------


async def test_openai_embed_empty_noop(openai_provider):
    assert await openai_provider.embed([]) == []
    openai_provider._client.embeddings.create.assert_not_awaited()


async def test_openai_embed_single(openai_provider):
    openai_provider._client.embeddings.create.return_value = response([vec()])

    result = await openai_provider.embed(["hello"])

    assert result == [vec()]
    assert openai_provider._client.embeddings.create.call_args.kwargs == {
        "model": "test-model",
        "input": ["hello"],
    }


async def test_openai_reorders_by_index(openai_provider):
    v0 = [0.0] * 8
    v1 = [1.0] * 8
    v2 = [2.0] * 8
    openai_provider._client.embeddings.create.return_value = response([v2, v0, v1], indices=[2, 0, 1])

    assert await openai_provider.embed(["a", "b", "c"]) == [v0, v1, v2]


async def test_openai_rejects_duplicate_indices(openai_provider):
    openai_provider._client.embeddings.create.return_value = response([vec(), vec()], indices=[0, 0])

    with pytest.raises(EmbeddingError, match="indices"):
        await openai_provider.embed(["a", "b"])


async def test_openai_rejects_out_of_range_indices(openai_provider):
    openai_provider._client.embeddings.create.return_value = response([vec(), vec()], indices=[0, 5])

    with pytest.raises(EmbeddingError, match="indices"):
        await openai_provider.embed(["a", "b"])


async def test_openai_rejects_wrong_count(openai_provider):
    openai_provider._client.embeddings.create.return_value = response([vec()])

    with pytest.raises(EmbeddingError, match="indices"):
        await openai_provider.embed(["a", "b"])


async def test_openai_rejects_wrong_dimension(openai_provider):
    openai_provider._client.embeddings.create.return_value = response([vec(dim=4)])

    with pytest.raises(EmbeddingError, match="dimension 4, expected 8"):
        await openai_provider.embed(["a"])


async def test_openai_splits_into_batches(openai_provider):
    openai_provider._max_batch_size = 2
    openai_provider._client.embeddings.create.side_effect = [
        response([vec(), vec()]),
        response([vec(), vec()]),
        response([vec()]),
    ]

    vectors = await openai_provider.embed(["a", "b", "c", "d", "e"])

    assert len(vectors) == 5
    assert openai_provider._client.embeddings.create.await_count == 3


# ---------------------------------------------------------------------------
# OpenAI provider — error mapping
# ---------------------------------------------------------------------------


async def test_openai_auth_error(openai_provider):
    openai_provider._client.embeddings.create.side_effect = AuthenticationError(
        "x", response=MagicMock(status_code=401), body=None
    )

    with pytest.raises(EmbeddingError, match="auth failed"):
        await openai_provider.embed(["a"])


async def test_openai_rate_limit_error(openai_provider):
    openai_provider._client.embeddings.create.side_effect = RateLimitError(
        "x", response=MagicMock(status_code=429), body=None
    )

    with pytest.raises(EmbeddingError, match="transient failure"):
        await openai_provider.embed(["a"])


async def test_openai_bad_request_error(openai_provider):
    openai_provider._client.embeddings.create.side_effect = BadRequestError(
        "x", response=MagicMock(status_code=400), body=None
    )

    with pytest.raises(EmbeddingError, match="bad request"):
        await openai_provider.embed(["a"])


async def test_openai_connection_error(openai_provider):
    openai_provider._client.embeddings.create.side_effect = APIConnectionError(request=MagicMock())

    with pytest.raises(EmbeddingError, match="transient failure"):
        await openai_provider.embed(["a"])


async def test_openai_unexpected_error(openai_provider):
    openai_provider._client.embeddings.create.side_effect = RuntimeError("boom")

    with pytest.raises(EmbeddingError, match="embedding failed"):
        await openai_provider.embed(["a"])


async def test_openai_close(openai_provider):
    await openai_provider.close()
    openai_provider._client.close.assert_awaited_once()
