"""Staged deterministic mechanical contracts with no persistence mappings."""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias
from uuid import UUID

from pydantic import Field, model_validator

from .common import InternalStrictModel, PositiveCharacterId, ShortText


ConditionName = Literal[
    "blinded", "charmed", "deafened", "exhaustion", "frightened",
    "grappled", "incapacitated", "invisible", "paralyzed", "petrified",
    "poisoned", "prone", "restrained", "stunned", "unconscious",
]
ResourceName = Literal[
    "experience_points",
    "spell_slot_level_1", "spell_slot_level_2", "spell_slot_level_3",
    "spell_slot_level_4", "spell_slot_level_5", "spell_slot_level_6",
    "spell_slot_level_7", "spell_slot_level_8", "spell_slot_level_9",
]


class StagedMechanicalUpdate(InternalStrictModel):
    update_id: UUID
    target_character_id: PositiveCharacterId
    source: ShortText
    committed: Literal[False] = False


class HpDamageUpdate(StagedMechanicalUpdate):
    operation: Literal["hp_damage"]
    amount: Annotated[int, Field(gt=0, le=100000)]
    hp_before: Annotated[int, Field(ge=0)]
    hp_after: Annotated[int, Field(ge=0)]
    hp_max: Annotated[int, Field(gt=0)]

    @model_validator(mode="after")
    def result_is_consistent(self):
        if self.hp_before > self.hp_max:
            raise ValueError("hp_before cannot exceed hp_max")
        expected = max(0, self.hp_before - self.amount)
        if self.hp_after != expected:
            raise ValueError(f"hp_after must equal {expected}")
        return self


class HpHealingUpdate(StagedMechanicalUpdate):
    operation: Literal["hp_healing"]
    amount: Annotated[int, Field(gt=0, le=100000)]
    hp_before: Annotated[int, Field(ge=0)]
    hp_after: Annotated[int, Field(ge=0)]
    hp_max: Annotated[int, Field(gt=0)]

    @model_validator(mode="after")
    def result_is_consistent(self):
        if self.hp_before > self.hp_max:
            raise ValueError("hp_before cannot exceed hp_max")
        expected = min(self.hp_max, self.hp_before + self.amount)
        if self.hp_after != expected:
            raise ValueError(f"hp_after must equal {expected}")
        return self


class HpSetUpdate(StagedMechanicalUpdate):
    operation: Literal["hp_set"]
    hp_before: Annotated[int, Field(ge=0)]
    hp_after: Annotated[int, Field(ge=0)]
    hp_max: Annotated[int, Field(gt=0)]

    @model_validator(mode="after")
    def values_are_bounded(self):
        if self.hp_before > self.hp_max or self.hp_after > self.hp_max:
            raise ValueError("HP values cannot exceed hp_max")
        return self


class ConditionAddUpdate(StagedMechanicalUpdate):
    operation: Literal["condition_add"]
    condition: ConditionName


class ConditionRemoveUpdate(StagedMechanicalUpdate):
    operation: Literal["condition_remove"]
    condition: ConditionName


class ResourceDeltaUpdate(StagedMechanicalUpdate):
    operation: Literal["resource_delta"]
    resource: ResourceName
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
    HpDamageUpdate | HpHealingUpdate | HpSetUpdate
    | ConditionAddUpdate | ConditionRemoveUpdate | ResourceDeltaUpdate,
    Field(discriminator="operation"),
]
