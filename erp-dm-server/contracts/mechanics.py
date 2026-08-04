"""Universal staged mechanics referencing trusted configured definitions."""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias
from uuid import UUID

from pydantic import Field, model_validator

from .common import InternalStrictModel, PositiveCharacterId, RegistryIdentifier, ShortText


class StagedMechanicalUpdate(InternalStrictModel):
    update_id: UUID
    target_character_id: PositiveCharacterId
    rules_profile_id: RegistryIdentifier
    source: ShortText
    committed: Literal[False] = False


class ConditionAddUpdate(StagedMechanicalUpdate):
    operation: Literal["condition_add"]
    condition_definition_id: RegistryIdentifier


class ConditionRemoveUpdate(StagedMechanicalUpdate):
    operation: Literal["condition_remove"]
    condition_definition_id: RegistryIdentifier


class ResourceDeltaUpdate(StagedMechanicalUpdate):
    operation: Literal["resource_delta"]
    resource_definition_id: RegistryIdentifier
    delta: Annotated[int, Field(ge=-100000, le=100000)]
    value_before: Annotated[int, Field(ge=0)]
    value_after: Annotated[int, Field(ge=0)]
    maximum: Annotated[int, Field(ge=0)] | None = None

    @model_validator(mode="after")
    def result_is_consistent(self):
        if self.delta == 0:
            raise ValueError("delta cannot be zero")
        expected = self.value_before + self.delta
        if expected < 0:
            raise ValueError("resource delta cannot produce a negative value")
        if self.maximum is not None:
            if self.value_before > self.maximum:
                raise ValueError("value_before cannot exceed maximum")
            expected = min(expected, self.maximum)
        if self.value_after != expected:
            raise ValueError(f"value_after must equal {expected}")
        return self


DeterministicMechanicalUpdate: TypeAlias = Annotated[
    ConditionAddUpdate | ConditionRemoveUpdate | ResourceDeltaUpdate,
    Field(discriminator="operation"),
]
