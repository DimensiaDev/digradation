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

import os
import uuid
from datetime import UTC, datetime, timedelta

from openai import AsyncOpenAI, AuthenticationError
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
    Tool,
    ToolCall,
    Usage,
)

#: Default model on the school hub. The hub also exposes
#: ``ai-sage/GigaChat3.5-432B-A28B-Reasoning`` and
#: ``ai-sage/Giga-Embeddings-instruct-3B`` (embeddings, not chat).
DEFAULT_OPENAI_MODEL = "GigaChat/GigaChat-3-Pro"


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
        self._client = AsyncOpenAI(api_key=api_key, base_url=url)

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
        if message.role == "tool":
            return {
                "role": "tool",
                "content": cls._text(message.content),
                "tool_call_id": message.tool_call_id or "",
            }
        parts = cls._content_parts(message.content)
        return {"role": message.role, "content": parts if parts is not None else cls._text(message.content)}

    @staticmethod
    def _tools_payload(tools: list[Tool]) -> list[dict]:
        return [{"type": tool.type, "function": tool.function.model_dump(exclude_none=True)} for tool in tools]

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

    @classmethod
    def _map_completion(cls, completion: ChatCompletion) -> GenerationResponse:
        choice = completion.choices[0]
        message = choice.message
        content = [Content(text=message.content)] if message.content else []
        tool_calls = [
            ToolCall(
                id=call.id,
                function=FunctionCall(name=call.function.name, arguments=call.function.arguments),
            )
            for call in message.tool_calls or []
            if call.type == "function"
        ]

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
        if self.tools:
            kwargs["tools"] = self._tools_payload(self.tools)
            kwargs["tool_choice"] = "auto"

        try:
            completion = await self._client.chat.completions.create(**kwargs)
        except AuthenticationError as e:
            log.error("auth_token_refresh_failed", exc=e)
            raise AuthorizationError() from e
        except Exception as e:
            log.error("completion_failure", exc=e)
            return None

        log.debug("completion_response", data=completion)
        return self._map_completion(completion)
