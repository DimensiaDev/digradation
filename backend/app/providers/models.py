import json
from datetime import UTC, datetime
from typing import Any, ClassVar, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.chat import ChatMessage


class AuthResponse(BaseModel):
    access_token: str
    expires_at: datetime

    @field_validator("expires_at", mode="before")
    @classmethod
    def parse_expires_at(cls, v):
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v / 1000, tz=UTC)
        return v


class FileSchema(BaseModel):
    id: str


class FunctionCall(BaseModel):
    """One function call requested by the model, as it arrives in a content part."""

    id: str | None = None
    name: str
    arguments: dict = Field(default_factory=dict)

    @field_validator("arguments", mode="before")
    @classmethod
    def parse_arguments(cls, v):
        """The v2 endpoint sends an object; an older one may send a JSON string."""
        if isinstance(v, str):
            return json.loads(v)
        return v


class FunctionResult(BaseModel):
    """The value of a function call, sent back as a `tool` message part."""

    id: str | None = None
    name: str
    result: Any


class FunctionSpecification(BaseModel):
    """Declaration of one function the model is allowed to call."""

    name: str
    description: str
    parameters: dict


class ToolFunctions(BaseModel):
    specifications: list[FunctionSpecification]


class Tool(BaseModel):
    """A tool available to the model; its functions are grouped under ``functions``."""

    functions: ToolFunctions


class ToolConfig(BaseModel):
    """How the model may pick a function: ``auto`` leaves the choice to it."""

    mode: Literal["auto", "none", "forced", "any"] = "auto"


class Content(BaseModel):
    """One part of a message: text, files, or a function call/result."""

    text: str | None = None
    inline_data: dict[str, str] | None = None
    files: list[FileSchema] = Field(default_factory=list)
    function_call: FunctionCall | None = None
    function_result: FunctionResult | None = None

    @field_validator("files", mode="before")
    @classmethod
    def none_to_empty_list(cls, v):
        return v if v is not None else []


class Message(BaseModel):
    """A chat message; ``tools_state_id`` ties function results to their call round."""

    role: Literal["user", "system", "assistant", "tool"]
    content: list[Content]
    tools_state_id: str | None = None

    @classmethod
    def from_chat_message(cls, obj: ChatMessage) -> Self:
        files = None
        if obj.files_str:
            files = [FileSchema(id=f.strip()) for f in obj.files_str.split(",")]
        return cls(content=[Content(text=obj.content, files=files)], role=obj.role)  # type: ignore


class ResponseFormat(BaseModel):
    TYPE_TEXT: ClassVar[Literal["text"]] = "text"
    TYPE_JSON_SCHEMA: ClassVar[Literal["json_schema"]] = "json_schema"

    type: Literal["json_schema", "text"]
    json_schema: dict | None = Field(None, alias="schema")
    strict: bool | None = None

    @model_validator(mode="after")
    def validate_schema(self):
        if self.type == "json_schema" and (self.json_schema is None or self.strict is None):
            raise ValueError("schema is required for json_schema messages")

        if self.type == "text" and (self.json_schema is not None or self.strict is not None):
            raise ValueError("schema must not be provided for text messages")

        return self


class ModelOptions(BaseModel):
    response_format: ResponseFormat


class GenerationRequest(BaseModel):
    model: str
    messages: list[Message]
    model_options: ModelOptions | None = None
    tools: list[Tool] | None = None
    tool_config: ToolConfig | None = None


class InputTokenDetails(BaseModel):
    prompt_tokens: int
    cached_tokens: int


class Usage(BaseModel):
    input_tokens: int
    input_tokens_details: InputTokenDetails
    output_tokens: int
    total_tokens: int


class GenerationResponse(BaseModel):
    messages: list[Message]
    model: str
    thread_id: str | None = None
    created_at: datetime
    finish_reason: Literal[
        "stop",
        "length",
        "function_call",
        "function_call_error",
        "blacklist",
        "request_blacklist",
        "request_whitelist",
        "request_filter",
        "response_blacklist",
    ]
    usage: Usage

    @field_validator("created_at", mode="before")
    @classmethod
    def parse_created_at(cls, v):
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v / 1000, tz=UTC)
        return v
