"""Contracts separating untrusted ingestion from trusted turn resolution."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, JsonValue, model_validator

from .common import InternalStrictModel, PositiveCharacterId, PositiveLocationId, RegistryIdentifier, UnitInterval
from .openai import StringChatMessage


class SamplingParameters(InternalStrictModel):
    temperature: Annotated[float, Field(ge=0.0, le=2.0)] = 0.85
    top_p: UnitInterval = 0.92
    top_k: Annotated[int, Field(ge=0, le=1000)] = 40
    max_tokens: Annotated[int, Field(ge=1, le=32768)] = 600
    presence_penalty: Annotated[float, Field(ge=-2.0, le=2.0)] = 0.0
    frequency_penalty: Annotated[float, Field(ge=-2.0, le=2.0)] = 0.0


class ParsedIngestedContext(InternalStrictModel):
    """Untrusted result of request validation and marker extraction."""

    request_id: UUID
    user_message: Annotated[str, Field(min_length=1)]
    raw_chat_history: list[StringChatMessage] = Field(default_factory=list)
    system_prompt: str = ""
    character_card_text: Annotated[str, Field(min_length=1)]
    parsed_character_name: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    scenario: str = ""
    examples: str = ""
    user_character: str = ""
    is_first_message: bool
    raw_system_prompts: list[StringChatMessage] = Field(default_factory=list)
    sampling: SamplingParameters = Field(default_factory=SamplingParameters)
    character_id_hint: PositiveCharacterId | None = None
    raw_payload: dict[str, JsonValue] = Field(default_factory=dict, exclude=True)

    @model_validator(mode="after")
    def current_message_is_not_duplicated(self):
        if (
            self.raw_chat_history
            and self.raw_chat_history[-1].role == "user"
            and self.raw_chat_history[-1].content == self.user_message
        ):
            raise ValueError("raw_chat_history must not duplicate user_message")
        return self


class RecentExchange(InternalStrictModel):
    user: Annotated[str, Field(min_length=1)]
    assistant: Annotated[str, Field(min_length=1)] | None = None


class ResolvedTurnContext(InternalStrictModel):
    """Trusted active-character context constructed after SQLite resolution."""

    request_id: UUID
    parsed: ParsedIngestedContext
    character_id: PositiveCharacterId
    character_name: Annotated[str, Field(min_length=1, max_length=256)]
    entity_kind: RegistryIdentifier
    control_type: RegistryIdentifier
    character_is_active: Literal[True]
    current_location_id: PositiveLocationId | None = None
    history_exchange_limit: Annotated[int, Field(ge=1, le=50)]
    recent_exchanges: list[RecentExchange] = Field(default_factory=list)

    @model_validator(mode="after")
    def history_respects_limit(self):
        if len(self.recent_exchanges) > self.history_exchange_limit:
            raise ValueError("recent_exchanges exceeds history_exchange_limit")
        return self
