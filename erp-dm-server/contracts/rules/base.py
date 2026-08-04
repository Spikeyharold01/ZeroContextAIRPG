"""Campaign-neutral rules adjudication contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from ..common import BoundedJsonPatch, InternalStrictModel, RegistryIdentifier, ShortText


class GenericRollRequest(InternalStrictModel):
    """Adapter-owned roll request with universally bounded JSON parameters."""

    roll_policy_id: RegistryIdentifier
    parameters: BoundedJsonPatch
    reason: ShortText


class RulesAdjudicationResult(InternalStrictModel):
    rules_profile_id: RegistryIdentifier
    operation_class: Literal["non_mechanical", "mechanical_candidate", "administrative"]
    requires_adjudication: bool
    roll: GenericRollRequest | None = None
    narration_hint: Annotated[str, Field(max_length=1000)] = ""
    rule_citations: list[ShortText] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def roll_requires_adjudication(self):
        if self.roll is not None and not self.requires_adjudication:
            raise ValueError("a roll request requires adjudication")
        return self
