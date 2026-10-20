import json
import os
import ssl
import uuid
from contextlib import suppress
from pathlib import Path

from httpx import AsyncClient, Timeout

from app.providers.client import AIClient, AuthorizationError
from app.providers.models import (
    AuthResponse,
    FunctionCall,
    FunctionSpecification,
    FunctionsSpecifications,
    GenerationRequest,
    GenerationResponse,
    Message,
    ModelOptions,
    ResponseFormat,
    Tool,
    ToolCall,
    ToolConfig,
    ToolsFunctions,
)


class SberProvider(AIClient):
    def __init__(self, api_key: str, scope: str, tools: list[Tool] | None = None):
        if scope not in ("PERS", "B2B", "CORP"):
            raise ValueError("Scope must be PERS, B2B or CORP")
        super().__init__(api_key, scope, tools)
        self._reject_strict_tools(self.tools)

        self._client = AsyncClient(
            base_url="https://api.giga.chat/v2",
            verify=ssl.create_default_context(cafile=Path("res/gigachat-ca.cer")),
            timeout=Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0),
        )

    async def auth(self):
        rq_uid = str(uuid.uuid4())
        scope = "GIGACHAT_API_" + self.scope.upper()

        self._log.info("auth", rq_uid=rq_uid, scope=scope)
        response = await self._client.post(
            "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
            headers={
                "RqUID": rq_uid,
                "Authorization": f"Basic {self.api_key}",
            },
            data={"scope": scope},
        )
        if response.status_code != 200:
            self._log.error(
                "auth_failed",
                rq_uid=rq_uid,
                status_code=response.status_code,
                resp=response.json(),
            )
            raise AuthorizationError()

        data = AuthResponse.model_validate(response.json())
        self.token = data.access_token
        self.token_expires_at = data.expires_at
        self._client.headers["Authorization"] = f"Bearer {self.token}"
        self._log.info("auth_ok", rq_uid=rq_uid, scope=scope, until=self.token_expires_at)

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
        headers = {
            "X-Client-Id": str(x_client_id),
            "X-Session-Id": str(x_session_id),
            "X-Request-Id": x_request_id,
        }
        active_tools = self.tools if tools is None else tools
        tool_payload = self._tools_payload(active_tools)
        data = GenerationRequest(
            model=os.getenv("GIGACHAT_MODEL", "GigaChat-3-Ultra"),
            messages=message_history,
            model_options=ModelOptions(response_format=response_format),
            tools=tool_payload,
            # ``auto`` lets the model decide; GigaChat defaults to it, but being
            # explicit keeps a tool-less request from inheriting the mode.
            tool_config=ToolConfig(mode="auto") if tool_payload else None,
        )
        request_body = data.model_dump(exclude_unset=True, exclude_none=True, by_alias=True)
        # ``tool_calls`` and ``tool_call_id`` are the OpenAI half of the shared
        # history; v2 carries the same information in the content parts and
        # rejects unknown message keys.
        for message in request_body["messages"]:
            message.pop("tool_calls", None)
            message.pop("tool_call_id", None)
            # The shared history normalises ``arguments`` to a JSON string so
            # OpenAI can use it verbatim. GigaChat takes the object, which is the
            # shape its responses use, so restore it for the wire.
            for function_call in (
                message.get("function_call"),
                *(
                    part.get("function_call")
                    for part in message.get("content") or []
                    if part.get("function_call") is not None
                ),
            ):
                if function_call is None:
                    continue
                arguments = function_call.get("arguments")
                if isinstance(arguments, str):
                    with suppress(json.JSONDecodeError):
                        function_call["arguments"] = json.loads(arguments)

        r = await self._post(
            "/chat/completions",
            json=request_body,
            headers=headers,
        )
        if r.status_code != 200:
            log.error(
                "completion_failure",
                data=data.model_dump(exclude_none=True, by_alias=True),
                response=r.json(),
                status_code=r.status_code,
            )
            return None

        json_data = r.json()
        log.debug("completion_response", data=json_data)
        response = GenerationResponse.model_validate(json_data)
        # v2 may nest a requested call inside a content part or put it on the
        # message itself; the shared model surfaces both at the top level, as the
        # OpenAI client does. The SDK example prefers the message-level field and
        # falls back to the parts, so a response carrying both is not counted
        # twice.
        calls: list[ToolCall] = []
        for message in response.messages:
            function_call = message.function_call
            if function_call is not None:
                calls.append(
                    ToolCall(
                        id=function_call.id,
                        function=FunctionCall(name=function_call.name, arguments=function_call.arguments),
                    )
                )
                continue
            calls.extend(
                ToolCall(
                    id=part.function_call.id,
                    function=FunctionCall(name=part.function_call.name, arguments=part.function_call.arguments),
                )
                for part in message.content
                if part.function_call is not None
            )
        response.tool_calls = calls
        # The state id can arrive on the assistant message rather than the
        # envelope; the next request has to echo whichever one is present.
        if response.tools_state_id is None:
            response.tools_state_id = next(
                (message.tools_state_id for message in response.messages if message.tools_state_id is not None),
                None,
            )
        return response

    def _reject_strict_tools(self, tools: list[Tool]) -> None:
        """GigaChat has no strict-schema mode for tools, so refuse rather than drop it."""
        strict = [tool.function.name for tool in tools if tool.function.strict]
        if strict:
            raise ValueError(f"GigaChat does not support strict tools: {', '.join(strict)}")

    def _tools_payload(self, tools: list[Tool]) -> list[ToolsFunctions] | None:
        """Translate the provider's tools into the GigaChat v2 ``tools`` shape."""
        self._reject_strict_tools(tools)
        if not tools:
            return None
        specifications = [
            FunctionSpecification(
                name=tool.function.name,
                description=tool.function.description,
                parameters=tool.function.parameters or {},
            )
            for tool in tools
        ]
        return [ToolsFunctions(functions=FunctionsSpecifications(specifications=specifications))]

    async def upload(
        self,
        filename: str,
        data: bytes,
        x_client_id: uuid.UUID,
        x_session_id: uuid.UUID,
        content_type: str,
    ) -> str:
        x_request_id = str(uuid.uuid4())
        log = self._log.bind(
            x_request_id=x_request_id,
            x_client_id=x_client_id,
            x_session_id=x_session_id,
            filename=filename,
        )
        headers = {
            "X-Client-Id": str(x_client_id),
            "X-Session-Id": str(x_session_id),
            "X-Request-Id": x_request_id,
        }

        r = await self._post(
            "https://api.giga.chat/v1/files",
            headers=headers,
            data={"purpose": "general"},
            files={"file": (filename, data, content_type)},
        )
        if r.status_code != 200:
            log.error("upload_failure", reponse=r.json(), status_code=r.status_code)
            raise RuntimeError("upload_failure")

        json_data = r.json()
        log.debug("upload_response", data=json_data)
        return json_data["id"]
