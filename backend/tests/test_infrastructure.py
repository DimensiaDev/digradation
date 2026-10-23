"""Smoke tests for the shared test infrastructure itself.

These exist so a broken fixture fails here with an obvious message, rather than
as a confusing error in whichever suite happens to run first.
"""

from app.cache import get_redis_client
from app.storage import get_s3_avatar_processed_key, storage


async def test_redis_client_reaches_the_test_container(redis_session: None):
    async with get_redis_client() as redis:
        await redis.set("smoke:key", "value")
        assert await redis.get("smoke:key") == "value"


async def test_object_storage_round_trip(s3: None):
    import uuid as _uuid

    key = f"smoke/{_uuid.uuid4()}.txt"
    async with storage.internal_client() as client:
        await client.put_object(Bucket="uploads", Key=key, Body=b"hello", ContentType="text/plain")
        response = await client.get_object(Bucket="uploads", Key=key)
        async with response["Body"] as body:
            assert await body.read() == b"hello"
        await client.delete_object(Bucket="uploads", Key=key)


def test_provider_selection(monkeypatch):
    from app.providers.mock import MockProvider
    from app.providers.openai import OpenAIProvider
    from app.providers.sber import SberProvider

    # Selecting `openai` requires an explicit endpoint; there is no public default.
    monkeypatch.setenv("OPENAI_URL", "https://hub.example.test/api/v1/llm/v1")

    def build(key: str, scope: str = "PERS"):
        import importlib

        monkeypatch.setenv("SBER_API_KEY", key)
        monkeypatch.setenv("SBER_API_SCOPE", scope)
        module = importlib.reload(importlib.import_module("app.providers.containers"))
        return module.provider

    assert isinstance(build("mock"), MockProvider)
    assert isinstance(build("real-key"), SberProvider)
    # The scope alone selects the OpenAI-compatible and mock clients.
    assert isinstance(build("real-key", "openai"), OpenAIProvider)
    assert isinstance(build("real-key", "mock"), MockProvider)
    # `SBER_API_KEY=mock` wins over the scope, `openai` included.
    assert isinstance(build("mock", "openai"), MockProvider)
    # The container hands its (currently empty) tool list to the real providers.
    assert build("real-key").tools == []
    assert build("real-key", "openai").tools == []
    # Leave the module holding the offline provider so later tests do not inherit
    # a real client.
    assert isinstance(build("mock"), MockProvider)


def test_the_cloudru_preset_names_a_documented_model(monkeypatch):
    """The preset is a convenience, and its default has to be a served model."""
    import importlib

    from app.providers.openai import OpenAIProvider

    monkeypatch.setenv("SBER_API_KEY", "real-key")
    monkeypatch.setenv("SBER_API_SCOPE", "PERS")
    monkeypatch.setenv("LLM_PROVIDER", "cloudru")
    for name in ("OPENAI_URL", "OPENAI_MODEL", "CLOUDRU_URL", "CLOUDRU_MODEL"):
        monkeypatch.delenv(name, raising=False)
    module = importlib.reload(importlib.import_module("app.providers.containers"))

    assert isinstance(module.provider, OpenAIProvider)
    assert module.provider._model() == "ai-sage/GigaChat3.5-432B-A28B"
    assert str(module.provider._client.base_url).rstrip("/") == "https://foundation-models.api.cloud.ru/v1"

    # Leave the module holding the offline provider for the tests that follow.
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("SBER_API_KEY", "mock")
    importlib.reload(module)


def test_embedding_provider_requires_the_index_dimension(monkeypatch):
    import asyncio
    import importlib

    import pytest

    module = importlib.import_module("app.providers.containers")
    monkeypatch.setenv("SBER_API_KEY", "mock")
    monkeypatch.setenv("SBER_API_SCOPE", "PERS")
    module = importlib.reload(module)
    mock = module.get_embedding_provider()
    assert mock.dimension == 2048

    monkeypatch.setenv("SBER_API_KEY", "real-key")
    monkeypatch.setenv("SBER_API_SCOPE", "openai")
    monkeypatch.setenv("OPENAI_URL", "https://hub.example.test/api/v1/llm/v1")
    monkeypatch.setenv("OPENAI_EMBEDDING_MODEL", "ai-sage/Giga-Embeddings-instruct-3B")
    monkeypatch.setenv("OPENAI_EMBEDDING_DIMENSION", "1024")
    module = importlib.reload(module)
    with pytest.raises(RuntimeError, match="2048"):
        module.get_embedding_provider()

    monkeypatch.setenv("OPENAI_EMBEDDING_DIMENSION", "2048")
    module = importlib.reload(module)
    provider = module.get_embedding_provider()
    assert provider.dimension == 2048
    asyncio.run(provider.close())

    # Leave the module holding the offline provider for the tests that follow.
    monkeypatch.setenv("SBER_API_KEY", "mock")
    monkeypatch.setenv("SBER_API_SCOPE", "PERS")
    importlib.reload(module)


def test_s3_key_builders_are_namespaced():
    import uuid as _uuid

    user = _uuid.uuid4()
    assert get_s3_avatar_processed_key(user).startswith("avatars/")
