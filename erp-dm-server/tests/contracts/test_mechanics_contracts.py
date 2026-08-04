from uuid import uuid4

from pydantic import TypeAdapter, ValidationError
import pytest

import contracts
from contracts.mechanics import ConditionAddUpdate, DeterministicMechanicalUpdate, ResourceDeltaUpdate
from contracts.rules.dnd5e import (
    Dnd5eConditionAddUpdate,
    Dnd5eHpDamageUpdate,
    Dnd5eResourceDeltaUpdate,
)


def base(operation):
    return {
        "operation": operation,
        "update_id": uuid4(),
        "target_character_id": 12,
        "rules_profile_id": "campaign.custom-rules",
        "source": "test",
    }


def test_universal_mechanics_reference_trusted_definition_ids():
    condition = ConditionAddUpdate(**base("condition_add"), condition_definition_id="campaign.panicked")
    resource = ResourceDeltaUpdate(
        **base("resource_delta"), resource_definition_id="campaign.sanity",
        delta=-2, value_before=10, value_after=8,
    )
    assert condition.condition_definition_id == "campaign.panicked"
    assert resource.value_after == 8
    with pytest.raises(ValidationError):
        ResourceDeltaUpdate(
            **base("resource_delta"), resource_definition_id="armor_class",
            delta=1, value_before=10, value_after=11,
        )


def test_resource_math_underflow_and_discriminator():
    adapter = TypeAdapter(DeterministicMechanicalUpdate)
    value = {**base("resource_delta"), "resource_definition_id": "campaign.sanity",
             "delta": -1, "value_before": 2, "value_after": 1}
    assert isinstance(adapter.validate_python(value), ResourceDeltaUpdate)
    with pytest.raises(ValidationError, match="negative"):
        ResourceDeltaUpdate(**base("resource_delta"), resource_definition_id="campaign.sanity",
                            delta=-3, value_before=2, value_after=0)


def test_dnd_concepts_are_exported_only_from_dnd_adapter():
    assert not hasattr(contracts, "Dnd5eConditionName")
    assert not hasattr(contracts, "Dnd5eResourceName")
    assert Dnd5eConditionAddUpdate(
        **{**base("dnd5e.condition_add"), "rules_profile_id": "dnd5e.core"}, condition="prone"
    )
    assert Dnd5eResourceDeltaUpdate(
        **{**base("dnd5e.resource_delta"), "rules_profile_id": "dnd5e.core"},
        resource="spell_slot_level_3", delta=-1, value_before=2, value_after=1,
    )
    assert Dnd5eHpDamageUpdate(
        **{**base("dnd5e.hp_damage"), "rules_profile_id": "dnd5e.core"},
        amount=3, hp_before=10, hp_after=7, hp_max=10,
    )
