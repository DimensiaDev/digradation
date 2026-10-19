"""Abstract contract for embedding providers."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Protocol for embedding providers.

    Implementations must be async — real providers call HTTP APIs.
    ``model`` and ``dimension`` are informational: the caller (indexer,
    or the backend wiring) uses them to assert that the provider matches
    the store's vector column dimension.

    The ordering contract: ``embed(texts)[i]`` is the embedding of
    ``texts[i]``. Batching is an internal concern of the implementation;
    callers pass a whole list and get a list of the same length back.
    """

    model: str
    dimension: int

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding per input text, preserving order."""
        ...
