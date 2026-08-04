"""Campaign-neutral storyteller narrative and hidden-state contracts."""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import Field, model_validator

from .common import (
    FactSourceType,
    FactType,
    InternalStrictModel,
    PositiveCharacterId,
    RegistryIdentifier,
    SubjectIdentifier,
    UnitInterval,
)
from .state import EntityReference, StatePatch


class EmotionalShift(InternalStrictModel):
    character_id: PositiveCharacterId
    affect_axis_definition_id: RegistryIdentifier
    proposed_delta: Annotated[float, Field(allow_inf_nan=False)]
    description: Annotated[str, Field(min_length=1, max_length=500)]
    confidence: UnitInterval


class AppliedEmotionalAxisChange(InternalStrictModel):
    affect_axis_definition_id: RegistryIdentifier
    value_before: Annotated[float, Field(allow_inf_nan=False)]
    proposed_delta: Annotated[float, Field(allow_inf_nan=False)]
    proposed_result: Annotated[float, Field(allow_inf_nan=False)]
    applied_delta: Annotated[float, Field(allow_inf_nan=False)]
    value_after: Annotated[float, Field(allow_inf_nan=False)]
    boundary_adjusted: bool

    @model_validator(mode="after")
    def preserve_proposed_and_applied_math(self):
        if self.proposed_result != self.value_before + self.proposed_delta:
            raise ValueError("proposed_result is inconsistent")
        if self.applied_delta != self.value_after - self.value_before:
            raise ValueError("applied_delta is inconsistent")
        if self.boundary_adjusted != (self.applied_delta != self.proposed_delta):
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
    event_type: RegistryIdentifier
    character_id: PositiveCharacterId | None = None
    importance: UnitInterval = 0.7
    dedupe_key: Annotated[
        str, Field(min_length=1, max_length=256, pattern=r"^[a-z0-9:_-]+$")
    ] | None = None


class SceneGraphOperationBase(InternalStrictModel):
    scene_id: SubjectIdentifier


class AddSceneEntity(SceneGraphOperationBase):
    op: Literal["add_entity"]
    entity: EntityReference


class RemoveSceneEntity(SceneGraphOperationBase):
    op: Literal["remove_entity"]
    entity: EntityReference
    missing_ok: bool = False


class UpsertSceneRelation(SceneGraphOperationBase):
    op: Literal["upsert_relation"]
    relation_id: SubjectIdentifier
    relation_type: RegistryIdentifier
    source: EntityReference
    target: EntityReference


class RemoveSceneRelation(SceneGraphOperationBase):
    op: Literal["remove_relation"]
    relation_id: SubjectIdentifier
    missing_ok: bool = False


SceneGraphOperation: TypeAlias = Annotated[
    AddSceneEntity | RemoveSceneEntity | UpsertSceneRelation | RemoveSceneRelation,
    Field(discriminator="op"),
]


class StorytellerStateUpdate(InternalStrictModel):
    schema_version: Literal[1] = 1
    emotional_shifts: list[EmotionalShift] = Field(default_factory=list, max_length=50)
    conversational_facts: list[ConversationalFactCandidate] = Field(default_factory=list, max_length=100)
    major_events: list[MajorEvent] = Field(default_factory=list, max_length=25)
    state_patches: list[StatePatch] = Field(default_factory=list, max_length=100)
    scene_operations: list[SceneGraphOperation] = Field(default_factory=list, max_length=100)


class StorytellerOutput(InternalStrictModel):
    """Complete generation; only narrative is user-visible."""

    narrative: Annotated[str, Field(min_length=1, max_length=20000)]
    state_update: StorytellerStateUpdate
