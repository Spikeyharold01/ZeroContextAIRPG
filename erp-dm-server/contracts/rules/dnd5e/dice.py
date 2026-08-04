"""D&D 5e dice policy."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from ...common import InternalStrictModel, ShortText


class Dnd5eDiceRollRequest(InternalStrictModel):
    expression: Annotated[str, Field(pattern=r"^[1-9][0-9]*d[1-9][0-9]*$")]
    modifier: Annotated[int, Field(ge=-30, le=30)] = 0
    difficulty_class: Annotated[int, Field(ge=1, le=40)] | None = None
    advantage: Literal["normal", "advantage", "disadvantage"] = "normal"
    reason: ShortText

    @model_validator(mode="after")
    def validate_dnd5e_dice(self):
        count_text, sides_text = self.expression.split("d", 1)
        count, sides = int(count_text), int(sides_text)
        if count > 100:
            raise ValueError("dice count cannot exceed 100")
        if sides not in {4, 6, 8, 10, 12, 20, 100}:
            raise ValueError("unsupported D&D 5e die size")
        if self.advantage != "normal" and self.expression != "1d20":
            raise ValueError("D&D 5e advantage and disadvantage require 1d20")
        return self
