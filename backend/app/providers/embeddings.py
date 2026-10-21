"""Embedding providers.

OpenAI-compatible embeddings via the school-hub router (``SBER_API_SCOPE=openai``
with ``OPENAI_URL`` set), plus a deterministic mock selected when
``SBER_API_KEY=mock``. Both implement :class:`EmbeddingProvider`, matching the
shape of ``SberProvider`` / ``MockProvider`` in this package.

``edu_rag.DocumentIndexer`` consumes them through its structural
``EmbeddingProvider`` protocol: ``model``, ``dimension``, ``async embed()``.
The dependency direction is backend → edu_rag only.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod

import numpy as np
from edu_rag.errors import EmbeddingError
from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)
from structlog.stdlib import get_logger


class EmbeddingProvider(ABC):
    """Interface every embedding provider must satisfy.

    ``model`` and ``dimension`` are part of the contract because
    ``edu_rag.DocumentIndexer`` builds its fingerprint from them: a chunk
    embedded with a different model or dimension must be re-embedded, and
    the indexer cannot know that without reading them from the provider.
    """

    model: str
    dimension: int

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding per input text, preserving order."""

    async def close(self) -> None:  # noqa: B027 — default no-op; subclasses with resources override
        """Release resources. No-op by default."""


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI-compatible embedding provider.

    All connection and model parameters are explicit — no defaults, no env
    lookups. Missing configuration must fail loudly at the container
    boundary, not silently fall back to a different endpoint or model.

    Splits the input into ``max_batch_size`` requests to stay within the
    per-request limit the router enforces. The SDK's own retry handles
    429/5xx/timeouts; only exhausted retries surface as
    :class:`edu_rag.errors.EmbeddingError`.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        dimension: int,
        base_url: str,
        *,
        max_batch_size: int = 32,
        max_retries: int = 3,
        timeout_s: float = 30.0,
    ) -> None:
        if not model:
            raise ValueError("model must be a non-empty string")
        if dimension <= 0:
            raise ValueError(f"dimension must be positive, got {dimension}")
        if not base_url:
            raise ValueError("base_url must be a non-empty string")
        if max_batch_size <= 0:
            raise ValueError(f"max_batch_size must be positive, got {max_batch_size}")

        self.model = model
        self.dimension = dimension
        self._max_batch_size = max_batch_size
        self._log = get_logger(__name__)
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=max_retries,
            timeout=timeout_s,
        )

    async def close(self) -> None:
        await self._client.close()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._max_batch_size):
            batch = texts[start : start + self._max_batch_size]
            vectors.extend(await self._embed_batch(batch))
        return vectors

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        try:
            response = await self._client.embeddings.create(
                model=self.model,
                input=texts,
            )
        except AuthenticationError as exc:
            self._log.error("embedding_auth_failed", exc=exc)
            raise EmbeddingError(f"embedding auth failed: {exc}") from exc
        except (RateLimitError, APITimeoutError, APIConnectionError) as exc:
            self._log.warning("embedding_transient_failure", exc=exc)
            raise EmbeddingError(f"embedding transient failure: {exc}") from exc
        except BadRequestError as exc:
            self._log.error("embedding_bad_request", exc=exc)
            raise EmbeddingError(f"embedding bad request: {exc}") from exc
        except Exception as exc:
            self._log.error("embedding_unexpected_failure", exc=exc)
            raise EmbeddingError(f"embedding failed: {exc}") from exc

        indices = [item.index for item in response.data]
        expected = list(range(len(texts)))
        if sorted(indices) != expected:
            raise EmbeddingError(
                f"provider returned indices {sorted(indices)}, " f"expected {expected} (duplicates or out-of-range)"
            )

        ordered = sorted(response.data, key=lambda d: d.index)
        vectors = [np.asarray(item.embedding, dtype=np.float64).tolist() for item in ordered]

        for i, vec in enumerate(vectors):
            if len(vec) != self.dimension:
                raise EmbeddingError(
                    f"vector {i} has dimension {len(vec)}, " f"expected {self.dimension} (model={self.model})"
                )
        return vectors


class MockEmbeddingProvider(EmbeddingProvider):
    """Deterministic in-process mock.

    Selected automatically when ``SBER_API_KEY=mock`` (or
    ``SBER_API_SCOPE=mock``). Matches the ``edu_rag`` mock's guarantees:
    same input yields the same vector, every vector has norm 1 and the
    configured dimension, empty text returns a zero vector. No network,
    no numpy hot path — good enough for tests and offline e2e.
    """

    def __init__(self, model: str = "mock-embeddings", dimension: int = 1024) -> None:
        if dimension <= 0:
            raise ValueError(f"dimension must be positive, got {dimension}")
        self.model = model
        self.dimension = dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def _vector(self, text: str) -> list[float]:
        if not text:
            return [0.0] * self.dimension

        digest = hashlib.sha256(text.encode("utf-8")).digest()
        needed = self.dimension * 2
        buf = bytearray()
        counter = 0
        while len(buf) < needed:
            buf.extend(hashlib.sha256(digest + counter.to_bytes(4, "big")).digest())
            counter += 1

        arr = np.frombuffer(bytes(buf[:needed]), dtype=np.uint16).astype(np.float64)
        arr = (arr / 65535.0) * 2.0 - 1.0
        norm = float(np.linalg.norm(arr))
        if norm == 0.0:  # pragma: no cover — hash bytes never all zero
            return [0.0] * self.dimension
        arr /= norm
        return arr.tolist()
