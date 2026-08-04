from datetime import datetime
import math

from pydantic import ValidationError
import pytest

from contracts.common import MAX_PATCH_BYTES
from contracts.storyteller import (
    AppliedEmotionalAxisChange,
    ConversationalFactCandidate,
    EmotionalAxisDeltas,
    EmotionalShift,
    MajorEvent,
    PlotStateUpdate,
    SceneGraphPatch,
    StorytellerOutput,
    StorytellerStateUpdate,
    WorldStatePatch,
)


def nested_patch(container_depth):
    value = True
    for index in range(container_depth):
        value = {f"level{index}": value}
    return value


def test_patch_depth_five_accepted_and_six_rejected():
    assert PlotStateUpdate(character_id=1, plot_state_patch=nested_patch(5))
    with pytest.raises(ValidationError, match="nesting depth"):
        PlotStateUpdate(character_id=1, plot_state_patch=nested_patch(6))


def test_patch_key_count_limits():
    assert WorldStatePatch(additional_state_patch={f"key{i}": i for i in range(100)})
    with pytest.raises(ValidationError, match="key count"):
        WorldStatePatch(additional_state_patch={f"key{i}": i for i in range(101)})


def test_patch_serialized_size_limits():
    # Account for the compact JSON wrapper: {"value":"..."}.
    accepted = {"value": "x" * (MAX_PATCH_BYTES - 12)}
    assert WorldStatePatch(additional_state_patch=accepted)
    with pytest.raises(ValidationError, match="serialized size"):
        WorldStatePatch(additional_state_patch={"value": "x" * MAX_PATCH_BYTES})


@pytest.mark.parametrize("patch", [
    {"": 1}, {"   ": 1}, {"x" * 129: 1}, {"_private": 1}, {"$operator": 1},
    {"value": float("nan")}, {"value": float("inf")},
    {"value": (1, 2)}, {"value": {1, 2}}, {"value": b"bytes"},
    {"value": datetime.now()}, {"value": object()},
])
def test_invalid_patch_values_are_rejected(patch):
    with pytest.raises(ValidationError):
        WorldStatePatch(additional_state_patch=patch)


def test_valid_json_patch_values_are_accepted():
    patch = {
        "string": "x", "integer": 1, "number": 1.5, "boolean": True,
        "nothing": None, "list": [1, "two", False], "object": {"nested": 3},
    }
    assert WorldStatePatch(additional_state_patch=patch).additional_state_patch == patch


def test_fact_provenance_and_score_rules():
    belief = ConversationalFactCandidate(
        character_id=1, text="Mara believes it.", fact_type="belief_fact",
        source_character_id=1, confidence=1.0, importance=0.0,
    )
    assert belief.source_character_id == 1
    for fact_type in ("belief_fact", "rumor_fact"):
        with pytest.raises(ValidationError, match="source_character_id"):
            ConversationalFactCandidate(
                character_id=1, text="Claim", fact_type=fact_type
            )
    with pytest.raises(ValidationError, match="world facts"):
        ConversationalFactCandidate(
            character_id=1, text="Truth", fact_type="world_fact",
            source_character_id=1,
        )
    for field, value in (("confidence", -0.1), ("importance", 1.1)):
        with pytest.raises(ValidationError):
            ConversationalFactCandidate.model_validate({
                "character_id": 1, "text": "Fact", field: value,
            })
    for proxy_owned_field in (
        "id", "embedding", "created_turn", "last_referenced_turn",
        "expires_at_turn", "timestamp",
    ):
        with pytest.raises(ValidationError):
            ConversationalFactCandidate.model_validate({
                "character_id": 1, "text": "Fact", proxy_owned_field: 1,
            })


def test_plot_updates_are_explicit_nonempty_patches():
    assert PlotStateUpdate(character_id=1, current_goal="Continue")
    with pytest.raises(ValidationError):
        PlotStateUpdate(character_id=1, current_goal="")
    with pytest.raises(ValidationError):
        PlotStateUpdate.model_validate({
            "character_id": 1, "plot_state": {"replace": "everything"}
        })


def test_major_event_uses_one_optional_character_and_known_type():
    assert MajorEvent(
        text="Found it", event_type="discovery", character_id=1
    ).character_id == 1
    with pytest.raises(ValidationError):
        MajorEvent.model_validate({
            "text": "Found it", "event_type": "discovery", "character_ids": [1, 2]
        })
    with pytest.raises(ValidationError):
        MajorEvent(text="Found it", event_type="unexpected")


def test_emotional_proposal_bounds_and_applied_result_distinction():
    assert EmotionalAxisDeltas(trust=-20).trust == -20
    assert EmotionalAxisDeltas(trust=20).trust == 20
    with pytest.raises(ValidationError):
        EmotionalAxisDeltas(trust=21)
    shift = EmotionalShift(
        character_id=1, deltas={"trust": 20}, description="Promise kept",
        confidence=0.8,
    )
    assert shift.deltas.trust == 20  # no database-aware clamping here
    applied = AppliedEmotionalAxisChange(
        axis="trust", value_before=95, proposed_delta=20, proposed_result=115,
        applied_delta=5, value_after=100, boundary_adjusted=True,
    )
    assert applied.proposed_result == 115
    assert applied.value_after == 100
    with pytest.raises(ValidationError):
        AppliedEmotionalAxisChange(
            axis="trust", value_before=95, proposed_delta=20, proposed_result=100,
            applied_delta=5, value_after=100, boundary_adjusted=True,
        )


def test_scene_patch_conflicts_are_rejected():
    with pytest.raises(ValidationError):
        SceneGraphPatch(
            location_id=1,
            upsert_objects=[{"object_name": "Door", "object_state": "open"}],
            remove_objects=["door"],
        )
    with pytest.raises(ValidationError):
        SceneGraphPatch(location_id=1, add_npc_ids=[1], remove_npc_ids=[1])


def test_hidden_state_is_separate_and_mechanics_are_forbidden():
    output = StorytellerOutput(
        narrative="The door opens.", state_update=StorytellerStateUpdate()
    )
    assert output.narrative == "The door opens."
    assert output.state_update.model_dump() != output.narrative
    with pytest.raises(ValidationError):
        StorytellerStateUpdate.model_validate({
            "mechanical_updates": [{"operation": "hp_damage"}]
        })
    with pytest.raises(ValidationError):
        StorytellerOutput.model_validate({
            "narrative": "Visible", "state_update": {}, "hidden_goal": "secret"
        })
