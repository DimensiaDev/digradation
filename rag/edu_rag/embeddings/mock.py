"""Deterministic in-process embedding provider for tests and offline demos."""

import hashlib
import math

from ..config import EmbeddingConfig


class MockEmbeddingProvider:
    """Deterministic embedding provider that does not call any API.

    Guarantees that matter for tests:

    - **Deterministic**: same text → same vector. Needed for idempotency
      checks in the indexer and for snapshot-style assertions.
    - **L2-normalized**: every vector has norm 1.0 (except empty text).
      Matches what real providers return, so cosine similarity in the
      store behaves the same way as with GigaChat.
    - **Correct dimension**: ``len(vec) == config.dimension``.
    - **Order preserved**: ``embed(texts)[i]`` is the embedding of
      ``texts[i]``.
    - **Empty text → zero vector**: avoids division by zero when
      normalizing, and is trivially distinguishable in tests.

    Pure stdlib — no numpy, no I/O. Safe to use in CI without a network.
    """

    def __init__(self, config: EmbeddingConfig | None = None) -> None:
        self._config = config or EmbeddingConfig()
        self.model = f"mock-{self._config.model}"
        self.dimension = self._config.dimension

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

        values: list[float] = []
        for i in range(self.dimension):
            x = int.from_bytes(buf[2 * i : 2 * i + 2], "big")
            values.append((x / 65535.0) * 2.0 - 1.0)

        norm = math.sqrt(sum(v * v for v in values))
        if norm == 0.0:  # pragma: no cover — hash bytes never all zero
            return [0.0] * self.dimension
        return [v / norm for v in values]
