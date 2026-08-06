"""Internal immutable models for context assembly."""

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class AuthorityLevel(IntEnum):
    RETRIEVAL_HINT = 1
    CHAT_HISTORY = 2
    COMPATIBILITY_READ_ONLY = 3
    GENERIC_MEMORY = 4
    EVENT_HISTORY = 5
    ACTIVE_FACT = 6
    RELATIONAL_TOPOLOGY = 7
    LEGACY_AUTHORITATIVE = 8
    GENERIC_AUTHORITATIVE = 9


@dataclass(frozen=True)
class ContextCandidate:
    source_type: str
    source_id: str
    authority: AuthorityLevel
    subject_type: str | None
    subject_id: str | None
    content: Any
    category: str
    required_core: bool = False
    direct_entity_relevance: float = 0.0
    current_scene_relevance: float = 0.0
    current_location_relevance: float = 0.0
    exact_alias_match: float = 0.0
    fuzzy_lexical_score: float = 0.0
    # Reserved for Stage 2C embedding-based retrieval. Stage 2B never populates it.
    semantic_score: float | None = None
    lexical_match_type: str | None = None
    importance: float = 0.0
    relevant_conversation_turn: int = 0
    active_status: str = "active"
    retrieval_order: int = 0

    @property
    def score(self) -> float:
        active = 1.0 if self.active_status == "active" else -1.0
        return (self.direct_entity_relevance * 4 + self.current_scene_relevance * 3
                + self.current_location_relevance * 2 + self.exact_alias_match * 3
                + self.fuzzy_lexical_score * 2 + self.importance + active)

    def rank_key(self) -> tuple:
        return (-int(self.authority), -self.score, -self.relevant_conversation_turn,
                self.source_type, self.source_id, self.retrieval_order)


@dataclass(frozen=True)
class AliasMatch:
    alias: str
    subject_type: str
    subject_id: str
    canonical: bool
    in_scene: bool = False
    at_location: bool = False
    ambiguous: bool = False


@dataclass
class TurnContext:
    campaign_id: str
    campaign_name: str
    snapshot_conversation_turn: int
    player_id: str
    player_name: str
    location_id: str | None
    location_name: str | None
    scene_id: str | None
    participants: list[dict[str, str]] = field(default_factory=list)
    participant_basis: str = "unknown"
    inferred_participants: list[dict[str, str]] = field(default_factory=list)
    alias_matches: list[AliasMatch] = field(default_factory=list)
    addressee_hint: dict[str, Any] = field(default_factory=dict)
    dialogue: dict[str, Any] | None = None
    scene_state: dict[str, Any] | None = None
    story_time: dict[str, Any] | None = None
    memory: dict[str, Any] | None = None
    state: list[dict[str, Any]] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    history: list[dict[str, str]] = field(default_factory=list)
    candidates: list[ContextCandidate] = field(default_factory=list)
    lexical_query: str = ""
    retrieval_mode: str = "relational_lexical"
    semantic_retrieval: str = "unavailable"
    revisions: dict[tuple[str, str, str], int] = field(default_factory=dict)


@dataclass(frozen=True)
class RequestReservation:
    """Capability proving ownership of one request-idempotency attempt."""

    request_row_id: str
    last_request_id: str
    attempt_number: int


@dataclass(frozen=True)
class ReservationOutcome:
    reservation: RequestReservation | None = None
    replay_json: str | None = None
