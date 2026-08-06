"""Application-owned dependency providers."""

from fastapi import Request

from campaign import CampaignSession
from .services.storyteller import DeterministicMockStoryteller, StorytellerProtocol


def get_campaign_session(request: Request) -> CampaignSession:
    session = getattr(request.app.state, "campaign_session", None)
    if session is None:
        raise RuntimeError("no campaign session configured")
    return session


def get_storyteller(request: Request) -> StorytellerProtocol:
    return request.app.state.storyteller


def default_storyteller() -> StorytellerProtocol:
    return DeterministicMockStoryteller()
