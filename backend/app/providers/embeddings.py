"""OpenAI-compatible embedding provider.

Uses the OpenAI SDK against any OpenAI-compatible ``/embeddings`` endpoint.
Selected when the school-hub router is in use (``SBER_API_SCOPE=openai``
with ``OPENAI_URL`` set). The direct GigaChat API does not get a native
embedding client: this provider is the only one, and it is gated by
scope in :func:`app.deps.get_embedding_provider`.

Satisfies the ``edu_rag.embeddings.base.EmbeddingProvider`` protocol:
the ``model``, ``dimension`` attributes and ``async embed(texts)`` method
are what ``DocumentIndexer`` depends on. ``edu_rag`` never imports from
this module — the dependency direction is backend → edu_rag only.
"""

from __future__ import annotations

import os
from typing import Final

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

DEFAULT_EMBEDDING_MODEL: Final = "ai-sage/Giga-Embeddings-instruct-3B"

DEFAULT_EMBEDDING_DIMENSION: Final = 2048


class OpenAIEmbeddingProvider:
    """OpenAI-compatible embedding provider.

    The client is created in ``__init__`` so the SDK can pool connections
    across calls, mirroring :class:`app.providers.openai.OpenAIProvider`.
    The SDK's built-in retry (``max_retries``) is used instead of tenacity
    — it already retries on 429, 5xx and timeouts, and it understands the
    ``Retry-After`` header some routers send.
    """

    model: str
    dimension: int

    def __init__(
        self,
        api_key: str,
        model: str | None = None,
        dimension: int | None = None,
        base_url: str | None = None,
        max_retries: int = 3,
        timeout_s: float = 30.0,
    ) -> None:
        self._log = get_logger(__name__)

        kwargs: dict = {
            "api_key": api_key,
            "max_retries": max_retries,
            "timeout": timeout_s,
        }
        url = base_url or os.getenv("OPENAI_URL")
        if url:
            kwargs["base_url"] = url
        else:
            self._log.warning(
                "openai_embedding_url_not_configured",
                detail="OPENAI_URL is unset or empty; using the official OpenAI endpoint",
            )
        self._client = AsyncOpenAI(**kwargs)

        self.model = model or os.getenv("OPENAI_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)
        raw_dim = (
            dimension
            if dimension is not None
            else int(os.getenv("OPENAI_EMBEDDING_DIMENSION", str(DEFAULT_EMBEDDING_DIMENSION)))
        )
        if raw_dim <= 0:
            raise ValueError(f"dimension must be positive, got {raw_dim}")
        self.dimension = raw_dim

    async def close(self) -> None:
        await self._client.close()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding per input text, preserving order.

        Empty input short-circuits without an API call. Order is restored
        by sorting on ``index`` because the OpenAI response is not
        guaranteed to match the request order.
        """
        if not texts:
            return []

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

        items = sorted(response.data, key=lambda d: d.index)
        if len(items) != len(texts):
            raise EmbeddingError(f"provider returned {len(items)} embeddings for {len(texts)} inputs")

        vectors: list[list[float]] = [list(item.embedding) for item in items]
        for i, vec in enumerate(vectors):
            if len(vec) != self.dimension:
                raise EmbeddingError(
                    f"vector {i} has dimension {len(vec)}, " f"expected {self.dimension} (model={self.model})"
                )
        return vectors
