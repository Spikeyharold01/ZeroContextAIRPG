from pydantic import ValidationError
import pytest

from contracts.rules import RulesAdjudicationResult
from contracts.rules.dnd5e import Dnd5eDiceRollRequest


def test_universal_rules_use_registry_profile_and_generic_roll_envelope():
    result = RulesAdjudicationResult(
        rules_profile_id="campaign.custom-rules",
        operation_class="mechanical_candidate",
        requires_adjudication=True,
        roll={
            "roll_policy_id": "campaign.stress-roll",
            "parameters": {"pool": 4, "risk": "high"},
            "reason": "Resist panic",
        },
    )
    assert result.roll.parameters["pool"] == 4
    with pytest.raises(ValidationError):
        RulesAdjudicationResult(
            rules_profile_id="campaign.custom-rules",
            operation_class="non_mechanical",
            requires_adjudication=False,
            roll=result.roll,
        )


def test_dnd_dice_policy_is_adapter_owned():
    assert Dnd5eDiceRollRequest(expression="1d20", modifier=3, reason="Check")
    with pytest.raises(ValidationError):
        Dnd5eDiceRollRequest(expression="1d20+3", reason="Check")
    with pytest.raises(ValidationError):
        Dnd5eDiceRollRequest(expression="1d7", reason="Check")
    with pytest.raises(ValidationError):
        Dnd5eDiceRollRequest(expression="2d20", advantage="advantage", reason="Check")
