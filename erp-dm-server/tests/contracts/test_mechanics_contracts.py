from uuid import uuid4

from pydantic import TypeAdapter, ValidationError
import pytest

from contracts.mechanics import (
    ConditionAddUpdate,
    DeterministicMechanicalUpdate,
    HpDamageUpdate,
    HpHealingUpdate,
    HpSetUpdate,
    ResourceDeltaUpdate,
)


def base(operation):
    return {
        "operation": operation,
        "update_id": uuid4(),
        "target_character_id": 12,
        "source": "test",
    }


def test_valid_hp_operations():
    damage = HpDamageUpdate.model_validate({
        **base("hp_damage"), "amount": 7, "hp_before": 22,
        "hp_after": 15, "hp_max": 30,
    })
    healing = HpHealingUpdate.model_validate({
        **base("hp_healing"), "amount": 20, "hp_before": 22,
        "hp_after": 30, "hp_max": 30,
    })
    set_hp = HpSetUpdate.model_validate({
        **base("hp_set"), "hp_before": 22, "hp_after": 1, "hp_max": 30,
    })
    assert (damage.hp_after, healing.hp_after, set_hp.hp_after) == (15, 30, 1)


@pytest.mark.parametrize("model,values", [
    (HpDamageUpdate, {"operation": "hp_damage", "amount": 7, "hp_before": 22, "hp_after": 14, "hp_max": 30}),
    (HpHealingUpdate, {"operation": "hp_healing", "amount": 4, "hp_before": 22, "hp_after": 27, "hp_max": 30}),
    (HpSetUpdate, {"operation": "hp_set", "hp_before": 22, "hp_after": 31, "hp_max": 30}),
    (HpDamageUpdate, {"operation": "hp_damage", "amount": 0, "hp_before": 22, "hp_after": 22, "hp_max": 30}),
])
def test_invalid_hp_math_is_rejected(model, values):
    with pytest.raises(ValidationError):
        model.model_validate({**base(values["operation"]), **values})


def test_conditions_and_resources_are_explicit():
    assert ConditionAddUpdate.model_validate({
        **base("condition_add"), "condition": "prone"
    }).condition == "prone"
    with pytest.raises(ValidationError):
        ConditionAddUpdate.model_validate({
            **base("condition_add"), "condition": "custom_database_value"
        })
    with pytest.raises(ValidationError):
        ResourceDeltaUpdate.model_validate({
            **base("resource_delta"), "resource": "armor_class", "delta": 1,
            "value_before": 10, "value_after": 11,
        })


def test_resource_math_and_underflow():
    valid = ResourceDeltaUpdate.model_validate({
        **base("resource_delta"), "resource": "spell_slot_level_3",
        "delta": -1, "value_before": 2, "value_after": 1, "maximum": 3,
    })
    assert valid.value_after == 1
    with pytest.raises(ValidationError, match="negative"):
        ResourceDeltaUpdate.model_validate({
            **base("resource_delta"), "resource": "spell_slot_level_3",
            "delta": -2, "value_before": 1, "value_after": 0,
        })


def test_discriminator_selects_subtype_and_committed_cannot_be_true():
    adapter = TypeAdapter(DeterministicMechanicalUpdate)
    result = adapter.validate_python({
        **base("hp_damage"), "amount": 1, "hp_before": 2,
        "hp_after": 1, "hp_max": 2,
    })
    assert isinstance(result, HpDamageUpdate)
    assert isinstance(adapter.validate_json(adapter.dump_json(result)), HpDamageUpdate)
    assert adapter.json_schema()["discriminator"]["propertyName"] == "operation"
    data = result.model_dump()
    data["committed"] = True
    with pytest.raises(ValidationError):
        adapter.validate_python(data)
