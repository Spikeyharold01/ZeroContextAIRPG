"""Strict storyteller narrative and hidden state patch contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .common import (
    BoundedJsonPatch, EventType, FactSourceType, FactType, InternalStrictModel,
    PositiveCharacterId, PositiveLocationId, UnitInterval,
)


class EmotionalAxisDeltas(InternalStrictModel):
    trust: Annotated[int, Field(ge=-20, le=20)] | None = None
    fear: Annotated[int, Field(ge=-20, le=20)] | None = None
    arousal: Annotated[int, Field(ge=-20, le=20)] | None = None
    tension: Annotated[int, Field(ge=-20, le=20)] | None = None
    intimacy: Annotated[int, Field(ge=-20, le=20)] | None = None

    @model_validator(mode="after")
    def contains_change(self):
        if not any(value not in (None, 0) for value in self.__dict__.values()):
            raise ValueError("at least one nonzero emotional delta is required")
        return self


class EmotionalShift(InternalStrictModel):
    character_id: PositiveCharacterId
    deltas: EmotionalAxisDeltas | None = None
    mood: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    description: Annotated[str, Field(min_length=1, max_length=500)]
    confidence: UnitInterval

    @model_validator(mode="after")
    def has_effect(self):
        if self.deltas is None and self.mood is None:
            raise ValueError("an emotional shift needs deltas or a mood")
        return self


class AppliedEmotionalAxisChange(InternalStrictModel):
    axis: Literal["trust", "fear", "arousal", "tension", "intimacy"]
    value_before: Annotated[int, Field(ge=0, le=100)]
    proposed_delta: Annotated[int, Field(ge=-20, le=20)]
    proposed_result: int
    applied_delta: Annotated[int, Field(ge=-20, le=20)]
    value_after: Annotated[int, Field(ge=0, le=100)]
    boundary_adjusted: bool

    @model_validator(mode="after")
    def preserve_proposed_and_applied_math(self):
        if self.proposed_result != self.value_before + self.proposed_delta:
            raise ValueError("proposed_result is inconsistent")
        if self.applied_delta != self.value_after - self.value_before:
            raise ValueError("applied_delta is inconsistent")
        bounded_result = min(100, max(0, self.proposed_result))
        if self.value_after != bounded_result:
            raise ValueError("value_after is not the bounded proposed result")
        adjusted = self.proposed_result != self.value_after
        if self.boundary_adjusted != adjusted:
            raise ValueError("boundary_adjusted is inconsistent")
        return self


class ConversationalFactCandidate(InternalStrictModel):
    character_id: PositiveCharacterId
    text: Annotated[str, Field(min_length=1, max_length=2000)]
    references: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=128)]],
        Field(default_factory=list, max_length=32),
    ]
    fact_type: FactType = "world_fact"
    source_type: FactSourceType = "narrative"
    source_character_id: PositiveCharacterId | None = None
    confidence: UnitInterval = 0.9
    importance: UnitInterval = 0.5
    expires_after_turns: Annotated[int, Field(ge=1, le=100000)] | None = None

    @model_validator(mode="after")
    def validate_provenance_and_references(self):
        if self.fact_type in {"belief_fact", "rumor_fact"}:
            if self.source_character_id is None:
                raise ValueError("belief and rumor facts require source_character_id")
        elif self.source_character_id is not None:
            raise ValueError("world facts cannot claim a source character")
        normalized = [reference.casefold() for reference in self.references]
        if len(normalized) != len(set(normalized)):
            raise ValueError("references must be unique case-insensitively")
        return self


class MajorEvent(InternalStrictModel):
    text: Annotated[str, Field(min_length=1, max_length=2000)]
    event_type: EventType
    character_id: PositiveCharacterId | None = None
    importance: UnitInterval = 0.7
    dedupe_key: Annotated[
        str, Field(min_length=1, max_length=256, pattern=r"^[a-z0-9:_-]+$")
    ] | None = None


class PlotStateUpdate(InternalStrictModel):
    character_id: PositiveCharacterId
    current_goal: Annotated[str, Field(min_length=1, max_length=1000)] | None = None
    hidden_goal: Annotated[str, Field(min_length=1, max_length=1000)] | None = None
    immediate_beat: Annotated[str, Field(min_length=1, max_length=1000)] | None = None
    long_arc: Annotated[str, Field(min_length=1, max_length=2000)] | None = None
    tension: UnitInterval | None = None
    plot_state_patch: BoundedJsonPatch = Field(default_factory=dict)

    @model_validator(mode="after")
    def is_not_empty(self):
        values = (
            self.current_goal, self.hidden_goal, self.immediate_beat,
            self.long_arc, self.tension,
        )
        if all(value is None for value in values) and not self.plot_state_patch:
            raise ValueError("plot-state update cannot be empty")
        return self


class WorldStatePatch(InternalStrictModel):
    war_active: bool | None = None
    bridge_destroyed: bool | None = None
    festival_active: bool | None = None
    moon_phase: Literal[
        "new", "waxing_crescent", "first_quarter", "waxing_gibbous", "full",
        "waning_gibbous", "last_quarter", "waning_crescent",
    ] | None = None
    weather: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    additional_state_patch: BoundedJsonPatch = Field(default_factory=dict)

    @model_validator(mode="after")
    def is_not_empty(self):
        values = (
            self.war_active, self.bridge_destroyed, self.festival_active,
            self.moon_phase, self.weather,
        )
        if all(value is None for value in values) and not self.additional_state_patch:
            raise ValueError("world-state patch cannot be empty")
        return self


class SceneObjectPatch(InternalStrictModel):
    object_name: Annotated[str, Field(min_length=1, max_length=128)]
    object_state: Annotated[str, Field(min_length=1, max_length=256)]


class SceneGraphPatch(InternalStrictModel):
    location_id: PositiveLocationId
    upsert_objects: list[SceneObjectPatch] = Field(default_factory=list, max_length=100)
    remove_objects: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(
        default_factory=list, max_length=100
    )
    add_npc_ids: list[PositiveCharacterId] = Field(default_factory=list, max_length=100)
    remove_npc_ids: list[PositiveCharacterId] = Field(default_factory=list, max_length=100)
    visibility: Literal["clear", "dim", "dark", "obscured"] | None = None

    @model_validator(mode="after")
    def validate_patch(self):
        if not any((
            self.upsert_objects, self.remove_objects, self.add_npc_ids,
            self.remove_npc_ids, self.visibility is not None,
        )):
            raise ValueError("scene-graph patch cannot be empty")
        upserted = {item.object_name.casefold() for item in self.upsert_objects}
        removed = {item.casefold() for item in self.remove_objects}
        if upserted & removed:
            raise ValueError("an object cannot be upserted and removed")
        if set(self.add_npc_ids) & set(self.remove_npc_ids):
            raise ValueError("an NPC cannot be added and removed")
        return self


class StorytellerStateUpdate(InternalStrictModel):
    schema_version: Literal[1] = 1
    emotional_shifts: list[EmotionalShift] = Field(default_factory=list, max_length=50)
    conversational_facts: list[ConversationalFactCandidate] = Field(default_factory=list, max_length=100)
    major_events: list[MajorEvent] = Field(default_factory=list, max_length=25)
    plot_updates: list[PlotStateUpdate] = Field(default_factory=list, max_length=25)
    world_state: WorldStatePatch | None = None
    scene_graph: list[SceneGraphPatch] = Field(default_factory=list, max_length=25)

    @model_validator(mode="after")
    def prevent_duplicate_targets(self):
        plot_ids = [item.character_id for item in self.plot_updates]
        if len(plot_ids) != len(set(plot_ids)):
            raise ValueError("only one plot update per character is allowed")
        locations = [item.location_id for item in self.scene_graph]
        if len(locations) != len(set(locations)):
            raise ValueError("only one scene patch per location is allowed")
        return self


class StorytellerOutput(InternalStrictModel):
    """Complete generation; only narrative is user-visible."""

    narrative: Annotated[str, Field(min_length=1, max_length=20000)]
    state_update: StorytellerStateUpdate
