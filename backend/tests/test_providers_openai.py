"""Unit tests for the OpenAI-compatible client in :mod:`app.providers.openai`.

Nothing here touches the network. The provider builds a real ``AsyncOpenAI``, and
every test swaps ``provider._client`` for a small fake whose
``chat.completions.create`` is an ``AsyncMock`` returning a real ``ChatCompletion``
model. That keeps the mapping under test against the library's own types without
opening a socket.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from openai import AuthenticationError
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_message_function_tool_call import (
    ChatCompletionMessageFunctionToolCall,
)
from openai.types.chat.chat_completion_message_function_tool_call import Function as ToolCallFunction
from openai.types.completion_usage import CompletionUsage, PromptTokensDetails

from app.providers.client import AIClient, AuthorizationError
from app.providers.models import (
    Content,
    FunctionDefinition,
    Message,
    ResponseFormat,
    Tool,
)
from app.providers.openai import DEFAULT_OPENAI_MODEL, OpenAIProvider

FUTURE_S = 1_700_000_000


def make_completion(
    *,
    content: str | None = "answer",
    tool_calls: list | None = None,
    finish_reason: str = "stop",
    model: str = "GigaChat/GigaChat-3-Pro",
) -> ChatCompletion:
    return ChatCompletion(
        id="chatcmpl-1",
        object="chat.completion",
        created=FUTURE_S,
        model=model,
        choices=[
            Choice(
                index=0,
                finish_reason=finish_reason,
                message=ChatCompletionMessage(role="assistant", content=content, tool_calls=tool_calls),
            )
        ],
        usage=CompletionUsage(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            prompt_tokens_details=PromptTokensDetails(cached_tokens=2),
        ),
    )


def function_tool(name: str = "lookup_price") -> Tool:
    return Tool(
        function=FunctionDefinition(
            name=name,
            description="Look up a price",
            parameters={"type": "object", "properties": {"item": {"type": "string"}}},
        )
    )


def history(text: str = "hi") -> list[Message]:
    return [Message(role="user", content=[Content(text=text)])]


@pytest_asyncio.fixture
async def make_provider():
    """Build an ``OpenAIProvider`` with a fake SDK client and close the real ones.

    The real ``AsyncOpenAI`` built by ``__init__`` is never used in these tests,
    but it still owns an http client, so it is closed on the way out.
    """
    originals = []

    def _make(
        completion: ChatCompletion | None = None,
        *,
        tools: list[Tool] | None = None,
    ) -> tuple[OpenAIProvider, MagicMock]:
        provider = OpenAIProvider(api_key="key", scope="openai", tools=tools)
        originals.append(provider._client)
        client = MagicMock()
        client.chat.completions.create = AsyncMock(return_value=completion)
        client.files.create = AsyncMock(return_value=SimpleNamespace(id="file-1"))
        client.close = AsyncMock()
        provider._client = client
        return provider, client

    yield _make

    for client in originals:
        await client.close()


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


def test_implements_the_abstract_interface():
    assert issubclass(OpenAIProvider, AIClient)
    assert AIClient.__abstractmethods__ == frozenset({"auth", "upload", "generate"})


def test_init_defaults_to_the_official_endpoint(monkeypatch):
    monkeypatch.delenv("OPENAI_URL", raising=False)
    provider = OpenAIProvider(api_key="key", scope="openai")

    assert str(provider._client.base_url).startswith("https://api.openai.com")


def test_init_treats_an_empty_openai_url_as_the_official_endpoint(monkeypatch):
    monkeypatch.setenv("OPENAI_URL", "")
    provider = OpenAIProvider(api_key="key", scope="openai")

    assert str(provider._client.base_url).startswith("https://api.openai.com")


@pytest.mark.asyncio
async def test_init_respects_openai_url(monkeypatch):
    monkeypatch.setenv("OPENAI_URL", "https://hub.example.test/api/v1/llm/v1")
    provider = OpenAIProvider(api_key="key", scope="openai")

    assert str(provider._client.base_url).startswith("https://hub.example.test/api/v1/llm/v1")

    await provider.close()


@pytest.mark.asyncio
async def test_init_stores_tools():
    tool = function_tool()
    provider = OpenAIProvider(api_key="key", scope="openai", tools=[tool])

    assert provider.tools == [tool]

    await provider.close()


@pytest.mark.asyncio
async def test_auth_sets_a_long_lived_token():
    provider = OpenAIProvider(api_key="key", scope="openai")
    assert provider.token is None

    await provider.auth()

    assert provider.token == "key"
    assert provider.token_expires_at is not None

    await provider.close()


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_maps_a_text_completion(make_provider):
    provider, _ = make_provider(make_completion())

    result = await provider.generate(
        history("hi"),
        ResponseFormat(type="text"),
        x_client_id=uuid.uuid4(),
        x_session_id=uuid.uuid4(),
    )

    assert result is not None
    assert result.model == "GigaChat/GigaChat-3-Pro"
    assert result.thread_id == "chatcmpl-1"
    assert result.finish_reason == "stop"
    assert result.messages[0].role == "assistant"
    assert result.messages[0].content[0].text == "answer"
    assert result.tool_calls == []
    assert result.created_at == datetime.fromtimestamp(FUTURE_S, tz=UTC)
    assert result.usage.total_tokens == 15
    assert result.usage.input_tokens == 10
    assert result.usage.output_tokens == 5
    assert result.usage.input_tokens_details.prompt_tokens == 10
    assert result.usage.input_tokens_details.cached_tokens == 2


def test_map_completion_defaults_missing_usage_to_zero():
    completion = make_completion().model_copy(update={"usage": None})

    result = OpenAIProvider._map_completion(completion)

    assert result.usage.input_tokens == 0
    assert result.usage.input_tokens_details.prompt_tokens == 0
    assert result.usage.input_tokens_details.cached_tokens == 0
    assert result.usage.output_tokens == 0
    assert result.usage.total_tokens == 0


@pytest.mark.asyncio
async def test_generate_returns_empty_content_when_the_model_only_calls_a_tool(make_provider):
    call = ChatCompletionMessageFunctionToolCall(
        id="call_1",
        type="function",
        function=ToolCallFunction(name="lookup_price", arguments='{"item": "steel"}'),
    )
    provider, _ = make_provider(make_completion(content=None, tool_calls=[call], finish_reason="tool_calls"))

    result = await provider.generate(
        history(),
        ResponseFormat(type="text"),
        x_client_id=uuid.uuid4(),
        x_session_id=uuid.uuid4(),
    )

    assert result is not None
    assert result.finish_reason == "tool_calls"
    assert result.messages[0].content == []
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id == "call_1"
    assert result.tool_calls[0].function.name == "lookup_price"
    assert result.tool_calls[0].function.arguments == '{"item": "steel"}'


@pytest.mark.asyncio
async def test_generate_advertises_the_configured_tools(make_provider):
    provider, client = make_provider(make_completion(), tools=[function_tool("a"), function_tool("b")])

    await provider.generate(
        history(),
        ResponseFormat(type="text"),
        x_client_id=uuid.uuid4(),
        x_session_id=uuid.uuid4(),
    )

    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["tool_choice"] == "auto"
    assert [t["function"]["name"] for t in kwargs["tools"]] == ["a", "b"]
    assert kwargs["tools"][0]["type"] == "function"


@pytest.mark.asyncio
async def test_generate_omits_tool_arguments_without_tools(make_provider):
    provider, client = make_provider(make_completion())

    await provider.generate(
        history(),
        ResponseFormat(type="text"),
        x_client_id=uuid.uuid4(),
        x_session_id=uuid.uuid4(),
    )

    kwargs = client.chat.completions.create.call_args.kwargs
    assert "tools" not in kwargs
    assert "tool_choice" not in kwargs


@pytest.mark.asyncio
async def test_generate_maps_a_json_schema_response_format(make_provider):
    provider, client = make_provider(make_completion())
    schema = {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]}

    await provider.generate(
        history(),
        ResponseFormat(type="json_schema", schema=schema, strict=True),
        x_client_id=uuid.uuid4(),
        x_session_id=uuid.uuid4(),
    )

    fmt = client.chat.completions.create.call_args.kwargs["response_format"]
    assert fmt == {
        "type": "json_schema",
        "json_schema": {"name": "response", "schema": schema, "strict": True},
    }


@pytest.mark.asyncio
async def test_generate_omits_response_format_for_text(make_provider):
    provider, client = make_provider(make_completion())

    await provider.generate(
        history(),
        ResponseFormat(type="text"),
        x_client_id=uuid.uuid4(),
        x_session_id=uuid.uuid4(),
    )

    assert "response_format" not in client.chat.completions.create.call_args.kwargs


@pytest.mark.asyncio
async def test_generate_flattens_content_and_maps_tool_messages(make_provider):
    provider, client = make_provider(make_completion())
    messages = [
        Message(role="system", content=[Content(text="rules")]),
        Message(role="assistant", content=[Content(text="first"), Content(text="second")]),
        Message(role="tool", content=[Content(text="result")], tool_call_id="call_1"),
    ]

    await provider.generate(
        messages,
        ResponseFormat(type="text"),
        x_client_id=uuid.uuid4(),
        x_session_id=uuid.uuid4(),
    )

    sent = client.chat.completions.create.call_args.kwargs["messages"]
    assert sent[0] == {"role": "system", "content": "rules"}
    assert sent[1] == {"role": "assistant", "content": "first\nsecond"}
    assert sent[2] == {"role": "tool", "content": "result", "tool_call_id": "call_1"}


@pytest.mark.asyncio
async def test_generate_uses_the_model_from_the_environment(make_provider, monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "ai-sage/GigaChat3.5-432B-A28B-Reasoning")
    provider, client = make_provider(make_completion())

    await provider.generate(
        history(),
        ResponseFormat(type="text"),
        x_client_id=uuid.uuid4(),
        x_session_id=uuid.uuid4(),
    )

    assert client.chat.completions.create.call_args.kwargs["model"] == "ai-sage/GigaChat3.5-432B-A28B-Reasoning"


@pytest.mark.asyncio
async def test_generate_falls_back_to_the_default_model(make_provider, monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    provider, client = make_provider(make_completion())

    await provider.generate(
        history(),
        ResponseFormat(type="text"),
        x_client_id=uuid.uuid4(),
        x_session_id=uuid.uuid4(),
    )

    assert client.chat.completions.create.call_args.kwargs["model"] == DEFAULT_OPENAI_MODEL


@pytest.mark.asyncio
async def test_generate_returns_none_on_api_error(make_provider):
    provider, client = make_provider()
    client.chat.completions.create.side_effect = RuntimeError("boom")

    result = await provider.generate(
        history(),
        ResponseFormat(type="text"),
        x_client_id=uuid.uuid4(),
        x_session_id=uuid.uuid4(),
    )

    assert result is None


@pytest.mark.asyncio
async def test_generate_raises_authorization_error_on_401(make_provider):
    provider, client = make_provider()
    client.chat.completions.create.side_effect = AuthenticationError(
        "no", response=MagicMock(status_code=401), body=None
    )

    with pytest.raises(AuthorizationError):
        await provider.generate(
            history(),
            ResponseFormat(type="text"),
            x_client_id=uuid.uuid4(),
            x_session_id=uuid.uuid4(),
        )


# ---------------------------------------------------------------------------
# upload / close
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_returns_the_file_id(make_provider):
    provider, client = make_provider()

    file_id = await provider.upload(
        filename="drawing.png",
        data=b"\x89PNG\r\n\x1a\n",
        x_client_id=uuid.uuid4(),
        x_session_id=uuid.uuid4(),
        content_type="image/png",
    )

    assert file_id == "file-1"
    kwargs = client.files.create.call_args.kwargs
    assert kwargs["purpose"] == "assistants"
    assert kwargs["file"] == ("drawing.png", b"\x89PNG\r\n\x1a\n", "image/png")


@pytest.mark.asyncio
async def test_upload_failure_raises_runtime_error(make_provider):
    provider, client = make_provider()
    client.files.create.side_effect = RuntimeError("nope")

    with pytest.raises(RuntimeError, match="upload_failure"):
        await provider.upload(
            filename="drawing.png",
            data=b"x",
            x_client_id=uuid.uuid4(),
            x_session_id=uuid.uuid4(),
            content_type="image/png",
        )


@pytest.mark.asyncio
async def test_close_closes_the_client(make_provider):
    provider, client = make_provider()

    await provider.close()

    client.close.assert_awaited_once()
