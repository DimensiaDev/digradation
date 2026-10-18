"""Bounded function-calling loop around the provider.

The methodist scenario is a sequence of model turns: clarify, search for sources,
save a draft, present it. GigaChat drives that sequence with user functions, and
this module runs the round trip — call the model, execute the functions it asked
for, send the results back — until it answers with text instead of calling
another function.

The loop is deliberately short-lived and synchronous with respect to one reply:
the rounds are not stored in the chat history, because what the transcript keeps
is the answer, while the draft itself lives in ``chat_programs``.
"""

from collections.abc import Awaitable, Callable

import structlog.stdlib

from app.providers.models import Content, FunctionCall, FunctionResult, GenerationResponse, Message

log = structlog.stdlib.get_logger(__name__)

#: Hard bound on model-to-tool rounds within one reply. Reaching it means the
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


def _function_calls(message: Message) -> list[FunctionCall]:
    """The function calls carried by one message, if any."""
    return [part.function_call for part in message.content if part.function_call is not None]


def _answer_text(messages: list[Message]) -> str:
    """The text the model wrote, assembling the parts of its messages."""
    return "".join(
        part.text or "" for message in messages if message.role == "assistant" for part in message.content
    ).strip()


def _answer(response: GenerationResponse) -> str:
    """The answer of a turn, refusing one the provider cut off mid-writing.

    ``length`` means generation stopped at the token limit: the text is real,
    but half-written, and returning it would present an unfinished answer as
    the model's final word.
    """
    if response.finish_reason == "length":
        raise AnswerTruncated()
    return _answer_text(response.messages)


async def run_tool_loop(
    message_history: list[Message],
    *,
    generate: Callable[[list[Message]], Awaitable[GenerationResponse | None]],
    call_tool: Callable[[FunctionCall], Awaitable[dict]],
) -> str:
    """Run the model until it answers with text instead of calling a function.

    ``generate`` receives the whole conversation of this reply — including the
    calls already made and their results — because the v2 endpoint expects the
    assistant message that requested a function and the ``tool`` message carrying
    its result to be sent back before the model continues.
    """
    messages = list(message_history)
    for round_number in range(1, MAX_TOOL_ROUNDS + 1):
        response = await generate(messages)
        if response is None:
            raise ProviderDidNotRespond()
        if response.finish_reason == "function_call_error":
            raise ToolCallFailed()

        calls = [call for message in response.messages for call in _function_calls(message)]
        if not calls:
            return _answer(response)

        messages.extend(response.messages)
        for message in response.messages:
            message_calls = _function_calls(message)
            if not message_calls:
                continue
            results = [
                Content(
                    function_result=FunctionResult(id=call.id, name=call.name, result=await call_tool(call)),
                )
                for call in message_calls
            ]
            messages.append(Message(role="tool", content=results, tools_state_id=message.tools_state_id))
        log.info("tool_round", round=round_number, calls=[call.name for call in calls])

    raise ToolLoopExceeded(f"the model did not answer within {MAX_TOOL_ROUNDS} tool rounds")
