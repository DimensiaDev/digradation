"""Tests for the bounded tool loop in ``app.agentic.loop``.

The loop takes a provider, a tool dispatcher and a checkpoint, so every test here
is a scripted conversation: no network, no database, no task queue. The scripted
provider returns the turns in order and records what it was asked, which is how
the tests check the history the model actually sees.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

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

USAGE = Usage(
    input_tokens=1,
    input_tokens_details=InputTokenDetails(prompt_tokens=1, cached_tokens=0),
    output_tokens=1,
    total_tokens=2,
)


def answer(
    text: str = "",
    *,
    calls: tuple[ToolCall, ...] = (),
    finish_reason: str = "stop",
    tools_state_id: str | None = None,
) -> GenerationResponse:
    """One scripted assistant turn."""
    return GenerationResponse(
        messages=[Message(role="assistant", content=[Content(text=text)] if text else [])],
        model="scripted",
        created_at=datetime.now(UTC),
        finish_reason=finish_reason,
        usage=USAGE,
        tool_calls=list(calls),
        tools_state_id=tools_state_id,
    )


def call(name: str = "save_program_draft", arguments: str = "{}", call_id: str | None = None) -> ToolCall:
    return ToolCall(id=call_id, function=FunctionCall(name=name, arguments=arguments))


class Script:
    """A provider that answers from a script and records the requests."""

    def __init__(self, *answers: GenerationResponse | None) -> None:
        self.answers = list(answers)
        self.requests: list[list[Message]] = []

    async def __call__(self, messages: list[Message]) -> GenerationResponse | None:
        self.requests.append(list(messages))
        return self.answers.pop(0) if self.answers else None


class Dispatcher:
    """A tool dispatcher that records the calls and answers from a script."""

    def __init__(self, *results: Any) -> None:
        self.results = list(results)
        self.calls: list[ToolCall] = []

    async def __call__(self, tool_call: ToolCall) -> Any:
        self.calls.append(tool_call)
        return self.results.pop(0) if self.results else {"ok": True}


async def test_a_text_answer_ends_the_loop_at_once() -> None:
    generate = Script(answer("готово"))
    tools = Dispatcher()

    text = await run_tool_loop([], generate=generate, call_tool=tools)

    assert text == "готово"
    assert tools.calls == []
    assert len(generate.requests) == 1


async def test_a_call_is_executed_and_its_result_returns_to_the_model() -> None:
    generate = Script(answer(calls=(call(call_id="call-1"),)), answer("сохранил"))
    tools = Dispatcher({"ok": True, "note": "принято"})
    history = [Message(role="user", content=[Content(text="начни")])]

    text = await run_tool_loop(history, generate=generate, call_tool=tools)

    assert text == "сохранил"
    assert [tool_call.function.name for tool_call in tools.calls] == ["save_program_draft"]
    assistant, tool_message = generate.requests[1][-2], generate.requests[1][-1]
    assert assistant.role == "assistant"
    assert assistant.tool_calls[0].id == "call-1"
    assert tool_message.role == "tool"
    assert tool_message.tool_call_id == "call-1"
    assert json.loads(tool_message.content[0].text) == {"ok": True, "note": "принято"}


async def test_the_checkpoint_runs_after_the_call_and_before_the_next_turn() -> None:
    events: list[str] = []
    script = Script(answer(calls=(call(call_id="call-1"),)), answer("готово"))

    async def generate(messages: list[Message]) -> GenerationResponse | None:
        events.append("generate")
        return await script(messages)

    async def call_tool(tool_call: ToolCall) -> dict:
        events.append("tool")
        return {"ok": True}

    async def checkpoint() -> None:
        events.append("checkpoint")

    await run_tool_loop([], generate=generate, call_tool=call_tool, checkpoint=checkpoint)

    assert events == ["generate", "tool", "checkpoint", "generate"]


async def test_two_calls_in_one_turn_are_both_executed() -> None:
    calls = (call(name="search_sources", call_id="call-1"), call(call_id="call-2"))
    generate = Script(answer(calls=calls), answer("готово"))
    tools = Dispatcher({"ok": True}, {"ok": True})

    await run_tool_loop([], generate=generate, call_tool=tools)

    assert len(tools.calls) == 2
    assert [message.role for message in generate.requests[1][-3:]] == ["assistant", "tool", "tool"]


async def test_a_missing_call_id_is_generated_for_both_sides_of_the_round() -> None:
    generate = Script(answer(calls=(call(),)), answer("готово"))

    await run_tool_loop([], generate=generate, call_tool=Dispatcher())

    assistant, tool_message = generate.requests[1][-2], generate.requests[1][-1]
    assert assistant.tool_calls[0].id
    assert assistant.tool_calls[0].id == tool_message.tool_call_id
    assert tool_message.content[0].function_result.id == tool_message.tool_call_id


async def test_the_state_id_of_a_round_travels_with_both_messages() -> None:
    generate = Script(answer(calls=(call(call_id="call-1"),), tools_state_id="state-1"), answer("готово"))

    await run_tool_loop([], generate=generate, call_tool=Dispatcher())

    assistant, tool_message = generate.requests[1][-2], generate.requests[1][-1]
    assert assistant.tools_state_id == "state-1"
    assert tool_message.tools_state_id == "state-1"


async def test_a_state_id_on_the_message_is_used_when_the_envelope_has_none() -> None:
    first = answer(calls=(call(call_id="call-1"),))
    first.messages[0].tools_state_id = "state-2"
    generate = Script(first, answer("готово"))

    await run_tool_loop([], generate=generate, call_tool=Dispatcher())

    assert generate.requests[1][-2].tools_state_id == "state-2"


async def test_a_truncated_answer_is_refused() -> None:
    with pytest.raises(AnswerTruncated):
        await run_tool_loop([], generate=Script(answer("нач", finish_reason="length")), call_tool=Dispatcher())


async def test_a_provider_that_returns_nothing_is_refused() -> None:
    with pytest.raises(ProviderDidNotRespond):
        await run_tool_loop([], generate=Script(None), call_tool=Dispatcher())


async def test_a_malformed_call_is_refused() -> None:
    with pytest.raises(ToolCallFailed):
        await run_tool_loop([], generate=Script(answer(finish_reason="function_call_error")), call_tool=Dispatcher())


async def test_a_model_that_never_stops_calling_is_cut_off() -> None:
    turns = [answer(calls=(call(call_id=f"call-{number}"),)) for number in range(MAX_TOOL_ROUNDS + 1)]

    with pytest.raises(ToolLoopExceeded):
        await run_tool_loop([], generate=Script(*turns), call_tool=Dispatcher())


async def test_the_last_round_still_gets_one_turn_to_answer() -> None:
    turns = [answer(calls=(call(call_id=f"call-{number}"),)) for number in range(MAX_TOOL_ROUNDS)]
    turns.append(answer("готово"))

    text = await run_tool_loop([], generate=Script(*turns), call_tool=Dispatcher())

    assert text == "готово"


async def test_a_first_response_is_used_instead_of_calling_the_provider_again() -> None:
    generate = Script(answer("лишний"))

    text = await run_tool_loop([], generate=generate, call_tool=Dispatcher(), first_response=answer("готово"))

    assert text == "готово"
    assert generate.requests == []


async def test_a_first_response_that_asked_for_a_call_is_still_executed() -> None:
    tools = Dispatcher()

    text = await run_tool_loop(
        [],
        generate=Script(answer("готово")),
        call_tool=tools,
        first_response=answer(calls=(call(call_id="call-1"),)),
    )

    assert text == "готово"
    assert len(tools.calls) == 1


async def test_a_message_level_call_without_an_id_gets_the_generated_one() -> None:
    first = answer(calls=(call(call_id=None),))
    first.messages[0].function_call = ContentFunctionCall(name="save_program_draft")
    generate = Script(first, answer("готово"))

    await run_tool_loop([], generate=generate, call_tool=Dispatcher())

    assistant = generate.requests[1][-2]
    assert assistant.tool_calls[0].id
    assert assistant.function_call.id == assistant.tool_calls[0].id


async def test_a_content_part_call_without_an_id_gets_the_generated_one() -> None:
    first = answer(calls=(call(call_id=None),))
    first.messages[0].content.append(Content(function_call=ContentFunctionCall(name="save_program_draft")))
    generate = Script(first, answer("готово"))

    await run_tool_loop([], generate=generate, call_tool=Dispatcher())

    assistant = generate.requests[1][-2]
    assert assistant.content[-1].function_call.id == assistant.tool_calls[0].id


async def test_a_response_without_an_assistant_message_still_produces_a_tool_round() -> None:
    first = answer(calls=(call(call_id="call-1"),))
    first.messages = []
    generate = Script(first, answer("готово"))

    await run_tool_loop([], generate=generate, call_tool=Dispatcher())

    assistant, tool_message = generate.requests[1][-2], generate.requests[1][-1]
    assert assistant.role == "assistant"
    assert assistant.tool_calls[0].id == "call-1"
    assert tool_message.role == "tool"


async def test_a_first_response_that_failed_its_call_is_refused() -> None:
    with pytest.raises(ToolCallFailed):
        await run_tool_loop(
            [],
            generate=Script(),
            call_tool=Dispatcher(),
            first_response=answer(finish_reason="function_call_error"),
        )
