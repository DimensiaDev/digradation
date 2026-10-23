"""Tests for :mod:`app.providers.strict_schema` and its wiring in the provider.

The schema transform is what makes ``strict: true`` mean something on an
endpoint that accepts the flag but does not validate the schema against the
OpenAI rules: the object is closed and every property is required.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from openai import BadRequestError
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from openai.types.completion_usage import CompletionUsage

from app.providers.models import Content, FunctionDefinition, Message, ResponseFormat, Tool
from app.providers.openai import OpenAIProvider
from app.providers.strict_schema import strict_schema

OPENAI_URL = "https://hub.example.test/api/v1/llm/v1"


def _schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 100, "default": "x"},
            "mode": {"type": "string", "enum": ["a", "b"]},
            "note": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": [],
                },
            },
        },
        "required": ["title"],
    }


def _tool() -> Tool:
    return Tool(
        function=FunctionDefinition(
            name="save",
            description="Save the block",
            parameters=_schema(),
        )
    )


def _completion() -> ChatCompletion:
    return ChatCompletion(
        id="chatcmpl-1",
        object="chat.completion",
        created=1_700_000_000,
        model="GigaChat/GigaChat-3-Pro",
        choices=[Choice(index=0, finish_reason="stop", message=ChatCompletionMessage(role="assistant", content="ok"))],
        usage=CompletionUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


# ---------------------------------------------------------------------------
# the transform
# ---------------------------------------------------------------------------


def test_every_object_becomes_closed_and_fully_required():
    schema = strict_schema(_schema())

    assert schema["additionalProperties"] is False
    assert schema["required"] == ["title", "mode", "note", "items"]
    assert schema["properties"]["title"] == {"type": "string"}


def test_bounds_and_defaults_leave_the_wire_but_the_enum_stays():
    schema = strict_schema(_schema())

    assert "default" not in schema["properties"]["title"]
    assert "maxLength" not in schema["properties"]["title"]
    assert schema["properties"]["mode"] == {"type": "string", "enum": ["a", "b"]}


def test_nullable_branches_are_kept_and_recursed():
    schema = strict_schema(_schema())

    assert schema["properties"]["note"]["anyOf"] == [{"type": "string"}, {"type": "null"}]


def test_array_items_are_closed_too():
    schema = strict_schema(_schema())

    item = schema["properties"]["items"]["items"]
    assert item["additionalProperties"] is False
    assert item["required"] == ["id"]


def test_a_tool_with_no_arguments_is_still_closed():
    assert strict_schema({"type": "object", "properties": {}}) == {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }


def test_a_non_object_value_is_returned_as_is():
    assert strict_schema("plain") == "plain"
    assert strict_schema(None) is None


# ---------------------------------------------------------------------------
# the payload
# ---------------------------------------------------------------------------


def test_the_payload_carries_the_strict_flag_and_the_closed_schema():
    payload = OpenAIProvider._tools_payload([_tool()], strict=True)

    function = payload[0]["function"]
    assert function["strict"] is True
    assert function["parameters"]["additionalProperties"] is False
    assert function["parameters"]["required"] == ["title", "mode", "note", "items"]


def test_the_payload_is_untouched_when_strict_is_off():
    payload = OpenAIProvider._tools_payload([_tool()], strict=False)

    function = payload[0]["function"]
    assert "strict" not in function
    assert function["parameters"] == _schema()


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("OPENAI_URL", OPENAI_URL)
    monkeypatch.delenv("OPENAI_STRICT_TOOLS", raising=False)


@pytest.fixture
async def provider():
    instance = OpenAIProvider(api_key="key", scope="openai")
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_completion())
    client.close = AsyncMock()
    instance._client = client
    yield instance, client
    await instance.close()


def _history() -> list[Message]:
    return [Message(role="user", content=[Content(text="hi")])]


async def _generate(instance: OpenAIProvider) -> None:
    await instance.generate(
        _history(),
        ResponseFormat(type="text"),
        x_client_id=uuid.uuid4(),
        x_session_id=uuid.uuid4(),
        tools=[_tool()],
    )


@pytest.mark.asyncio
async def test_generate_sends_strict_tools_by_default(provider):
    instance, client = provider

    await _generate(instance)

    function = client.chat.completions.create.call_args.kwargs["tools"][0]["function"]
    assert function["strict"] is True
    assert function["parameters"]["additionalProperties"] is False


@pytest.mark.asyncio
async def test_the_switch_turns_strict_off(monkeypatch, provider):
    monkeypatch.setenv("OPENAI_STRICT_TOOLS", "false")
    instance, client = provider
    instance._strict_tools = False

    await _generate(instance)

    function = client.chat.completions.create.call_args.kwargs["tools"][0]["function"]
    assert "strict" not in function


@pytest.mark.asyncio
async def test_a_rejected_strict_schema_is_retried_without_it(provider):
    instance, client = provider
    client.chat.completions.create = AsyncMock(
        side_effect=[
            BadRequestError("strict tools are not supported", response=MagicMock(status_code=400), body=None),
            _completion(),
        ]
    )

    await _generate(instance)

    assert client.chat.completions.create.await_count == 2
    first = client.chat.completions.create.await_args_list[0].kwargs["tools"][0]["function"]
    second = client.chat.completions.create.await_args_list[1].kwargs["tools"][0]["function"]
    assert first["strict"] is True
    assert "strict" not in second
    assert instance._strict_tools is False


@pytest.mark.asyncio
async def test_an_unrelated_400_is_not_retried(provider):
    instance, client = provider
    client.chat.completions.create = AsyncMock(
        side_effect=BadRequestError("context length exceeded", response=MagicMock(status_code=400), body=None)
    )

    assert (
        await instance.generate(
            _history(),
            ResponseFormat(type="text"),
            x_client_id=uuid.uuid4(),
            x_session_id=uuid.uuid4(),
            tools=[_tool()],
        )
        is None
    )

    assert client.chat.completions.create.await_count == 1
