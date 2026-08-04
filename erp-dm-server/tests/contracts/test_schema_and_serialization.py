import json
from uuid import uuid4

import pytest

from contracts.ingestion import ParsedIngestedContext, RecentExchange, ResolvedTurnContext, SamplingParameters
from contracts.mechanics import ConditionAddUpdate, ConditionRemoveUpdate, ResourceDeltaUpdate
from contracts.openai import (
    AssistantResponseMessage, ChatCompletionChunk, ChatCompletionResponse,
    ChatCompletionRequestBase, CompletionChoice, NonStreamingChatCompletionRequest,
    StreamChoice, StreamDelta, StreamingChatCompletionRequest, StringChatMessage, TokenUsage,
)
from contracts.rules import GenericRollRequest, RulesAdjudicationResult
from contracts.state import AddSetMember, EntityReference, MergeObject, RemoveSetMember, RemoveValue, SetValue, StatePatch, StateTarget
from contracts.storyteller import (
    AddSceneEntity, AppliedEmotionalAxisChange, ConversationalFactCandidate,
    EmotionalShift, MajorEvent, RemoveSceneEntity, RemoveSceneRelation,
    StorytellerOutput, StorytellerStateUpdate, UpsertSceneRelation,
)


REQUEST_ID = uuid4()
UPDATE_ID = uuid4()
TARGET = {"namespace": "campaign.world", "subject_type": "core.campaign", "subject_id": "primary"}
parsed = ParsedIngestedContext(
    request_id=REQUEST_ID, user_message="Hi", character_card_text="Name: Mara", is_first_message=True,
)

PUBLIC_INSTANCES = [
    StringChatMessage(role="user", content="Hi"),
    ChatCompletionRequestBase(model="proxy", messages=[{"role": "user", "content": "Hi"}]),
    NonStreamingChatCompletionRequest(model="proxy", messages=[{"role": "user", "content": "Hi"}]),
    StreamingChatCompletionRequest(model="proxy", messages=[{"role": "user", "content": "Hi"}], stream=True),
    AssistantResponseMessage(content="Hello"),
    CompletionChoice(index=0, message={"content": "Hello"}, finish_reason="stop"),
    TokenUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
    ChatCompletionResponse(id="chatcmpl-1", created=1, model="proxy", choices=[{
        "index": 0, "message": {"content": "Hello"}, "finish_reason": "stop",
    }], usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}),
    StreamDelta(role="assistant", content="Hi"),
    StreamChoice(index=0, delta={"content": "Hi"}),
    ChatCompletionChunk(id="chatcmpl-1", created=1, model="proxy", choices=[{"index": 0, "delta": {"content": "Hi"}}]),
    SamplingParameters(), parsed, RecentExchange(user="Hi", assistant="Hello"),
    ResolvedTurnContext(request_id=REQUEST_ID, parsed=parsed, character_id=1,
                        character_name="Mara", entity_kind="core.character",
                        control_type="core.non-player", character_is_active=True,
                        history_exchange_limit=1),
    GenericRollRequest(roll_policy_id="campaign.stress-roll", parameters={"pool": 2}, reason="Test"),
    RulesAdjudicationResult(rules_profile_id="campaign.custom-rules",
                            operation_class="mechanical_candidate", requires_adjudication=True),
    ConditionAddUpdate(operation="condition_add", update_id=UPDATE_ID, target_character_id=1,
                       rules_profile_id="campaign.custom-rules", source="test",
                       condition_definition_id="campaign.panicked"),
    ConditionRemoveUpdate(operation="condition_remove", update_id=UPDATE_ID, target_character_id=1,
                          rules_profile_id="campaign.custom-rules", source="test",
                          condition_definition_id="campaign.panicked"),
    ResourceDeltaUpdate(operation="resource_delta", update_id=UPDATE_ID, target_character_id=1,
                        rules_profile_id="campaign.custom-rules", source="test",
                        resource_definition_id="campaign.sanity", delta=-1,
                        value_before=2, value_after=1),
    EntityReference(entity_kind="core.character", entity_id="mara"),
    StateTarget(**TARGET),
    SetValue(op="set", path=["x"], value=1), RemoveValue(op="remove", path=["x"]),
    MergeObject(op="merge_object", path=["x"], value={"y": 1}),
    AddSetMember(op="add_set_member", path=["x"], member=1),
    RemoveSetMember(op="remove_set_member", path=["x"], member=1),
    StatePatch(target=TARGET, operations=[{"op": "set", "path": ["x"], "value": 1}], idempotency_key=UPDATE_ID),
    EmotionalShift(character_id=1, affect_axis_definition_id="campaign.resolve",
                   proposed_delta=1, description="Helped", confidence=0.8),
    AppliedEmotionalAxisChange(affect_axis_definition_id="campaign.resolve", value_before=1,
                               proposed_delta=1, proposed_result=2, applied_delta=1,
                               value_after=2, boundary_adjusted=False),
    ConversationalFactCandidate(character_id=1, text="A fact"),
    MajorEvent(text="Found it", event_type="core.discovery", character_id=1),
    AddSceneEntity(op="add_entity", scene_id="hall", entity={"entity_kind": "core.character", "entity_id": "mara"}),
    RemoveSceneEntity(op="remove_entity", scene_id="hall", entity={"entity_kind": "core.character", "entity_id": "mara"}),
    UpsertSceneRelation(op="upsert_relation", scene_id="hall", relation_id="mara-in-hall",
                        relation_type="core.present-in", source={"entity_kind": "core.character", "entity_id": "mara"},
                        target={"entity_kind": "core.location", "entity_id": "hall"}),
    RemoveSceneRelation(op="remove_relation", scene_id="hall", relation_id="mara-in-hall"),
    StorytellerStateUpdate(), StorytellerOutput(narrative="Visible", state_update={}),
]


@pytest.mark.parametrize("instance", PUBLIC_INSTANCES, ids=lambda item: type(item).__name__)
def test_public_contract_json_round_trip_and_schema(instance):
    model_type = type(instance)
    restored = model_type.model_validate_json(instance.model_dump_json())
    assert restored.model_dump() == instance.model_dump()
    schema = model_type.model_json_schema()
    assert schema.get("type") == "object"
    json.dumps(schema)


def test_universal_schemas_have_no_sql_selector_fields():
    schemas = json.dumps([type(instance).model_json_schema() for instance in PUBLIC_INSTANCES])
    for forbidden in ('"table"', '"table_name"', '"column"', '"column_name"'):
        assert forbidden not in schemas
