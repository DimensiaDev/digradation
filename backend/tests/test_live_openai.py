"""Live tests against the real OpenAI-compatible endpoint.

Deselected by default (``addopts = "-m 'not live'"``) because every call costs
tokens. Run them deliberately, with the provider environment exported before
``pytest`` so :func:`real_provider_env` can restore it:

    SBER_API_KEY=<token> SBER_API_SCOPE=openai \
        OPENAI_URL=<url> OPENAI_MODEL=<model> \
        poetry -C backend run pytest -m 'live and openai'

If ``SBER_API_SCOPE`` is not ``openai`` or the key is missing/``mock``, every test
here skips rather than reaching the network.
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio

from app.providers.models import Content, FunctionDefinition, Message, ResponseFormat, Tool
from app.providers.openai import OpenAIProvider

pytestmark = [pytest.mark.live, pytest.mark.openai]


def _require_openai_env():
    key = os.environ.get("SBER_API_KEY", "")
    scope = os.environ.get("SBER_API_SCOPE", "")
    if not key or key == "mock":
        pytest.skip("set SBER_API_KEY to a real token to run live OpenAI tests")
    if scope != "openai":
        pytest.skip("set SBER_API_SCOPE=openai to run live OpenAI tests")
    return key


@pytest_asyncio.fixture
async def live_provider(real_provider_env: None):
    key = _require_openai_env()
    provider = OpenAIProvider(api_key=key, scope="openai")
    try:
        yield provider
    finally:
        await provider.close()


def user_message(text: str) -> list[Message]:
    return [Message(role="user", content=[Content(text=text)])]


async def test_live_openai_text_completion(live_provider):
    """One small completion, verifying text and usage round-trip."""
    response = await live_provider.generate(
        user_message("Ответь одним словом: столица Франции?"),
        ResponseFormat(type="text"),
        x_client_id=uuid.uuid4(),
        x_session_id=uuid.uuid4(),
    )

    assert response is not None, "the live endpoint returned an error"
    assert response.messages[0].content, "the model returned no text"
    assert response.usage.total_tokens > 0


async def test_live_openai_tool_call(live_provider):
    """The model asks for a tool instead of answering in prose."""
    tool = Tool(
        function=FunctionDefinition(
            name="get_weather",
            description="Return the current weather for a city.",
            parameters={
                "type": "object",
                "properties": {"city": {"type": "string", "description": "City name"}},
                "required": ["city"],
            },
        )
    )
    provider = live_provider
    provider.tools = [tool]

    response = await provider.generate(
        user_message("Вызови инструмент get_weather для города Москва. Не отвечай текстом."),
        ResponseFormat(type="text"),
        x_client_id=uuid.uuid4(),
        x_session_id=uuid.uuid4(),
    )

    assert response is not None, "the live endpoint returned an error"
    assert response.tool_calls, "the model did not request the tool"
    assert response.tool_calls[0].function.name == "get_weather"
    assert "Москва" in response.tool_calls[0].function.arguments
