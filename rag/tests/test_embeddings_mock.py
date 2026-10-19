"""Tests for MockEmbeddingProvider."""

import math

import pytest

from edu_rag import EmbeddingConfig
from edu_rag.embeddings.base import EmbeddingProvider
from edu_rag.embeddings.mock import MockEmbeddingProvider


@pytest.fixture
def provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider()


def test_satisfies_protocol(provider):
    assert isinstance(provider, EmbeddingProvider)


def test_attributes(provider):
    assert provider.model == "mock-Embeddings"
    assert provider.dimension == 1024


async def test_returns_one_per_text(provider):
    vectors = await provider.embed(["a", "b", "c"])
    assert len(vectors) == 3


async def test_dimension(provider):
    [vec] = await provider.embed(["hello"])
    assert len(vec) == 1024


async def test_empty_input(provider):
    assert await provider.embed([]) == []


async def test_deterministic(provider):
    a = await provider.embed(["hello"])
    b = await provider.embed(["hello"])
    assert a == b


async def test_different_texts_different_vectors(provider):
    a, b = await provider.embed(["alpha", "beta"])
    assert a != b


async def test_normalized(provider):
    [vec] = await provider.embed(["hello world"])
    norm = math.sqrt(sum(v * v for v in vec))
    assert math.isclose(norm, 1.0, rel_tol=1e-6)


async def test_empty_text_zero_vector(provider):
    [vec] = await provider.embed([""])
    assert all(v == 0.0 for v in vec)


async def test_custom_dimension():
    provider = MockEmbeddingProvider(EmbeddingConfig(dimension=64))
    [vec] = await provider.embed(["hello"])
    assert len(vec) == 64


async def test_order_preserved(provider):
    texts = ["first", "second", "third"]
    vectors = await provider.embed(texts)
    for text, vec in zip(texts, vectors, strict=True):
        [single] = await provider.embed([text])
        assert vec == single


async def test_unicode_text(provider):
    [a] = await provider.embed(["привет"])
    [b] = await provider.embed(["hello"])
    assert a != b
    assert len(a) == 1024
