"""OpenAI-compatible provider, used against any chat-completions endpoint.

Selected when ``SBER_API_SCOPE=openai``. The token travels in ``SBER_API_KEY``
and the endpoint comes from ``OPENAI_URL``, which is mandatory: there is no
implicit public endpoint. The model defaults to the GigaChat model the school
hub exposes and can be overridden with ``OPENAI_MODEL``.

This provider advertises tools on every request, and the calls the model asks
for come back on ``GenerationResponse.tool_calls``. Nothing executes them yet:
the caller decides whether to answer the call or run it. The GigaChat client
implements the same contract on its own wire format.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from openai import AsyncOpenAI, AuthenticationError, BadRequestError
from openai.types import CompletionUsage
from openai.types.chat import ChatCompletion

from app.providers.client import AIClient, AuthorizationError
from app.providers.models import (
    Content,
    FunctionCall,
    GenerationResponse,
    InputTokenDetails,
    Message,
    ResponseFormat,
    Role,
    Tool,
    ToolCall,
    Usage,
)
from app.providers.strict_schema import strict_schema

#: Default model on the school hub. The hub also exposes
#: ``ai-sage/GigaChat3.5-432B-A28B-Reasoning`` and
#: ``ai-sage/Giga-Embeddings-instruct-3B`` (embeddings, not chat).
DEFAULT_OPENAI_MODEL = "GigaChat/GigaChat-3-Pro"

#: The hosts a strict deployment may talk to. Cloud.ru is the endpoint the
#: criteria name; the school hub is what the demo uses today. Anything else is
#: refused when ``STRICT_PROVIDER`` is on, so a foreign vendor cannot be
#: reached by setting ``OPENAI_URL`` alone.
ALLOWED_LLM_HOSTS_DEFAULT: tuple[str, ...] = (
    "foundation-models.api.cloud.ru",
    "hub.gigaschool.ru",
)


def _strict() -> bool:
    return os.getenv("STRICT_PROVIDER", "").strip().lower() in {"1", "true", "yes", "on"}


def _strict_tools() -> bool:
    """Whether tool arguments are decoded under a strict schema.

    On unless the deployment turns it off: the hub accepts ``strict`` and
    constrains generation to the schema, which is what stops a stage from
    spending a tool round on arguments pydantic has to refuse. An endpoint that
    rejects the flag is detected at the first completion and remembered, so the
    switch is a fallback rather than a requirement.
    """
    return os.getenv("OPENAI_STRICT_TOOLS", "").strip().lower() not in {"0", "false", "no", "off"}


def _refused_strict(error: BadRequestError) -> bool:
    """Whether a 400 is about the strict schema rather than the request itself.

    The body is not part of the SDK's message in every version, so both are read
    and the check stays case-insensitive. A 400 that names neither ``strict``
    nor the schema is a real request error and is not retried.
    """
    text = str(error).lower()
    body = getattr(getattr(error, "response", None), "text", "")
    if isinstance(body, str):
        text = f"{text} {body.lower()}"
    return "strict" in text or "schema" in text


def _allowed_hosts() -> tuple[str, ...]:
    """The hosts the deployment accepts, from ``ALLOWED_LLM_HOSTS`` or the defaults."""
    raw = os.getenv("ALLOWED_LLM_HOSTS", "").strip()
    if not raw:
        return ALLOWED_LLM_HOSTS_DEFAULT
    return tuple(host.strip().lower() for host in raw.split(",") if host.strip())


def _reject_foreign_host(url: str) -> None:
    """Refuse an endpoint a strict deployment has not approved.

    A no-op unless ``STRICT_PROVIDER`` is on: a deployment that is not strict
    talks to whatever ``OPENAI_URL`` names, which is what makes the school hub
    and an operator's own proxy usable without changing the code.
    """
    if not _strict():
        return
    host = (urlparse(url).hostname or "").lower()
    allowed = _allowed_hosts()
    if host not in allowed:
        raise ValueError(f"LLM host {host!r} is not in ALLOWED_LLM_HOSTS; " f"allowed: {', '.join(allowed)}")


#: The marker GigaChat writes when it answers with a function call as text.
#: The hub relays it verbatim in ``content``, with no structured ``tool_calls``
#: beside it, so a call the model made would otherwise read as prose and the
#: stage would fail with "the model saved no block".
INLINE_CALL = "<|function_call|>"


class OpenAIProvider(AIClient):
    _client: AsyncOpenAI

    def __init__(self, api_key: str, scope: str, tools: list[Tool] | None = None):
        super().__init__(api_key, scope, tools)
        # The SDK would otherwise silently fall back to its public endpoint. This
        # deployment must always talk to an explicitly configured hub, so an
        # unset or blank URL is a configuration error rather than a default.
        url = os.getenv("OPENAI_URL", "").strip()
        if not url:
            raise ValueError("OPENAI_URL must be set when SBER_API_SCOPE=openai")
        _reject_foreign_host(url)
        self._client = AsyncOpenAI(api_key=api_key, base_url=url)
        #: Cleared for the life of this client if the endpoint refuses a strict
        #: schema, so the next request does not spend a round on it again.
        self._strict_tools = _strict_tools()

    def _model(self) -> str:
        return os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)

    async def auth(self):
        # The key authenticates every request; there is no token endpoint and no
        # refresh. A far-future expiry keeps the base ``ensure_fresh_token`` a
        # no-op if some future caller routes through it.
        self.token = self.api_key
        self.token_expires_at = datetime.now(UTC) + timedelta(days=365 * 100)
        self._log.info("openai_auth", scope=self.scope)

    async def close(self):
        await self._client.close()

    async def upload(
        self,
        filename: str,
        data: bytes,
        x_client_id: uuid.UUID,
        x_session_id: uuid.UUID,
        content_type: str,
    ) -> str:
        log = self._log.bind(
            filename=filename,
            x_client_id=str(x_client_id),
            x_session_id=str(x_session_id),
        )
        try:
            result = await self._client.files.create(
                file=(filename, data, content_type),
                purpose="assistants",
            )
        except Exception as e:
            log.error("upload_failure", exc=e)
            raise RuntimeError("upload_failure") from e
        log.debug("upload_response", file_id=result.id)
        return result.id

    @staticmethod
    def _text(contents: list[Content]) -> str:
        return "\n".join(part.text for part in contents if part.text)

    @classmethod
    def _content_parts(cls, contents: list[Content]) -> list[dict] | None:
        """Build multimodal parts when a message carries files or inline data.

        A text-only message keeps the plain string form, which every endpoint
        accepts. A file id becomes a ``file`` part, the shape OpenAI-compatible
        servers use for uploaded documents; inline data becomes a ``data:`` URL.
        """
        if not any(part.files or part.inline_data for part in contents):
            return None

        parts: list[dict] = []
        for part in contents:
            if part.text:
                parts.append({"type": "text", "text": part.text})
            parts.extend({"type": "file", "file": {"file_id": file.id}} for file in part.files)
            parts.extend(
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}
                for mime, data in (part.inline_data or {}).items()
            )
        return parts

    @classmethod
    def _message_to_openai(cls, message: Message) -> dict:
        if message.role == Role.TOOL:
            return {
                "role": "tool",
                "content": cls._text(message.content),
                "tool_call_id": message.tool_call_id or "",
            }
        if message.role == Role.ASSISTANT and message.tool_calls:
            return {
                "role": "assistant",
                "content": cls._text(message.content) or None,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.function.name, "arguments": call.function.arguments},
                    }
                    for call in message.tool_calls
                ],
            }
        parts = cls._content_parts(message.content)
        return {"role": message.role, "content": parts if parts is not None else cls._text(message.content)}

    @staticmethod
    def _tools_payload(tools: list[Tool], *, strict: bool | None = None) -> list[dict]:
        """The tools as the wire takes them.

        In strict mode the schema is closed and every property is required, so
        the decoder cannot omit a field the block needs or invent one it does
        not have. The shared tool definitions stay untouched: the flag is a
        property of the endpoint, not of the tool, and the GigaChat v2 client
        refuses a strict tool outright.
        """
        close = _strict_tools() if strict is None else strict
        payload: list[dict] = []
        for tool in tools:
            function = tool.function.model_dump(exclude_none=True)
            parameters = function.get("parameters")
            if close and parameters is not None:
                function["strict"] = True
                function["parameters"] = strict_schema(parameters)
            payload.append({"type": tool.type, "function": function})
        return payload

    @staticmethod
    def _response_format(response_format: ResponseFormat) -> dict | None:
        if response_format.type != ResponseFormat.TYPE_JSON_SCHEMA:
            return None
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "response",
                "schema": response_format.json_schema,
                "strict": bool(response_format.strict),
            },
        }

    @staticmethod
    def _map_usage(usage: CompletionUsage | None) -> Usage:
        if usage is None:
            return Usage(
                input_tokens=0,
                input_tokens_details=InputTokenDetails(prompt_tokens=0, cached_tokens=0),
                output_tokens=0,
                total_tokens=0,
            )

        details = usage.prompt_tokens_details
        return Usage(
            input_tokens=usage.prompt_tokens,
            input_tokens_details=InputTokenDetails(
                prompt_tokens=usage.prompt_tokens,
                cached_tokens=details.cached_tokens if details and details.cached_tokens else 0,
            ),
            output_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
        )

    @staticmethod
    def _inline_call(payload: object) -> ToolCall | None:
        """One call out of the JSON the model wrote after the marker."""
        if not isinstance(payload, dict):
            return None
        name = payload.get("name")
        if not isinstance(name, str) or not name:
            return None
        arguments = payload.get("arguments", {})
        raw = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
        return ToolCall(id=f"call_{uuid.uuid4().hex}", function=FunctionCall(name=name, arguments=raw))

    @classmethod
    def _inline_calls(cls, text: str) -> tuple[list[ToolCall], str]:
        """Calls the endpoint wrote into the text, and the prose before them.

        GigaChat's native answer is the marker followed by a JSON object. The
        decoder takes the first complete value and leaves the rest, so a marker
        whose payload runs into the next token still yields the call. What the
        model wrote before the first marker is kept as its prose. Everything
        from the marker on is dropped, even when no call can be read out of it:
        the marker is a control token, and a request that carries one is
        refused, so it must never be sent back to the hub.
        """
        if INLINE_CALL not in text:
            return [], text
        decoder = json.JSONDecoder()
        calls: list[ToolCall] = []
        prose = ""
        cursor = 0
        while (index := text.find(INLINE_CALL, cursor)) >= 0:
            if not calls:
                prose = text[:index]
            start = index + len(INLINE_CALL)
            while start < len(text) and text[start].isspace():
                start += 1
            try:
                payload, cursor = decoder.raw_decode(text, start)
            except json.JSONDecodeError:
                break
            for item in payload if isinstance(payload, list) else [payload]:
                call = cls._inline_call(item)
                if call is not None:
                    calls.append(call)
        return calls, prose.strip()

    @classmethod
    def _map_completion(cls, completion: ChatCompletion) -> GenerationResponse:
        choice = completion.choices[0]
        message = choice.message
        text = message.content or ""
        tool_calls = [
            ToolCall(
                id=call.id,
                function=FunctionCall(name=call.function.name, arguments=call.function.arguments),
            )
            for call in message.tool_calls or []
            if call.type == "function"
        ]
        if INLINE_CALL in text:
            # The endpoint answered with the call as text, or left the marker
            # in the prose. Either way the token is not an answer and must not
            # reach the next request: the hub refuses one that carries it.
            inline, text = cls._inline_calls(text)
            tool_calls = tool_calls or inline
        content = [Content(text=text)] if text else []

        return GenerationResponse(
            messages=[Message(role="assistant", content=content)],
            model=completion.model,
            thread_id=completion.id,
            created_at=datetime.fromtimestamp(completion.created, tz=UTC),
            finish_reason=choice.finish_reason,
            usage=cls._map_usage(completion.usage),
            tool_calls=tool_calls,
        )

    async def generate(
        self,
        message_history: list[Message],
        response_format: ResponseFormat,
        x_client_id: uuid.UUID,
        x_session_id: uuid.UUID,
        *,
        tools: list[Tool] | None = None,
    ) -> GenerationResponse | None:
        x_request_id = str(uuid.uuid4())
        log = self._log.bind(
            x_request_id=x_request_id,
            x_client_id=x_client_id,
            x_session_id=x_session_id,
        )
        kwargs: dict = {
            "model": self._model(),
            "messages": [self._message_to_openai(m) for m in message_history],
        }
        fmt = self._response_format(response_format)
        if fmt is not None:
            kwargs["response_format"] = fmt
        active_tools = self.tools if tools is None else tools
        strict_used = bool(active_tools) and self._strict_tools
        if active_tools:
            kwargs["tools"] = self._tools_payload(active_tools, strict=self._strict_tools)
            kwargs["tool_choice"] = "auto"

        try:
            completion = await self._client.chat.completions.create(**kwargs)
        except AuthenticationError as e:
            log.error("auth_token_refresh_failed", exc=e)
            raise AuthorizationError() from e
        except BadRequestError as e:
            if not (strict_used and _refused_strict(e)):
                log.error("completion_failure", exc=e)
                return None
            # The schema is the only part of the request that changes shape, so
            # the request is retried once without it rather than failing a stage
            # on a flag. The client remembers the refusal for its lifetime.
            log.warning("strict_tools_rejected", exc=e)
            self._strict_tools = False
            kwargs["tools"] = self._tools_payload(active_tools, strict=False)
            try:
                completion = await self._client.chat.completions.create(**kwargs)
            except Exception as retry_error:
                log.error("completion_failure", exc=retry_error)
                return None
        except Exception as e:
            log.error("completion_failure", exc=e)
            return None

        log.debug("completion_response", data=completion)
        return self._map_completion(completion)
