import json
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError
import pytest

from contracts.state import (
    StateOperation,
    StatePatch,
    StatePatchConflict,
    StateTarget,
    apply_state_patch,
)
from contracts.storyteller import (
    AddSceneEntity,
    AppliedEmotionalAxisChange,
    ConversationalFactCandidate,
    EmotionalShift,
    MajorEvent,
    RemoveSceneEntity,
    RemoveSceneRelation,
    StorytellerOutput,
    StorytellerStateUpdate,
    UpsertSceneRelation,
)


def patch(operations, **updates):
    data = {
        "target": {
            "namespace": "campaign.world",
            "subject_type": "core.campaign",
            "subject_id": "primary",
        },
        "operations": operations,
        "idempotency_key": uuid4(),
    }
    data.update(updates)
    return StatePatch.model_validate(data)


@pytest.mark.parametrize("path,value", [
    (["kingdoms", "north", "ruler"], "Queen Mara"),
    (["starships", "odyssey", "reactor"], {"output": 91, "stable": True}),
    (["investigation", "sanity", "clock"], 4),
])
def test_arbitrary_genre_state_needs_no_predefined_property(path, value):
    result, revision = apply_state_patch({}, 0, patch([{"op": "set", "path": path, "value": value}]))
    current = result
    for segment in path:
        current = current[segment]
    assert current == value
    assert revision == 1


def test_fixed_example_and_narrative_fields_are_absent():
    state_schema = json.dumps(StatePatch.model_json_schema())
    storyteller_schema = json.dumps(StorytellerStateUpdate.model_json_schema())
    for forbidden in (
        "war_active", "bridge_destroyed", "festival_active", "moon_phase",
        "weather", "visibility", "current_goal", "hidden_goal",
        "immediate_beat", "long_arc", "tension", "trust", "fear", "arousal",
        "intimacy", "table", "column",
    ):
        assert f'"{forbidden}"' not in state_schema
        assert f'"{forbidden}"' not in storyteller_schema


@pytest.mark.parametrize("identifier", ["core.discovery", "dnd5e.combat", "campaign.coronation"])
def test_registry_identifiers_accept_namespaced_values(identifier):
    assert MajorEvent(text="Something happened", event_type=identifier).event_type == identifier


@pytest.mark.parametrize("identifier", ["combat", "_private.value", "$private.value", "Engine.Internal"])
def test_invalid_registry_identifiers_are_rejected(identifier):
    with pytest.raises(ValidationError):
        MajorEvent(text="Something happened", event_type=identifier)
    if identifier == "Engine.Internal":
        return


def test_reserved_namespaces_are_rejected():
    with pytest.raises(ValidationError, match="reserved"):
        StateTarget(namespace="engine.internal", subject_type="core.campaign", subject_id="primary")


def test_path_limits_and_no_positional_array_operations():
    assert patch([{"op": "set", "path": ["x"] * 16, "value": 1}])
    with pytest.raises(ValidationError):
        patch([{"op": "set", "path": ["x"] * 17, "value": 1}])
    with pytest.raises(ValidationError):
        patch([{"op": "set", "path": ["x" * 129], "value": 1}])
    schema = json.dumps(TypeAdapter(StateOperation).json_schema())
    for forbidden in ("insert", "index", "position"):
        assert f'"{forbidden}"' not in schema


def test_operation_count_and_json_payload_bounds_are_enforced():
    assert patch([{"op": "set", "path": [f"key-{i}"], "value": i} for i in range(100)])
    with pytest.raises(ValidationError):
        patch([{"op": "set", "path": [f"key-{i}"], "value": i} for i in range(101)])
    with pytest.raises(ValidationError, match="nesting depth"):
        patch([{"op": "set", "path": ["nested"],
                "value": {"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}}}])
    with pytest.raises(ValidationError, match="serialized size"):
        patch([{"op": "set", "path": ["large"], "value": "x" * (32 * 1024)}])
    assert patch([{"op": "set", "path": ["object"],
                   "value": {f"key-{i}": i for i in range(100)}}])
    with pytest.raises(ValidationError, match="key count"):
        patch([{"op": "set", "path": ["object"],
                "value": {f"key-{i}": i for i in range(101)}}])


@pytest.mark.parametrize("value", [
    {"_private": 1}, {"$operator": 1}, {"bad": float("nan")},
    {"bad": float("inf")}, {"bad": (1, 2)}, {"bad": b"bytes"},
])
def test_state_values_reject_reserved_keys_and_non_json_values(value):
    with pytest.raises(ValidationError):
        patch([{"op": "set", "path": ["value"], "value": value}])


def test_explicit_null_is_distinct_from_remove_and_missing_remove_policy():
    state, _ = apply_state_patch({"flag": True}, 0, patch([{"op": "set", "path": ["flag"], "value": None}]))
    assert "flag" in state and state["flag"] is None
    state, _ = apply_state_patch(state, 1, patch([{"op": "remove", "path": ["flag"]}]))
    assert "flag" not in state
    with pytest.raises(StatePatchConflict, match="missing"):
        apply_state_patch({}, 0, patch([{"op": "remove", "path": ["flag"]}]))
    assert apply_state_patch({}, 0, patch([{"op": "remove", "path": ["flag"], "missing_ok": True}]))[0] == {}


def test_merge_is_shallow_and_operations_are_ordered():
    original = {"faction": {"leader": {"name": "Mara", "rank": 3}, "power": 2}}
    state, _ = apply_state_patch(original, 0, patch([
        {"op": "merge_object", "path": ["faction"], "value": {"leader": {"name": "Ivo"}}},
        {"op": "set", "path": ["faction", "power"], "value": 4},
    ]))
    assert state == {"faction": {"leader": {"name": "Ivo"}, "power": 4}}


def test_set_members_use_canonical_equality_and_add_is_idempotent():
    member_a = {"id": 1, "kind": "discovery"}
    member_b = {"kind": "discovery", "id": 1}
    state, _ = apply_state_patch({"known": [member_a]}, 0, patch([
        {"op": "add_set_member", "path": ["known"], "member": member_b},
    ]))
    assert len(state["known"]) == 1
    state, _ = apply_state_patch(state, 1, patch([
        {"op": "remove_set_member", "path": ["known"], "member": member_b},
    ]))
    assert state["known"] == []


def test_expected_or_revision_conflict_rejects_complete_patch():
    original = {"a": 1, "b": 2}
    expected_conflict = patch([
        {"op": "set", "path": ["a"], "value": 9},
        {"op": "set", "path": ["b"], "value": 8, "expected": {"value": 99}},
    ])
    with pytest.raises(StatePatchConflict):
        apply_state_patch(original, 4, expected_conflict)
    assert original == {"a": 1, "b": 2}
    with pytest.raises(StatePatchConflict, match="revision"):
        apply_state_patch(original, 4, patch([{"op": "set", "path": ["a"], "value": 3}], base_revision=3))


def test_emotional_axis_is_configurable_and_preserves_application_result():
    shift = EmotionalShift(
        character_id=1, affect_axis_definition_id="campaign.resolve",
        proposed_delta=20, description="Held firm", confidence=0.8,
    )
    assert shift.proposed_delta == 20
    applied = AppliedEmotionalAxisChange(
        affect_axis_definition_id="campaign.resolve", value_before=95,
        proposed_delta=20, proposed_result=115, applied_delta=5,
        value_after=100, boundary_adjusted=True,
    )
    assert applied.proposed_result == 115 and applied.value_after == 100
    assert EmotionalShift(character_id=1, affect_axis_definition_id="campaign.resolve",
                          proposed_delta=21, description="Profile validates later",
                          confidence=0.8).proposed_delta == 21
    with pytest.raises(ValidationError):
        EmotionalShift(character_id=1, affect_axis_definition_id="campaign.resolve",
                       proposed_delta=float("inf"), description="Invalid", confidence=0.8)


def test_fact_proxy_ownership_and_storyteller_mechanics_forbidden():
    with pytest.raises(ValidationError):
        ConversationalFactCandidate(character_id=1, text="Fact", embedding=[1.0])
    output = StorytellerOutput(narrative="Visible", state_update={})
    assert output.narrative == "Visible"
    with pytest.raises(ValidationError):
        StorytellerStateUpdate(mechanical_updates=[])


def test_scene_topology_is_generic_and_has_no_object_or_npc_taxonomy():
    entity = {"entity_kind": "campaign.starship", "entity_id": "odyssey"}
    location = {"entity_kind": "campaign.starport", "entity_id": "ceres"}
    operations = [
        AddSceneEntity(op="add_entity", scene_id="dock-7", entity=entity),
        RemoveSceneEntity(op="remove_entity", scene_id="dock-7", entity=entity),
        UpsertSceneRelation(op="upsert_relation", scene_id="dock-7", relation_id="docked",
                            relation_type="campaign.docked-at", source=entity, target=location),
        RemoveSceneRelation(op="remove_relation", scene_id="dock-7", relation_id="docked"),
    ]
    assert [operation.op for operation in operations] == [
        "add_entity", "remove_entity", "upsert_relation", "remove_relation",
    ]
    schema = json.dumps(StorytellerStateUpdate.model_json_schema())
    for forbidden in ('"object_name"', '"npc_present"', '"visibility"'):
        assert forbidden not in schema
