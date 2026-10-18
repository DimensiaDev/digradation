from datetime import UTC, datetime
from typing import ClassVar, Literal, Self

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


class Content(BaseModel):
    text: str
    inline_data: dict[str, str] | None = None
    files: list[FileSchema] = Field(default_factory=list)

    @field_validator("files", mode="before")
    @classmethod
    def none_to_empty_list(cls, v):
        return v if v is not None else []


class FunctionDefinition(BaseModel):
    """A callable the model may choose during generation.

    This is the request-side half of tool calling: it is passed to the provider
    at construction time and advertised to the model on every completion request.
    """

    name: str
    description: str | None = None
    parameters: dict | None = None
    strict: bool | None = None


class Tool(BaseModel):
    """A tool offered to the model. Only ``function`` tools exist today."""

    type: Literal["function"] = "function"
    function: FunctionDefinition


class FunctionCall(BaseModel):
    """What the model asked a tool to do. ``arguments`` is JSON, as a string."""

    name: str
    arguments: str


class ToolCall(BaseModel):
    """One tool invocation requested by the model."""

    id: str
    type: Literal["function"] = "function"
    function: FunctionCall


class Message(BaseModel):
    role: Literal["user", "system", "assistant", "tool"]
    content: list[Content]
    #: Set on a ``tool`` message to tie it back to the assistant's ``ToolCall``.
    tool_call_id: str | None = None

    @classmethod
    def from_chat_message(cls, obj: ChatMessage) -> Self:
        files = None
        if obj.files_str:
            files = [FileSchema(id=f.strip()) for f in obj.files_str.split(",")]
        return cls(content=[Content(text=obj.content, files=files)], role=obj.role)  # type: ignore


class HarnessStructuredOutput(BaseModel):
    chat_name: str | None = None
    message: str


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
    model_options: ModelOptions


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
        "tool_calls",
        "content_filter",
        "blacklist",
        "request_blacklist",
        "request_whitelist",
        "request_filter",
        "response_blacklist",
    ]
    usage: Usage
    #: Tools the model wants called before it can answer. A response may carry
    #: both text and tool calls, or tool calls with no text at all.
    tool_calls: list[ToolCall] = Field(default_factory=list)

    @field_validator("created_at", mode="before")
    @classmethod
    def parse_created_at(cls, v):
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v / 1000, tz=UTC)
        return v
