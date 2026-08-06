"""OpenAI-compatible, non-streaming chat boundary."""

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, Header
from pydantic import TypeAdapter, ValidationError

from campaign import CampaignSession
from contracts.openai import ChatCompletionRequest, NonStreamingChatCompletionRequest
from proxy_server.dependencies import get_campaign_session, get_storyteller
from proxy_server.errors import TurnError
from proxy_server.services.conversation_turn_service import ConversationTurnService
from proxy_server.services.storyteller import StorytellerProtocol


router = APIRouter()
_REQUEST = TypeAdapter(ChatCompletionRequest)


@router.post("/v1/chat/completions")
def chat_completions(payload: Any = Body(...), x_idempotency_key: str | None = Header(default=None),
                     session: CampaignSession = Depends(get_campaign_session),
                     storyteller: StorytellerProtocol = Depends(get_storyteller)):
    request_id = uuid4().hex
    forbidden = {"campaign_id", "database_path", "db_path", "state_patches", "hidden_state",
                 "tools", "tool_choice", "functions", "function_call"}
    if isinstance(payload, dict) and forbidden.intersection(payload):
        raise TurnError("invalid_request", "The chat completion request contains unsupported fields.",
                        request_id, False, 422)
    try:
        parsed = _REQUEST.validate_python(payload)
    except ValidationError as error:
        raise TurnError("invalid_request", "The chat completion request is invalid.", request_id, False, 422) from error
    if not isinstance(parsed, NonStreamingChatCompletionRequest):
        raise TurnError("unsupported_streaming", "Streaming chat completions are not supported.", request_id, False, 400)
    return ConversationTurnService(session, storyteller).complete(parsed, x_idempotency_key)
