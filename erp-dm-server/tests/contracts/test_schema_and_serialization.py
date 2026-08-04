import json
from uuid import uuid4

import pytest

from contracts.ingestion import (
    ParsedIngestedContext, RecentExchange, ResolvedTurnContext, SamplingParameters,
)
from contracts.mechanics import (
    ConditionAddUpdate, ConditionRemoveUpdate, HpDamageUpdate, HpHealingUpdate,
    HpSetUpdate, ResourceDeltaUpdate,
)
from contracts.openai import (
    AssistantResponseMessage, ChatCompletionChunk, ChatCompletionResponse,
    ChatCompletionRequestBase, CompletionChoice, NonStreamingChatCompletionRequest, StreamChoice,
    StreamDelta, StreamingChatCompletionRequest, StringChatMessage, TokenUsage,
)
from contracts.rules import DiceRollRequest, RulesAdjudicationResult
from contracts.storyteller import (
    AppliedEmotionalAxisChange, ConversationalFactCandidate, EmotionalAxisDeltas,
    EmotionalShift, MajorEvent, PlotStateUpdate, SceneGraphPatch,
    SceneObjectPatch, StorytellerOutput, StorytellerStateUpdate, WorldStatePatch,
)


REQUEST_ID = uuid4()
UPDATE_ID = uuid4()
parsed = ParsedIngestedContext(
    request_id=REQUEST_ID, user_message="Hi", character_card_text="Name: Mara",
    is_first_message=True,
)

PUBLIC_INSTANCES = [
    StringChatMessage(role="user", content="Hi"),
    ChatCompletionRequestBase(model="proxy", messages=[{"role": "user", "content": "Hi"}]),
    NonStreamingChatCompletionRequest(model="proxy", messages=[{"role": "user", "content": "Hi"}]),
    StreamingChatCompletionRequest(model="proxy", messages=[{"role": "user", "content": "Hi"}], stream=True),
    AssistantResponseMessage(content="Hello"),
    CompletionChoice(index=0, message={"content": "Hello"}, finish_reason="stop"),
    TokenUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
    ChatCompletionResponse(
        id="chatcmpl-1", created=1, model="proxy",
        choices=[{"index": 0, "message": {"content": "Hello"}, "finish_reason": "stop"}],
        usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    ),
    StreamDelta(role="assistant", content="Hi"),
    StreamChoice(index=0, delta={"content": "Hi"}),
    ChatCompletionChunk(
        id="chatcmpl-1", created=1, model="proxy",
        choices=[{"index": 0, "delta": {"content": "Hi"}}],
    ),
    SamplingParameters(),
    parsed,
    RecentExchange(user="Hi", assistant="Hello"),
    ResolvedTurnContext(
        request_id=REQUEST_ID, parsed=parsed, character_id=1,
        character_name="Mara", character_type="NPC", character_is_active=True,
        history_exchange_limit=1,
    ),
    DiceRollRequest(expression="1d20", modifier=3, reason="Check"),
    RulesAdjudicationResult(
        intent="skill_check", rules_system="dnd_5e", requires_roll=True,
        roll={"expression": "1d20", "modifier": 3, "reason": "Check"},
    ),
    HpDamageUpdate(
        operation="hp_damage", update_id=UPDATE_ID, target_character_id=1,
        source="hit", amount=1, hp_before=2, hp_after=1, hp_max=2,
    ),
    HpHealingUpdate(
        operation="hp_healing", update_id=UPDATE_ID, target_character_id=1,
        source="heal", amount=1, hp_before=1, hp_after=2, hp_max=2,
    ),
    HpSetUpdate(
        operation="hp_set", update_id=UPDATE_ID, target_character_id=1,
        source="set", hp_before=1, hp_after=2, hp_max=2,
    ),
    ConditionAddUpdate(
        operation="condition_add", update_id=UPDATE_ID, target_character_id=1,
        source="fall", condition="prone",
    ),
    ConditionRemoveUpdate(
        operation="condition_remove", update_id=UPDATE_ID, target_character_id=1,
        source="stand", condition="prone",
    ),
    ResourceDeltaUpdate(
        operation="resource_delta", update_id=UPDATE_ID, target_character_id=1,
        source="cast", resource="spell_slot_level_1", delta=-1,
        value_before=2, value_after=1,
    ),
    EmotionalAxisDeltas(trust=1),
    EmotionalShift(
        character_id=1, deltas={"trust": 1}, description="Helped", confidence=0.8,
    ),
    AppliedEmotionalAxisChange(
        axis="trust", value_before=50, proposed_delta=1, proposed_result=51,
        applied_delta=1, value_after=51, boundary_adjusted=False,
    ),
    ConversationalFactCandidate(character_id=1, text="A fact"),
    MajorEvent(text="Found it", event_type="discovery", character_id=1),
    PlotStateUpdate(character_id=1, current_goal="Continue"),
    WorldStatePatch(weather="rain"),
    SceneObjectPatch(object_name="door", object_state="open"),
    SceneGraphPatch(location_id=1, visibility="dim"),
    StorytellerStateUpdate(),
    StorytellerOutput(narrative="Visible", state_update={}),
]


@pytest.mark.parametrize("instance", PUBLIC_INSTANCES, ids=lambda item: type(item).__name__)
def test_public_contract_json_round_trip_and_schema(instance):
    model_type = type(instance)
    serialized = instance.model_dump_json()
    restored = model_type.model_validate_json(serialized)
    assert restored.model_dump() == instance.model_dump()
    schema = model_type.model_json_schema()
    assert isinstance(schema, dict)
    assert schema.get("type") == "object"
    json.dumps(schema)
