"""Rules-engine adjudication contracts; dice execution is intentionally absent."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .common import IntentType, InternalStrictModel, ShortText


class DiceRollRequest(InternalStrictModel):
    expression: Annotated[str, Field(pattern=r"^[1-9][0-9]*d[1-9][0-9]*$")]
    modifier: Annotated[int, Field(ge=-30, le=30)] = 0
    difficulty_class: Annotated[int, Field(ge=1, le=40)] | None = None
    advantage: Literal["normal", "advantage", "disadvantage"] = "normal"
    reason: ShortText

    @model_validator(mode="after")
    def validate_supported_dice(self):
        count_text, sides_text = self.expression.split("d", 1)
        count, sides = int(count_text), int(sides_text)
        if count > 100:
            raise ValueError("dice count cannot exceed 100")
        if sides not in {4, 6, 8, 10, 12, 20, 100}:
            raise ValueError("unsupported die size")
        if self.advantage != "normal" and self.expression != "1d20":
            raise ValueError(
                "advantage and disadvantage are supported only for 1d20"
            )
        return self


class RulesAdjudicationResult(InternalStrictModel):
    intent: IntentType
    rules_system: Literal[
        "dnd_5e", "pathfinder", "call_of_cthulhu", "battletech", "off"
    ]
    requires_roll: bool
    roll: DiceRollRequest | None = None
    narration_hint: Annotated[str, Field(max_length=1000)] = ""
    rule_citations: list[ShortText] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def roll_matches_flag_and_intent(self):
        if self.requires_roll != (self.roll is not None):
            raise ValueError("requires_roll must agree exactly with roll presence")
        if self.intent == "narrative" and self.requires_roll:
            raise ValueError("narrative intent cannot require a mechanical roll")
        return self
