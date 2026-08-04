"""D&D 5e-specific mechanical updates."""

from typing import Annotated, Literal, TypeAlias

from pydantic import Field, model_validator

from ...mechanics import StagedMechanicalUpdate

Dnd5eConditionName = Literal[
    "blinded", "charmed", "deafened", "exhaustion", "frightened",
    "grappled", "incapacitated", "invisible", "paralyzed", "petrified",
    "poisoned", "prone", "restrained", "stunned", "unconscious",
]
Dnd5eResourceName = Literal[
    "experience_points",
    "spell_slot_level_1", "spell_slot_level_2", "spell_slot_level_3",
    "spell_slot_level_4", "spell_slot_level_5", "spell_slot_level_6",
    "spell_slot_level_7", "spell_slot_level_8", "spell_slot_level_9",
]


class Dnd5eHpDamageUpdate(StagedMechanicalUpdate):
    operation: Literal["dnd5e.hp_damage"]
    amount: Annotated[int, Field(gt=0, le=100000)]
    hp_before: Annotated[int, Field(ge=0)]
    hp_after: Annotated[int, Field(ge=0)]
    hp_max: Annotated[int, Field(gt=0)]

    @model_validator(mode="after")
    def result_is_consistent(self):
        if self.hp_before > self.hp_max or self.hp_after != max(0, self.hp_before - self.amount):
            raise ValueError("D&D HP damage result is inconsistent")
        return self


class Dnd5eHpHealingUpdate(StagedMechanicalUpdate):
    operation: Literal["dnd5e.hp_healing"]
    amount: Annotated[int, Field(gt=0, le=100000)]
    hp_before: Annotated[int, Field(ge=0)]
    hp_after: Annotated[int, Field(ge=0)]
    hp_max: Annotated[int, Field(gt=0)]

    @model_validator(mode="after")
    def result_is_consistent(self):
        if self.hp_before > self.hp_max or self.hp_after != min(self.hp_max, self.hp_before + self.amount):
            raise ValueError("D&D HP healing result is inconsistent")
        return self


class Dnd5eHpSetUpdate(StagedMechanicalUpdate):
    operation: Literal["dnd5e.hp_set"]
    hp_before: Annotated[int, Field(ge=0)]
    hp_after: Annotated[int, Field(ge=0)]
    hp_max: Annotated[int, Field(gt=0)]

    @model_validator(mode="after")
    def values_are_bounded(self):
        if self.hp_before > self.hp_max or self.hp_after > self.hp_max:
            raise ValueError("D&D HP values cannot exceed hp_max")
        return self


class Dnd5eConditionAddUpdate(StagedMechanicalUpdate):
    operation: Literal["dnd5e.condition_add"]
    condition: Dnd5eConditionName


class Dnd5eConditionRemoveUpdate(StagedMechanicalUpdate):
    operation: Literal["dnd5e.condition_remove"]
    condition: Dnd5eConditionName


class Dnd5eResourceDeltaUpdate(StagedMechanicalUpdate):
    operation: Literal["dnd5e.resource_delta"]
    resource: Dnd5eResourceName
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
            raise ValueError("resource result is inconsistent")
        return self


Dnd5eMechanicalUpdate: TypeAlias = Annotated[
    Dnd5eHpDamageUpdate | Dnd5eHpHealingUpdate | Dnd5eHpSetUpdate
    | Dnd5eConditionAddUpdate | Dnd5eConditionRemoveUpdate | Dnd5eResourceDeltaUpdate,
    Field(discriminator="operation"),
]
