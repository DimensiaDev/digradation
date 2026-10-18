"""Tests for the Pydantic schemas in :mod:`app.providers.models`.

These are the wire types exchanged with GigaChat. Several of them carry
validators that silently accept a second input shape, so most tests here feed the
model two different representations of the same value and assert both land in the
same place.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models.chat import ChatMessage, UserRole
from app.providers.models import (
    AuthResponse,
    Content,
    ContentFunctionCall,
    FileSchema,
    FunctionCall,
    FunctionDefinition,
    FunctionSpecification,
    FunctionsSpecifications,
    GenerationRequest,
    GenerationResponse,
    HarnessStructuredOutput,
    InputTokenDetails,
    Message,
    ModelOptions,
    ResponseFormat,
    Tool,
    ToolCall,
    ToolConfig,
    ToolsFunctions,
    Usage,
)

FUTURE_MS = 4_102_444_800_000  # 2100-01-01T00:00:00Z
FUTURE = datetime.fromtimestamp(FUTURE_MS / 1000, tz=UTC)


# ---------------------------------------------------------------------------
# AuthResponse
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [FUTURE_MS, float(FUTURE_MS)])
def test_auth_response_parses_int_and_float_ms_epoch(raw):
    response = AuthResponse.model_validate({"access_token": "tok", "expires_at": raw})

    assert response.access_token == "tok"
    assert response.expires_at == FUTURE
    assert response.expires_at.tzinfo is not None


def test_auth_response_passes_through_a_datetime():
    moment = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    response = AuthResponse.model_validate({"access_token": "tok", "expires_at": moment})

    assert response.expires_at == moment


def test_auth_response_rejects_an_unparseable_expiry():
    with pytest.raises(ValidationError):
        AuthResponse.model_validate({"access_token": "tok", "expires_at": "tomorrow"})


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------


def test_content_none_files_becomes_an_empty_list():
    content = Content(text="hello", files=None)

    assert content.files == []
    assert content.inline_data is None


def test_content_files_default_to_empty_without_the_key():
    content = Content(text="hello")

    assert content.files == []


def test_content_files_accepts_a_list_of_dicts():
    content = Content(text="hello", files=[{"id": "a"}, {"id": "b"}])

    assert content.files == [FileSchema(id="a"), FileSchema(id="b")]


def test_content_files_rejects_a_malformed_entry():
    with pytest.raises(ValidationError):
        Content(text="hello", files=[{"id": 1}])


def test_content_carries_inline_data():
    content = Content(text="", inline_data={"image/png": "base64"})

    assert content.inline_data == {"image/png": "base64"}


# ---------------------------------------------------------------------------
# Message.from_chat_message
# ---------------------------------------------------------------------------


def test_message_from_chat_message_without_files():
    row = ChatMessage(role=UserRole.USER, content="привет")

    message = Message.from_chat_message(row)

    assert message.role == "user"
    assert message.content == [Content(text="привет", files=[])]


def test_message_from_chat_message_splits_and_strips_files_str():
    row = ChatMessage(role=UserRole.ASSISTANT, content="ok", files_str=" a1 , a2 ,a3")

    message = Message.from_chat_message(row)

    assert message.role == "assistant"
    assert message.content[0].files == [FileSchema(id="a1"), FileSchema(id="a2"), FileSchema(id="a3")]


def test_message_from_chat_message_preserves_the_system_role():
    row = ChatMessage(role=UserRole.SYSTEM, content="system prompt")

    assert Message.from_chat_message(row).role == "system"


def test_message_tool_call_id_defaults_to_none():
    assert Message(role="user", content=[{"text": "hi"}]).tool_call_id is None


def test_message_carries_the_tool_call_id():
    message = Message(role="tool", content=[{"text": "result"}], tool_call_id="call_1")

    assert message.tool_call_id == "call_1"


# ---------------------------------------------------------------------------
# Tool calling
# ---------------------------------------------------------------------------


def test_tool_defaults_to_a_function():
    tool = Tool(function=FunctionDefinition(name="lookup"))

    assert tool.type == "function"
    assert tool.function.name == "lookup"
    assert tool.function.description is None
    assert tool.function.parameters is None


def test_tool_keeps_its_description_and_parameters():
    tool = Tool(
        function=FunctionDefinition(
            name="lookup",
            description="Look something up",
            parameters={"type": "object"},
            strict=True,
        )
    )

    assert tool.function.description == "Look something up"
    assert tool.function.parameters == {"type": "object"}
    assert tool.function.strict is True


def test_tool_rejects_an_unknown_type():
    with pytest.raises(ValidationError):
        Tool(type="retrieval", function={"name": "lookup"})


def test_tool_call_requires_an_id_name_and_arguments():
    call = ToolCall(id="call_1", function=FunctionCall(name="lookup", arguments='{"q": "x"}'))

    assert call.type == "function"
    assert call.id == "call_1"
    assert call.function.name == "lookup"
    assert call.function.arguments == '{"q": "x"}'


def test_tool_call_rejects_a_missing_function_name():
    with pytest.raises(ValidationError):
        ToolCall(id="call_1", function={"arguments": "{}"})


def test_tool_call_id_is_optional():
    assert ToolCall(function={"name": "lookup", "arguments": "{}"}).id is None


def test_content_defaults_to_empty_text_and_no_function_call():
    content = Content()

    assert content.text == ""
    assert content.function_call is None


def test_content_keeps_a_string_function_call_argument():
    content = Content(function_call=ContentFunctionCall(id="call_1", name="lookup", arguments='{"q": "x"}'))

    assert content.function_call.id == "call_1"
    assert content.function_call.name == "lookup"
    assert content.function_call.arguments == '{"q": "x"}'


def test_content_serializes_object_function_call_arguments():
    content = Content.model_validate({"function_call": {"name": "lookup", "arguments": {"q": "x"}}})

    assert content.function_call.arguments == '{"q": "x"}'


def test_content_function_call_requires_a_name():
    with pytest.raises(ValidationError):
        Content.model_validate({"function_call": {"arguments": "{}"}})


def test_tool_config_defaults_to_auto():
    config = ToolConfig()

    assert config.mode == "auto"
    assert config.tool_name is None
    assert config.function_name is None


def test_tool_config_rejects_an_unknown_mode():
    with pytest.raises(ValidationError):
        ToolConfig(mode="always")


def test_tools_functions_dump_to_the_gigachat_v2_shape():
    functions = ToolsFunctions(
        functions=FunctionsSpecifications(
            specifications=[
                FunctionSpecification(
                    name="lookup",
                    description="Look something up",
                    parameters={"type": "object"},
                )
            ]
        )
    )

    assert functions.model_dump(exclude_none=True) == {
        "functions": {
            "specifications": [{"name": "lookup", "description": "Look something up", "parameters": {"type": "object"}}]
        }
    }


# ---------------------------------------------------------------------------
# ResponseFormat
# ---------------------------------------------------------------------------


def test_response_format_class_vars():
    assert ResponseFormat.TYPE_TEXT == "text"
    assert ResponseFormat.TYPE_JSON_SCHEMA == "json_schema"


def test_response_format_json_schema_requires_a_schema():
    with pytest.raises(ValidationError, match="schema is required"):
        ResponseFormat(type="json_schema", strict=True)


def test_response_format_json_schema_requires_strict():
    with pytest.raises(ValidationError, match="schema is required"):
        ResponseFormat(type="json_schema", schema={"type": "object"})


def test_response_format_json_schema_with_both_is_valid():
    fmt = ResponseFormat(type="json_schema", schema={"type": "object"}, strict=True)

    assert fmt.json_schema == {"type": "object"}
    assert fmt.strict is True


def test_response_format_json_schema_validates_through_the_schema_alias():
    fmt = ResponseFormat.model_validate({"type": "json_schema", "schema": {"type": "object"}, "strict": True})

    assert fmt.json_schema == {"type": "object"}


def test_response_format_text_rejects_a_schema():
    with pytest.raises(ValidationError, match="schema must not be provided"):
        ResponseFormat(type="text", schema={"type": "object"})


def test_response_format_text_rejects_strict():
    with pytest.raises(ValidationError, match="schema must not be provided"):
        ResponseFormat(type="text", strict=True)


def test_response_format_text_with_neither_is_valid():
    assert ResponseFormat(type="text") == ResponseFormat(type="text", json_schema=None, strict=None)


def test_response_format_rejects_an_unknown_type():
    with pytest.raises(ValidationError):
        ResponseFormat(type="xml")


# ---------------------------------------------------------------------------
# ModelOptions / GenerationRequest
# ---------------------------------------------------------------------------


def test_model_options_wraps_a_response_format():
    options = ModelOptions(response_format=ResponseFormat(type="text"))

    assert options.response_format.type == "text"


def test_model_options_propagates_a_bad_response_format():
    with pytest.raises(ValidationError):
        ModelOptions(response_format={"type": "json_schema", "strict": True})


def test_generation_request_holds_model_messages_and_options():
    request = GenerationRequest(
        model="GigaChat-3-Ultra",
        messages=[Message(role="user", content=[{"text": "hi"}])],
        model_options=ModelOptions(response_format=ResponseFormat(type="text")),
    )

    dumped = request.model_dump(exclude_unset=True, by_alias=True)

    assert dumped["model"] == "GigaChat-3-Ultra"
    assert dumped["messages"][0]["content"][0]["text"] == "hi"
    assert dumped["model_options"]["response_format"] == {"type": "text"}


def test_generation_request_requires_a_model():
    with pytest.raises(ValidationError):
        GenerationRequest(
            messages=[Message(role="user", content=[{"text": "hi"}])],
            model_options=ModelOptions(response_format=ResponseFormat(type="text")),
        )


def test_generation_request_carries_gigachat_v2_tools():
    request = GenerationRequest(
        model="GigaChat-3-Ultra",
        messages=[Message(role="user", content=[{"text": "hi"}])],
        model_options=ModelOptions(response_format=ResponseFormat(type="text")),
        tool_config=ToolConfig(mode="auto"),
        tools=[
            ToolsFunctions(
                functions=FunctionsSpecifications(
                    specifications=[FunctionSpecification(name="lookup", parameters={"type": "object"})]
                )
            )
        ],
    )

    dumped = request.model_dump(exclude_unset=True, exclude_none=True, by_alias=True)

    assert dumped["tool_config"] == {"mode": "auto"}
    specification = dumped["tools"][0]["functions"]["specifications"][0]
    assert specification["name"] == "lookup"
    assert specification["parameters"] == {"type": "object"}
    assert "description" not in specification


def test_generation_request_drops_tool_fields_when_unset():
    request = GenerationRequest(
        model="GigaChat-3-Ultra",
        messages=[Message(role="user", content=[{"text": "hi"}])],
        model_options=ModelOptions(response_format=ResponseFormat(type="text")),
    )

    dumped = request.model_dump(exclude_unset=True, exclude_none=True, by_alias=True)

    assert "tools" not in dumped
    assert "tool_config" not in dumped


# ---------------------------------------------------------------------------
# GenerationResponse
# ---------------------------------------------------------------------------


def response_body(**overrides) -> dict:
    body = {
        "messages": [{"role": "assistant", "content": [{"text": "ответ"}]}],
        "model": "GigaChat-3-Ultra",
        "created_at": FUTURE_MS,
        "finish_reason": "stop",
        "usage": {
            "input_tokens": 10,
            "input_tokens_details": {"prompt_tokens": 8, "cached_tokens": 2},
            "output_tokens": 5,
            "total_tokens": 15,
        },
    }
    body.update(overrides)
    return body


def test_generation_response_parses_an_int_ms_created_at():
    response = GenerationResponse.model_validate(response_body())

    assert response.created_at == FUTURE
    assert response.thread_id is None


def test_generation_response_parses_a_float_ms_created_at():
    response = GenerationResponse.model_validate(response_body(created_at=float(FUTURE_MS)))

    assert response.created_at == FUTURE


def test_generation_response_passes_through_a_datetime_created_at():
    moment = datetime(2026, 5, 6, 7, 8, 9, tzinfo=UTC)

    response = GenerationResponse.model_validate(response_body(created_at=moment))

    assert response.created_at == moment


def test_generation_response_keeps_the_thread_id():
    response = GenerationResponse.model_validate(response_body(thread_id="thread-1"))

    assert response.thread_id == "thread-1"


def test_generation_response_rejects_an_unknown_finish_reason():
    with pytest.raises(ValidationError):
        GenerationResponse.model_validate(response_body(finish_reason="exploded"))


def test_generation_response_accepts_openai_finish_reasons():
    assert GenerationResponse.model_validate(response_body(finish_reason="tool_calls")).finish_reason == "tool_calls"
    assert (
        GenerationResponse.model_validate(response_body(finish_reason="content_filter")).finish_reason
        == "content_filter"
    )


def test_generation_response_defaults_to_no_tool_calls():
    assert GenerationResponse.model_validate(response_body()).tool_calls == []


def test_generation_response_carries_tool_calls():
    body = response_body(tool_calls=[{"id": "call_1", "function": {"name": "lookup", "arguments": '{"q": "x"}'}}])

    response = GenerationResponse.model_validate(body)

    assert [call.id for call in response.tool_calls] == ["call_1"]
    assert response.tool_calls[0].function.name == "lookup"


def test_generation_response_requires_usage():
    body = response_body()
    del body["usage"]

    with pytest.raises(ValidationError):
        GenerationResponse.model_validate(body)


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------


def test_usage_requires_the_prompt_token_details():
    with pytest.raises(ValidationError):
        Usage(input_tokens=1, output_tokens=2, total_tokens=3)


def test_input_token_details_is_a_plain_model():
    assert InputTokenDetails(prompt_tokens=8, cached_tokens=2).cached_tokens == 2


# ---------------------------------------------------------------------------
# HarnessStructuredOutput
# ---------------------------------------------------------------------------


def test_harness_structured_output_requires_only_a_message():
    """`chat_name` is optional so a model that omits it still yields a usable reply.

    The schema asks the provider for both, but a missing name must not cost the user
    their answer: the session simply keeps its default title.
    """
    output = HarnessStructuredOutput(message="ок")

    assert output.message == "ок"
    assert output.chat_name is None


def test_harness_structured_output_takes_both_fields():
    output = HarnessStructuredOutput.model_validate({"chat_name": "Заметки", "message": "ок"})

    assert output.chat_name == "Заметки"
    assert output.message == "ок"


def test_harness_structured_output_rejects_a_missing_message():
    with pytest.raises(ValidationError):
        HarnessStructuredOutput.model_validate({"chat_name": "Заметки"})
