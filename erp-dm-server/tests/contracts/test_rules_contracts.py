from pydantic import ValidationError
import pytest

from contracts.rules import DiceRollRequest, RulesAdjudicationResult


def roll(**updates):
    values = {"expression": "1d20", "modifier": 3, "reason": "Pick lock"}
    values.update(updates)
    return DiceRollRequest.model_validate(values)


def test_unmodified_expression_and_separate_modifier():
    assert roll().modifier == 3
    with pytest.raises(ValidationError):
        roll(expression="1d20+3")


@pytest.mark.parametrize("updates", [
    {"expression": "1d7"},
    {"expression": "101d6"},
    {"expression": "2d20", "advantage": "advantage"},
])
def test_unsupported_dice_are_rejected(updates):
    with pytest.raises(ValidationError):
        roll(**updates)


def test_requires_roll_agrees_with_roll_presence_and_intent():
    valid = RulesAdjudicationResult.model_validate({
        "intent": "skill_check", "rules_system": "dnd_5e",
        "requires_roll": True, "roll": roll(),
    })
    assert valid.roll is not None
    with pytest.raises(ValidationError):
        RulesAdjudicationResult.model_validate({
            "intent": "skill_check", "rules_system": "dnd_5e",
            "requires_roll": True, "roll": None,
        })
    with pytest.raises(ValidationError):
        RulesAdjudicationResult.model_validate({
            "intent": "narrative", "rules_system": "dnd_5e",
            "requires_roll": True, "roll": roll(),
        })
