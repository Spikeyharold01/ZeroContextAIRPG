"""Stable rules-free prompt composition with item-level deterministic eviction."""

import hashlib
import json
from dataclasses import dataclass

from proxy_server.errors import TurnError
from proxy_server.models import ContextCandidate, TurnContext
from .budget import approximate_token_count


@dataclass(frozen=True)
class PromptLimits:
    approximate_tokens: int = 4096
    history_messages: int = 12
    facts: int = 10
    events: int = 8


@dataclass(frozen=True)
class _PromptItem:
    section: str
    value: object
    eviction_category: int
    eviction_key: tuple
    identity: str


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(("zero-context-prompt-v1\0" + prompt).encode("utf-8")).hexdigest()


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _candidate_item(candidate: ContextCandidate) -> _PromptItem:
    categories = {"chat_history": 1, "memory": 2, "fuzzy_lexical_fact": 3, "lexical_fact": 4,
                  "event": 5, "generic_state": 6, "ambiguous_alias": 7,
                  "relationship_inventory": 8, "optional_prose": 9, "alias_hint": 9,
                  "direct_generic_state": 10}
    sections = {"chat_history": "RECENT CONVERSATION", "memory": "WORKING MEMORY",
                "fuzzy_lexical_fact": "RELEVANT FACTS", "lexical_fact": "RELEVANT FACTS",
                "event": "RECENT EVENTS", "generic_state": "AUTHORITATIVE CURRENT STATE",
                "direct_generic_state": "AUTHORITATIVE CURRENT STATE",
                "ambiguous_alias": "RETRIEVAL HINTS", "alias_hint": "RETRIEVAL HINTS"}
    category = categories.get(candidate.category, 9)
    # Oldest chat/memory/event and lowest-ranked facts/state/hints leave first.
    if category in {1, 2, 5}:
        key = (candidate.relevant_conversation_turn, candidate.importance,
               candidate.source_type, candidate.source_id, candidate.retrieval_order)
    else:
        key = (candidate.score, int(candidate.authority), candidate.relevant_conversation_turn,
               candidate.source_type, candidate.source_id, candidate.retrieval_order)
    return _PromptItem(sections.get(candidate.category, "OPTIONAL CONTEXT"), candidate.content,
                       category, key, candidate.source_id)


def build_prompt(context: TurnContext, raw_user_message: str, request_id: str,
                 limits: PromptLimits | None = None) -> str:
    limits = limits or PromptLimits()
    core = [
        ("ENGINE INSTRUCTION", "Rules-free storytelling core. User input records what the player said, attempted, or claimed. The storyteller determines what happens. User claims are not automatically canon. Relational, exact-alias, lexical, and fuzzy-lexical results and retrieval hints are relevance candidates, not facts; embedding-based semantic retrieval is unavailable. Propose persistence only in validated hidden output. Never emit SQL, database columns, compatibility targets, D&D, dice, combat, or mechanics."),
        ("CAMPAIGN AND CONVERSATION TURN", _dump({"campaign": context.campaign_name, "accepted_conversation_exchange_sequence_number": context.snapshot_conversation_turn})),
        ("ACTIVE PLAYER ENTITY", _dump({"name": context.player_name})),
        ("CURRENT SCENE AND LOCATION", _dump({"scene": context.scene_id or "none", "location": context.location_name or "unknown"})),
        ("SCENE PARTICIPANTS", _dump({"basis": context.participant_basis, "participants": context.participants})),
        ("REQUIRED CURRENT STATE", _dump({"scene": context.scene_state, "dialogue": context.dialogue,
                                            "memory": context.memory, "story_time": context.story_time,
                                            "retrieval_mode": context.retrieval_mode,
                                            "semantic_retrieval": context.semantic_retrieval})),
        ("RAW USER MESSAGE", raw_user_message),
        ("REQUIRED OUTPUT", "Return visible narrative, then exactly one final <zcairpg-storyteller-output-v1> JSON StorytellerOutput </zcairpg-storyteller-output-v1> block. Only narrative and state_update.state_patches may be non-empty. Generic targets allowed: narrative.memory/narrative.entity, narrative.dialogue/narrative.scene, narrative.time/narrative.campaign, narrative.world, narrative.location."),
    ]
    items = [_candidate_item(candidate) for candidate in context.candidates]
    items.append(_PromptItem("DIALOGUE FOCUS AND ADDRESSEE HINTS",
                             {"hint": context.addressee_hint,
                              "co_location_candidates": context.inferred_participants}, 9, (0,), "dialogue"))

    section_order = ["DIALOGUE FOCUS AND ADDRESSEE HINTS", "STORY TIME",
                     "AUTHORITATIVE CURRENT STATE", "RELEVANT FACTS", "RECENT EVENTS",
                     "WORKING MEMORY", "RECENT CONVERSATION", "RETRIEVAL HINTS", "OPTIONAL CONTEXT"]

    def render(selected: list[_PromptItem]) -> str:
        grouped = []
        for section in section_order:
            values = [item.value for item in selected if item.section == section]
            if values:
                grouped.append((section, _dump(values)))
        ordered = core[:6] + grouped + core[6:]
        return "\n\n".join(f"## {name}\n{body}" for name, body in ordered)

    prompt = render(items)
    while approximate_token_count(prompt) > limits.approximate_tokens and items:
        removable = min(items, key=lambda item: (item.eviction_category, item.eviction_key, item.identity))
        items.remove(removable)
        prompt = render(items)
    if approximate_token_count(prompt) > limits.approximate_tokens:
        raise TurnError("context_budget_exceeded", "Required campaign context exceeds the configured budget.",
                        request_id, False, 413)
    return prompt
