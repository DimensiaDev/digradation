"""Unit tests for the bounded provider tool loop in :mod:`app.agentic.loop`.

The loop is the place where a model turn becomes an action: the assistant asks
for a function, the loop executes it, sends the result back, and stops when the
model answers with text. Nothing here touches a provider or a database — the
generate and call_tool callables are scripted, which is exactly the seam the
stage task fills with the real provider and dispatcher.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.agentic.loop import (
    MAX_TOOL_ROUNDS,
    AnswerTruncated,
    ProviderDidNotRespond,
    ToolCallFailed,
    ToolLoopExceeded,
    run_tool_loop,
)
from app.providers.models import (
    Content,
    ContentFunctionCall,
    FunctionCall,
    GenerationResponse,
    InputTokenDetails,
    Message,
    ToolCall,
    Usage,
)


def response(
    text: str | None = "готово",
    *,
    calls: list[ToolCall] | None = None,
    finish_reason: str = "stop",
    tools_state_id: str | None = None,
) -> GenerationResponse:
    return GenerationResponse(
        messages=[Message(role="assistant", content=[Content(text=text)] if text is not None else [])],
        model="test",
        thread_id="thread-1",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        finish_reason=finish_reason,
        usage=Usage(
            input_tokens=1,
            input_tokens_details=InputTokenDetails(prompt_tokens=1, cached_tokens=0),
            output_tokens=1,
            total_tokens=2,
        ),
        tool_calls=calls or [],
        tools_state_id=tools_state_id,
    )


def tool_call(name: str, arguments: dict | None = None, call_id: str | None = "call-1") -> ToolCall:
    return ToolCall(
        id=call_id,
        function=FunctionCall(name=name, arguments=json.dumps(arguments or {}, ensure_ascii=False)),
    )


async def ok_tool(call) -> dict:
    return {"ok": True}


async def answer(messages) -> GenerationResponse:
    return response("ответ")


async def unused(messages) -> GenerationResponse:
    return response("не должен вызываться")


async def test_a_text_response_is_returned_without_calling_a_tool():
    calls: list[ToolCall] = []

    async def call_tool(call):
        calls.append(call)
        return {"ok": True}

    text = await run_tool_loop(
        [Message(role="user", content=[Content(text="привет")])],
        generate=answer,
        call_tool=call_tool,
    )

    assert text == "ответ"
    assert calls == []


async def test_a_first_response_can_be_reused():
    history = [Message(role="user", content=[Content(text="привет")])]
    generated = 0

    async def generate(messages):
        nonlocal generated
        generated += 1
        return response("не должен вызываться")

    text = await run_tool_loop(
        history,
        generate=generate,
        call_tool=ok_tool,
        first_response=response("готово"),
    )

    assert text == "готово"
    assert generated == 0


async def test_a_tool_call_is_executed_and_the_result_goes_back():
    seen: list[list[Message]] = []
    responses = iter(
        [
            response(calls=[tool_call("search_sources", {"query": "данные"})], finish_reason="tool_calls"),
            response("нашёл"),
        ]
    )

    async def generate(messages):
        seen.append(list(messages))
        return next(responses)

    async def call_tool(call):
        assert call.function.name == "search_sources"
        return {"ok": True, "fragments": []}

    text = await run_tool_loop(
        [Message(role="user", content=[Content(text="найди")])],
        generate=generate,
        call_tool=call_tool,
    )

    assert text == "нашёл"
    assert len(seen) == 2
    assistant, tool = seen[1][-2:]
    assert assistant.role == "assistant"
    assert assistant.tool_calls[0].function.name == "search_sources"
    assert tool.role == "tool"
    assert tool.tool_call_id == "call-1"
    assert json.loads(tool.content[0].text) == {"ok": True, "fragments": []}
    assert tool.content[0].function_result is not None
    assert tool.content[0].function_result.name == "search_sources"


async def test_a_missing_call_id_is_generated_for_the_result():
    calls = iter(
        [
            response(calls=[tool_call("search_sources", call_id=None)], finish_reason="tool_calls"),
            response("готово"),
        ]
    )

    async def generate(messages):
        return next(calls)

    text = await run_tool_loop(
        [Message(role="user", content=[Content(text="найди")])],
        generate=generate,
        call_tool=ok_tool,
    )

    assert text == "готово"


async def test_every_call_in_a_round_is_executed():
    executed: list[str] = []
    responses = iter(
        [
            response(calls=[tool_call("a", call_id="1"), tool_call("b", call_id="2")], finish_reason="tool_calls"),
            response("готово"),
        ]
    )

    async def generate(messages):
        return next(responses)

    async def call_tool(call):
        executed.append(call.function.name)
        return {"ok": True}

    await run_tool_loop(
        [Message(role="user", content=[Content(text="сделай")])],
        generate=generate,
        call_tool=call_tool,
    )

    assert executed == ["a", "b"]


async def test_the_checkpoint_runs_after_every_call():
    checkpoints = 0
    responses = iter(
        [
            response(calls=[tool_call("a", call_id="1"), tool_call("b", call_id="2")], finish_reason="tool_calls"),
            response("готово"),
        ]
    )

    async def generate(messages):
        return next(responses)

    async def checkpoint():
        nonlocal checkpoints
        checkpoints += 1

    await run_tool_loop(
        [Message(role="user", content=[Content(text="сделай")])],
        generate=generate,
        call_tool=ok_tool,
        checkpoint=checkpoint,
    )

    assert checkpoints == 2


async def test_a_pydantic_tool_result_is_serialized_for_the_provider():
    class Result:
        def model_dump(self, **kwargs):
            assert kwargs["mode"] == "json"
            return {"ok": True, "stage": "competences"}

    responses = iter(
        [
            response(calls=[tool_call("save_program_draft")], finish_reason="tool_calls"),
            response("готово"),
        ]
    )
    seen: list[list[Message]] = []

    async def generate(messages):
        seen.append(list(messages))
        return next(responses)

    async def call_tool(call):
        return Result()

    await run_tool_loop(
        [Message(role="user", content=[Content(text="сохрани")])],
        generate=generate,
        call_tool=call_tool,
    )

    tool = seen[1][-1]
    assert json.loads(tool.content[0].text) == {"ok": True, "stage": "competences"}


async def test_a_response_without_an_assistant_message_still_produces_one():
    responses = iter(
        [
            GenerationResponse(
                messages=[],
                model="test",
                thread_id="thread-1",
                created_at=datetime(2024, 1, 1, tzinfo=UTC),
                finish_reason="tool_calls",
                usage=Usage(
                    input_tokens=1,
                    input_tokens_details=InputTokenDetails(prompt_tokens=1, cached_tokens=0),
                    output_tokens=1,
                    total_tokens=2,
                ),
                tool_calls=[tool_call("search_sources")],
            ),
            response("готово"),
        ]
    )
    seen: list[list[Message]] = []

    async def generate(messages):
        seen.append(list(messages))
        return next(responses)

    await run_tool_loop(
        [Message(role="user", content=[Content(text="найди")])],
        generate=generate,
        call_tool=ok_tool,
    )

    assistant = seen[1][-2]
    assert assistant.role == "assistant"
    assert assistant.content[0].text == ""
    assert assistant.tool_calls[0].function.name == "search_sources"


async def test_tools_state_id_is_carried_to_the_tool_message():
    responses = iter(
        [
            response(calls=[tool_call("search_sources")], finish_reason="tool_calls", tools_state_id="state-1"),
            response("готово"),
        ]
    )
    seen: list[list[Message]] = []

    async def generate(messages):
        seen.append(list(messages))
        return next(responses)

    await run_tool_loop(
        [Message(role="user", content=[Content(text="найди")])],
        generate=generate,
        call_tool=ok_tool,
    )

    assert seen[1][-1].tools_state_id == "state-1"


async def test_none_becomes_provider_did_not_respond():
    async def generate(messages):
        return None

    with pytest.raises(ProviderDidNotRespond):
        await run_tool_loop(
            [Message(role="user", content=[Content(text="привет")])],
            generate=generate,
            call_tool=ok_tool,
        )


async def test_function_call_error_becomes_tool_call_failed():
    async def generate(messages):
        return response(finish_reason="function_call_error")

    with pytest.raises(ToolCallFailed):
        await run_tool_loop(
            [Message(role="user", content=[Content(text="привет")])],
            generate=generate,
            call_tool=ok_tool,
        )


async def test_a_first_response_with_function_call_error_is_refused():
    with pytest.raises(ToolCallFailed):
        await run_tool_loop(
            [Message(role="user", content=[Content(text="привет")])],
            generate=unused,
            call_tool=ok_tool,
            first_response=response(finish_reason="function_call_error"),
        )


async def test_a_truncated_answer_is_refused():
    async def generate(messages):
        return response("половина", finish_reason="length")

    with pytest.raises(AnswerTruncated):
        await run_tool_loop(
            [Message(role="user", content=[Content(text="привет")])],
            generate=generate,
            call_tool=ok_tool,
        )


async def test_the_loop_stops_after_the_round_budget():
    async def generate(messages):
        return response(calls=[tool_call("search_sources")], finish_reason="tool_calls")

    with pytest.raises(ToolLoopExceeded, match=str(MAX_TOOL_ROUNDS)):
        await run_tool_loop(
            [Message(role="user", content=[Content(text="найди")])],
            generate=generate,
            call_tool=ok_tool,
        )


async def test_a_content_part_call_receives_the_generated_id():
    turn = response(calls=[tool_call("search_sources")], finish_reason="tool_calls")
    turn.messages[0].content = [Content(function_call=ContentFunctionCall(name="search_sources"))]
    responses = iter([turn, response("готово")])
    seen: list[list[Message]] = []

    async def generate(messages):
        seen.append(list(messages))
        return next(responses)

    await run_tool_loop(
        [Message(role="user", content=[Content(text="найди")])],
        generate=generate,
        call_tool=ok_tool,
    )

    asserted = seen[1][-2].tool_calls[0].id
    assert asserted is not None
    assert turn.messages[0].content[0].function_call is not None
    assert turn.messages[0].content[0].function_call.id == asserted


async def test_a_message_level_tools_state_id_is_carried_to_the_result():
    turn = response(calls=[tool_call("search_sources")], finish_reason="tool_calls")
    turn.messages[0].tools_state_id = "state-on-message"
    responses = iter([turn, response("готово")])
    seen: list[list[Message]] = []

    async def generate(messages):
        seen.append(list(messages))
        return next(responses)

    await run_tool_loop(
        [Message(role="user", content=[Content(text="найди")])],
        generate=generate,
        call_tool=ok_tool,
    )

    assistant, tool = seen[1][-2:]
    assert assistant.tools_state_id == "state-on-message"
    assert tool.tools_state_id == "state-on-message"
