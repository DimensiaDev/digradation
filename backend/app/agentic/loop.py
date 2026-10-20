"""Bounded function-calling loop around the provider.

The programme pipeline is a sequence of model turns: each stage asks the model
for one block, executes the ``save_program_draft`` call it makes, sends the
result back, and accepts the next text answer. This module owns that round trip
and nothing else: the provider, the tool dispatcher and the checkpoint are
injected, so the loop can be tested with scripted turns and no database.

The loop never outlives one stage. It does not persist the transcript; what it
persists, through ``checkpoint``, is the programme state after each executed
call, so a worker that dies mid-stage leaves the blocks it already saved behind.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import structlog.stdlib

from app.providers.models import Content, FunctionResult, GenerationResponse, Message, ToolCall

log = structlog.stdlib.get_logger(__name__)

#: Hard bound on model-to-tool rounds within one stage. Reaching it means the
#: model is looping instead of answering, which is a bug, not a slow conversation.
MAX_TOOL_ROUNDS = 6


class ProviderDidNotRespond(RuntimeError):
    """The provider returned nothing at all (``MockProvider`` "error" mode)."""


class ToolCallFailed(RuntimeError):
    """The model's function call came back malformed (``function_call_error``)."""


class AnswerTruncated(RuntimeError):
    """The model stopped at the token limit, so its text is half-written."""


class ToolLoopExceeded(RuntimeError):
    """The model kept calling functions without ever writing an answer."""


class StageNotCompleted(RuntimeError):
    """The model answered without saving the block the stage is responsible for."""


def _answer_text(messages: list[Message]) -> str:
    """The text the model wrote, assembling the parts of its messages."""
    return "".join(
        part.text or "" for message in messages if message.role == "assistant" for part in message.content
    ).strip()


def _answer(response: GenerationResponse) -> str:
    """The answer of a turn, refusing one the provider cut off mid-writing."""
    if response.finish_reason == "length":
        raise AnswerTruncated()
    return _answer_text(response.messages)


async def _generate_turn(
    generate: Callable[[list[Message]], Awaitable[GenerationResponse | None]], messages: list[Message]
) -> GenerationResponse:
    """One provider turn, with the provider-side failure modes checked."""
    response = await generate(messages)
    if response is None:
        raise ProviderDidNotRespond()
    if response.finish_reason == "function_call_error":
        raise ToolCallFailed()
    return response


def _normalise_call_ids(calls: list[ToolCall]) -> None:
    """Give every call an id.

    OpenAI requires one on both the assistant tool call and the ``tool`` result.
    GigaChat may omit it, so an id generated here is the only thing that lets the
    same history serve both wire formats.
    """
    for call in calls:
        if call.id is None:
            call.id = f"call_{uuid.uuid4().hex}"


def _link_content_call_ids(response: GenerationResponse, calls: list[ToolCall]) -> None:
    """Give GigaChat's content-part call the id the normaliser generated.

    GigaChat may omit the id on an assistant content part. The tool result then
    carries the generated id, and sending the assistant turn without the same id
    makes the round ambiguous on the wire. OpenAI has no content-part calls, so
    this only touches the v2 history.
    """
    pending = list(calls)
    for message in response.messages:
        if message.function_call is not None:
            match = next((call for call in pending if call.function.name == message.function_call.name), None)
            if match is not None:
                message.function_call.id = match.id
                pending.remove(match)
            continue
        for part in message.content:
            if part.function_call is None:
                continue
            match = next((call for call in pending if call.function.name == part.function_call.name), None)
            if match is not None:
                part.function_call.id = match.id
                pending.remove(match)


def _tools_state_id(response: GenerationResponse) -> str | None:
    """The tool-round id, from the envelope or the assistant message.

    GigaChat v2 is not consistent about where it puts ``tools_state_id``; the
    SDK example reads it from the assistant message, so both places count.
    """
    return response.tools_state_id or next(
        (message.tools_state_id for message in response.messages if message.tools_state_id is not None),
        None,
    )


def _assistant_messages(response: GenerationResponse) -> list[Message]:
    """The assistant turn, annotated with the calls the top-level response lists.

    GigaChat already nests the calls in the content parts, but the loop reads
    ``tool_calls``; OpenAI has no content parts at all. Copying the calls onto the
    stored assistant message makes the history carry both representations. A
    message that already carries a state id keeps it; the envelope value is only
    the fallback.
    """
    state_id = _tools_state_id(response)
    messages = [
        message.model_copy(
            update={"tool_calls": response.tool_calls, "tools_state_id": message.tools_state_id or state_id}
        )
        for message in response.messages
        if message.role == "assistant"
    ]
    if messages:
        return messages
    return [
        Message(
            role="assistant",
            content=[Content(text=_answer_text(response.messages))],
            tool_calls=response.tool_calls,
            tools_state_id=state_id,
        )
    ]


def _result_payload(result: Any) -> Any:
    """Serialise a tool result for the provider.

    The dispatcher returns pydantic models, but the loop stays usable with a
    plain dict in a test or another caller.
    """
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json", exclude_none=True)
    return result


def _tool_message(call: ToolCall, result: Any, tools_state_id: str | None) -> Message:
    """The ``tool`` turn carrying one call's result in both wire shapes."""
    payload = _result_payload(result)
    function_result = FunctionResult(id=call.id, name=call.function.name, result=payload)
    return Message(
        role="tool",
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

    ``generate`` receives the whole conversation of this stage, including the
    assistant turn that requested a call and the ``tool`` turn carrying its
    result. ``checkpoint`` is called after every executed call, before the result
    goes back to the model, which is where a stage commits the block it just
    received.

    The bound counts tool rounds, not model turns: after the last round the model
    still gets one turn to answer from the results it already has, and only a
    turn that reacts with another call is a model stuck in a loop.
    """
    messages = list(message_history)
    if first_response is None:
        response = await _generate_turn(generate, messages)
    else:
        if first_response.finish_reason == "function_call_error":
            raise ToolCallFailed()
        response = first_response

    for round_number in range(1, MAX_TOOL_ROUNDS + 1):
        calls = response.tool_calls
        if not calls:
            return _answer(response)

        _normalise_call_ids(calls)
        _link_content_call_ids(response, calls)
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
