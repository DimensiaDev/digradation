"""Bounded function-calling loop around the provider.

Each stage asks the model for one block, executes the calls it makes, sends the
results back and accepts the next text answer. The provider, the dispatcher and
the checkpoint are injected, so the loop runs against scripted turns in tests.

The loop persists nothing itself: ``checkpoint`` is called after every executed
call, which is where a stage commits the block it just received.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import structlog.stdlib

from app.agentic.errors import AnswerTruncated, ProviderDidNotRespond, ToolCallFailed, ToolLoopExceeded
from app.providers.models import (
    Content,
    FinishReason,
    FunctionResult,
    GenerationResponse,
    Message,
    Role,
    ToolCall,
)

log = structlog.stdlib.get_logger(__name__)

#: Hard bound on model-to-tool rounds within one stage. Reaching it means the
#: model is looping instead of answering, which is a bug, not a slow conversation.
MAX_TOOL_ROUNDS = 6


def _answer_text(messages: list[Message]) -> str:
    """The text the model wrote, assembling the parts of its messages."""
    return "".join(
        part.text or "" for message in messages if message.role == Role.ASSISTANT for part in message.content
    ).strip()


def _answer(response: GenerationResponse) -> str:
    """The answer of a turn, refusing one the provider cut off mid-writing."""
    if response.finish_reason == FinishReason.LENGTH:
        raise AnswerTruncated()
    return _answer_text(response.messages)


async def _generate_turn(
    generate: Callable[[list[Message]], Awaitable[GenerationResponse | None]], messages: list[Message]
) -> GenerationResponse:
    """One provider turn, with the provider-side failure modes checked."""
    response = await generate(messages)
    if response is None:
        raise ProviderDidNotRespond()
    if response.finish_reason == FinishReason.FUNCTION_CALL_ERROR:
        raise ToolCallFailed()
    return response


def _normalise_call_ids(calls: list[ToolCall]) -> None:
    """Give every call an id; GigaChat may omit the one OpenAI requires."""
    for call in calls:
        if call.id is None:
            call.id = f"call_{uuid.uuid4().hex}"


def _take_call(pending: list[ToolCall], call: Any) -> None:
    """Give a content-part call the id of the matching entry in ``pending``."""
    if call is None:
        return
    for candidate in pending:
        if candidate.function.name == call.name:
            call.id = candidate.id
            pending.remove(candidate)
            return


def _attach_call_ids(response: GenerationResponse, calls: list[ToolCall]) -> None:
    """Echo the generated ids onto GigaChat's content-part calls.

    The tool result carries the generated id, so sending the assistant turn
    without the same id would make the round ambiguous on the wire.
    """
    pending = list(calls)
    for message in response.messages:
        if message.function_call is not None:
            _take_call(pending, message.function_call)
            continue
        for part in message.content:
            _take_call(pending, part.function_call)


def _tools_state_id(response: GenerationResponse) -> str | None:
    """The tool-round id, from the envelope or the assistant message."""
    return response.tools_state_id or next(
        (message.tools_state_id for message in response.messages if message.tools_state_id is not None),
        None,
    )


def _assistant_messages(response: GenerationResponse) -> list[Message]:
    """The assistant turn, carrying the calls in both wire shapes."""
    state_id = _tools_state_id(response)
    messages = [
        message.model_copy(
            update={"tool_calls": response.tool_calls, "tools_state_id": message.tools_state_id or state_id}
        )
        for message in response.messages
        if message.role == Role.ASSISTANT
    ]
    if messages:
        return messages
    return [
        Message(
            role=Role.ASSISTANT,
            content=[Content(text=_answer_text(response.messages))],
            tool_calls=response.tool_calls,
            tools_state_id=state_id,
        )
    ]


def _result_payload(result: Any) -> Any:
    """Serialise a tool result for the provider."""
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json", exclude_none=True)
    return result


def _tool_message(call: ToolCall, result: Any, tools_state_id: str | None) -> Message:
    """The ``tool`` turn carrying one call's result in both wire shapes."""
    payload = _result_payload(result)
    function_result = FunctionResult(id=call.id, name=call.function.name, result=payload)
    return Message(
        role=Role.TOOL,
        content=[Content(text=json.dumps(payload, ensure_ascii=False), function_result=function_result)],
        tool_call_id=call.id,
        tools_state_id=tools_state_id,
    )


async def run_tool_loop(
    message_history: list[Message],
    *,
    generate: Callable[[list[Message]], Awaitable[GenerationResponse | None]],
    call_tool: Callable[[ToolCall], Awaitable[Any]],
    checkpoint: Callable[[], Awaitable[None]] | None = None,
    first_response: GenerationResponse | None = None,
) -> str:
    """Run the model until it answers with text instead of calling a tool.

    ``checkpoint`` is called after every executed call, before the result goes
    back to the model, which is where a stage commits the block it just received.
    The bound counts tool rounds, not model turns: after the last round the model
    still gets one turn to answer from the results it already has.
    """
    messages = list(message_history)
    if first_response is None:
        response = await _generate_turn(generate, messages)
    else:
        if first_response.finish_reason == FinishReason.FUNCTION_CALL_ERROR:
            raise ToolCallFailed()
        response = first_response

    for round_number in range(1, MAX_TOOL_ROUNDS + 1):
        calls = response.tool_calls
        if not calls:
            return _answer(response)

        _normalise_call_ids(calls)
        _attach_call_ids(response, calls)
        messages.extend(_assistant_messages(response))
        tools_state_id = _tools_state_id(response)
        for call in calls:
            result = await call_tool(call)
            if checkpoint is not None:
                await checkpoint()
            messages.append(_tool_message(call, result, tools_state_id))
        log.info("tool_round", round=round_number, calls=[call.function.name for call in calls])
        response = await _generate_turn(generate, messages)

    if response.tool_calls:
        raise ToolLoopExceeded(f"the model kept calling functions after {MAX_TOOL_ROUNDS} tool rounds")
    return _answer(response)
