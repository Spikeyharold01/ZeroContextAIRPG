"""Typed contracts for the supported OpenAI-compatible string-chat subset."""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import Field, model_validator

from .common import (
    InternalStrictModel,
    NonEmptyText,
    OpenAIMessageModel,
    OpenAIRequestModel,
    UnitInterval,
)


class StringChatMessage(OpenAIMessageModel):
    """A string-only system, user, or assistant message.

    Structured content, images, audio, tools, developer roles, function calls,
    and unknown message fields are deliberately unsupported and rejected.
    """

    role: Literal["system", "user", "assistant"]
    content: Annotated[str, Field(min_length=1)]
    name: Annotated[str, Field(min_length=1, max_length=128)] | None = None


class ChatCompletionRequestBase(OpenAIRequestModel):
    """Shared request fields for the deliberate OpenAI string-chat subset."""

    model: Annotated[str, Field(min_length=1, max_length=256)]
    messages: Annotated[list[StringChatMessage], Field(min_length=1, max_length=1000)]
    temperature: Annotated[float, Field(ge=0.0, le=2.0)] = 0.85
    top_p: UnitInterval = 0.92
    top_k: Annotated[int, Field(ge=0, le=1000)] = 40
    max_tokens: Annotated[int, Field(ge=1, le=32768)] = 600
    presence_penalty: Annotated[float, Field(ge=-2.0, le=2.0)] = 0.0
    frequency_penalty: Annotated[float, Field(ge=-2.0, le=2.0)] = 0.0
    stop: str | list[str] | None = None
    seed: int | None = None
    user: Annotated[str, Field(min_length=1, max_length=256)] | None = None

    @model_validator(mode="after")
    def validate_request(self):
        if not any(message.role == "user" for message in self.messages):
            raise ValueError("messages must contain at least one user message")
        if isinstance(self.stop, list):
            if not 1 <= len(self.stop) <= 4:
                raise ValueError("stop must contain between 1 and 4 strings")
            if any(not item or len(item) > 256 for item in self.stop):
                raise ValueError("each stop sequence must contain 1–256 characters")
        elif isinstance(self.stop, str) and not 1 <= len(self.stop) <= 256:
            raise ValueError("stop must contain 1–256 characters")
        return self


class NonStreamingChatCompletionRequest(ChatCompletionRequestBase):
    stream: Literal[False] = False


class StreamingChatCompletionRequest(ChatCompletionRequestBase):
    stream: Literal[True]


ChatCompletionRequest: TypeAlias = Annotated[
    NonStreamingChatCompletionRequest | StreamingChatCompletionRequest,
    Field(discriminator="stream"),
]


class AssistantResponseMessage(InternalStrictModel):
    role: Literal["assistant"] = "assistant"
    content: str


class CompletionChoice(InternalStrictModel):
    index: Annotated[int, Field(ge=0)]
    message: AssistantResponseMessage
    finish_reason: Literal["stop", "length", "content_filter"]


class TokenUsage(InternalStrictModel):
    prompt_tokens: Annotated[int, Field(ge=0)]
    completion_tokens: Annotated[int, Field(ge=0)]
    total_tokens: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def total_matches_components(self):
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError(
                "total_tokens must equal prompt_tokens + completion_tokens"
            )
        return self


class ChatCompletionResponse(InternalStrictModel):
    id: Annotated[str, Field(pattern=r"^chatcmpl-[A-Za-z0-9_-]+$")]
    object: Literal["chat.completion"] = "chat.completion"
    created: Annotated[int, Field(ge=0)]
    model: NonEmptyText
    choices: Annotated[list[CompletionChoice], Field(min_length=1)]
    usage: TokenUsage


class StreamDelta(InternalStrictModel):
    role: Literal["assistant"] | None = None
    content: str | None = None


class StreamChoice(InternalStrictModel):
    index: Annotated[int, Field(ge=0)]
    delta: StreamDelta
    finish_reason: Literal["stop", "length", "content_filter"] | None = None


class ChatCompletionChunk(InternalStrictModel):
    """One JSON payload for an SSE data frame; transport is out of scope."""

    id: Annotated[str, Field(pattern=r"^chatcmpl-[A-Za-z0-9_-]+$")]
    object: Literal["chat.completion.chunk"] = "chat.completion.chunk"
    created: Annotated[int, Field(ge=0)]
    model: NonEmptyText
    choices: Annotated[list[StreamChoice], Field(min_length=1)]
