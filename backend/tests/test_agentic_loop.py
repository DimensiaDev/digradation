"""Tests for the bounded function-calling loop in ``app.agentic.loop``.

The loop is pure orchestration: ``generate`` and ``call_tool`` are injected, so
the tests drive it with scripted provider turns and need neither a database nor
the network.
"""

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
from app.providers.models import Content, FunctionCall, GenerationResponse, InputTokenDetails, Message, Usage

HISTORY = [Message(role="user", content=[Content(text="Собери программу")])]


def turn(content: list[Content], finish_reason: str, tools_state_id: str | None = None) -> GenerationResponse:
    """A provider response carrying one assistant message."""
    return GenerationResponse(
        messages=[Message(role="assistant", content=content, tools_state_id=tools_state_id)],
        model="test-model",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        finish_reason=finish_reason,
        usage=Usage(
            input_tokens=1,
            input_tokens_details=InputTokenDetails(prompt_tokens=1, cached_tokens=0),
            output_tokens=1,
            total_tokens=2,
        ),
    )


def text_turn(text: str, finish_reason: str = "stop") -> GenerationResponse:
    return turn([Content(text=text)], finish_reason)


def call_turn(name: str, call_id: str = "call-1") -> GenerationResponse:
    call = FunctionCall(id=call_id, name=name, arguments={"query": "образовательные программы"})
    return turn([Content(function_call=call)], "function_call", tools_state_id="tools-state-1")


class ScriptedProvider:
    """A ``generate`` that returns the scripted turns in order and records its histories."""

    def __init__(self, *turns: GenerationResponse | None):
        self.turns = list(turns)
        self.histories: list[list[Message]] = []

    async def generate(self, messages: list[Message]) -> GenerationResponse | None:
        self.histories.append(list(messages))
        return self.turns.pop(0)


class RecordingTools:
    """A ``call_tool`` that records the calls it receives."""

    def __init__(self):
        self.calls: list[FunctionCall] = []

    async def call_tool(self, call: FunctionCall) -> dict:
        self.calls.append(call)
        return {"ok": True, "echo": call.name}


async def test_a_text_answer_is_returned_as_is():
    provider = ScriptedProvider(text_turn("Черновик готов"))
    tools = RecordingTools()

    answer = await run_tool_loop(HISTORY, generate=provider.generate, call_tool=tools.call_tool)

    assert answer == "Черновик готов"
    assert tools.calls == []


async def test_a_cut_off_answer_is_refused():
    provider = ScriptedProvider(text_turn("Программа состоит из трёх модулей,", finish_reason="length"))

    with pytest.raises(AnswerTruncated):
        await run_tool_loop(HISTORY, generate=provider.generate, call_tool=RecordingTools().call_tool)


async def test_no_response_at_all_is_an_error():
    provider = ScriptedProvider(None)

    with pytest.raises(ProviderDidNotRespond):
        await run_tool_loop(HISTORY, generate=provider.generate, call_tool=RecordingTools().call_tool)


async def test_a_broken_function_call_is_an_error():
    provider = ScriptedProvider(text_turn("", finish_reason="function_call_error"))

    with pytest.raises(ToolCallFailed):
        await run_tool_loop(HISTORY, generate=provider.generate, call_tool=RecordingTools().call_tool)


async def test_function_calls_are_executed_and_their_results_sent_back():
    provider = ScriptedProvider(call_turn("search_sources"), text_turn("Нашёл три источника"))
    tools = RecordingTools()

    answer = await run_tool_loop(HISTORY, generate=provider.generate, call_tool=tools.call_tool)

    assert answer == "Нашёл три источника"
    assert [call.name for call in tools.calls] == ["search_sources"]
    result_message = provider.histories[1][-1]
    assert result_message.role == "tool"
    assert result_message.tools_state_id == "tools-state-1"
    result = result_message.content[0].function_result
    assert result is not None
    assert result.name == "search_sources"
    assert result.result == {"ok": True, "echo": "search_sources"}


async def test_the_model_still_answers_after_the_last_tool_round():
    """The bound counts tool rounds, so the results of the last one are answered."""
    provider = ScriptedProvider(
        *[call_turn("search_sources", call_id=f"call-{number}") for number in range(MAX_TOOL_ROUNDS)],
        text_turn("Итог по найденным источникам"),
    )
    tools = RecordingTools()

    answer = await run_tool_loop(HISTORY, generate=provider.generate, call_tool=tools.call_tool)

    assert answer == "Итог по найденным источникам"
    assert len(tools.calls) == MAX_TOOL_ROUNDS
    assert len(provider.histories) == MAX_TOOL_ROUNDS + 1


async def test_another_call_after_the_last_round_is_an_error():
    provider = ScriptedProvider(
        *[call_turn("search_sources", call_id=f"call-{number}") for number in range(MAX_TOOL_ROUNDS + 1)]
    )

    with pytest.raises(ToolLoopExceeded, match="kept calling functions"):
        await run_tool_loop(HISTORY, generate=provider.generate, call_tool=RecordingTools().call_tool)
