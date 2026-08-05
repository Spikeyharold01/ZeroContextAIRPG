import json
from enum import Enum
from uuid import uuid4

import pytest
from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

from config import EngineConfig
from contracts.rules.dnd5e.mechanics import Dnd5eHpDamageUpdate
from contracts.state import StatePatch
from contracts.storyteller import ConversationalFactCandidate, MajorEvent, StorytellerOutput
from structured_output import StructuredOutputPolicy, validate_structured_output
import structured_output.recovery as recovery


class Mood(str, Enum):
    calm = "calm"
    afraid = "afraid"


class Emotion(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    fear: int = Field(ge=0, le=100)
    trust: int = Field(ge=0, le=100)
    mood: Mood = Mood.calm


class Arithmetic(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    before: int
    delta: int
    after: int

    @model_validator(mode="after")
    def consistent(self):
        if self.after != self.before + self.delta:
            raise ValueError("inconsistent arithmetic")
        return self


class EmotionList(RootModel[list[Emotion]]):
    pass


class GenericState(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    state: dict


class ValidationExplosion(RuntimeError):
    pass


class ExplodingModel(BaseModel):
    x: int

    @model_validator(mode="after")
    def explode(self):
        raise ValidationExplosion("must remain internal")


class RuntimeExplodingModel(BaseModel):
    x: int

    @model_validator(mode="after")
    def explode(self):
        raise RuntimeError("must remain internal")


class ValueExplodingModel(BaseModel):
    x: int

    @model_validator(mode="after")
    def explode(self):
        raise ValueError("normal Pydantic semantic failure")


@pytest.fixture
def repair_spy(monkeypatch):
    calls = []

    def repair(raw):
        calls.append(raw)
        mappings = {
            '{"fear": 80 "trust": 20}': '{"fear":80,"trust":20}',
            '{"fear":80,"trust":20,}': '{"fear":80,"trust":20}',
            '{"fear":80,"trust":20': '{"fear":80,"trust":20}',
            '[{"fear":80,"trust":20}': '[{"fear":80,"trust":20}]',
            '{"fear":80,"trust:20}': '{"fear":80,"trust":20}',
            '{"fear": "80", "trust": 20': '{"fear":"80","trust":20}',
            '{"fear": 800, "trust": 20': '{"fear":800,"trust":20}',
            '{"fear": 80, "database_table": "characters"': '{"fear":80,"database_table":"characters"}',
            '{"fear": "broken, "trust": 20}': '{"fear":"broken","trust":20}',
        }
        return mappings.get(raw, raw)

    monkeypatch.setattr(recovery, "_repair_json_once", repair)
    return calls


def test_valid_json_never_calls_repair(repair_spy):
    result = validate_structured_output('{"fear":10,"trust":20}', Emotion)
    assert result.status == "valid"
    assert result.validated_model.fear == 10
    assert repair_spy == []


def test_disabled_policy_still_validates_but_never_repairs(repair_spy):
    policy = StructuredOutputPolicy(enabled=False)
    assert validate_structured_output('{"fear":10,"trust":20}', Emotion, policy).status == "valid"
    syntax = validate_structured_output('{"fear":10 "trust":20}', Emotion, policy)
    schema = validate_structured_output('{"fear":800,"trust":20}', Emotion, policy)
    assert syntax.status == "syntax_repair_failed" and not syntax.repair_attempted
    assert schema.status == "schema_validation_failed"
    assert repair_spy == []


@pytest.mark.parametrize("model", [RuntimeExplodingModel, ExplodingModel])
def test_unexpected_first_validation_exceptions_are_controlled(model, repair_spy):
    result = validate_structured_output('{"x":1}', model)
    assert result.status == "validation_infrastructure_failed"
    assert result.failure_category == "validation_infrastructure"
    assert result.error_summary in {"RuntimeError", "ValidationExplosion"}
    assert repair_spy == []
    assert result.secure_debug_raw_output is None


def test_value_error_from_validator_remains_expected_schema_failure(repair_spy):
    result = validate_structured_output('{"x":1}', ValueExplodingModel)
    assert result.status == "schema_validation_failed"
    assert repair_spy == []


def test_unexpected_second_validation_exception_is_controlled(monkeypatch):
    calls = []
    monkeypatch.setattr(recovery, "_repair_json_once", lambda raw: calls.append(raw) or '{"x":1}')
    result = validate_structured_output('{"x":1', ExplodingModel)
    assert result.status == "validation_infrastructure_failed"
    assert result.diagnostics.second_validation_category == "infrastructure"
    assert len(calls) == 1


@pytest.mark.parametrize("raw", [
    '{"fear": 80 "trust": 20}',
    '{"fear":80,"trust":20,}',
    '{"fear":80,"trust":20',
])
def test_syntax_is_repaired_exactly_once(raw, repair_spy):
    result = validate_structured_output(raw, Emotion)
    assert result.status == "repaired"
    assert result.repair_attempted and result.repair_succeeded
    assert result.diagnostics.repair_attempts == 1
    assert len(repair_spy) == 1


def test_missing_array_close_and_malformed_quote_repair_once(repair_spy):
    array_result = validate_structured_output('[{"fear":80,"trust":20}', EmotionList)
    quote_result = validate_structured_output('{"fear":80,"trust:20}', Emotion)
    assert array_result.status == quote_result.status == "repaired"
    assert len(repair_spy) == 2  # one independent attempt for each output


def test_fence_and_designated_block_are_deterministic_envelopes(repair_spy):
    fenced = validate_structured_output('```json\n{"fear":10,"trust":20}\n```', Emotion)
    blocked = validate_structured_output('<structured-output>{"fear":10,"trust":20}</structured-output>', Emotion)
    assert fenced.status == blocked.status == "valid"
    assert fenced.diagnostics.envelope_extracted and blocked.diagnostics.envelope_extracted
    assert repair_spy == []


@pytest.mark.parametrize("raw", [
    '{"fear":"terrified","trust":20}',
    '{"fear":10,"trust":20,"unknown":1}',
    '{"fear":10}',
    '{"fear":10,"trust":20,"mood":"angry"}',
    '{"fear":800,"trust":20}',
])
def test_valid_semantic_failures_are_immediate(raw, repair_spy):
    result = validate_structured_output(raw, Emotion)
    assert result.status == "schema_validation_failed"
    assert result.failure_category == "schema"
    assert repair_spy == []


def test_domain_semantics_never_call_repair(repair_spy):
    result = validate_structured_output('{"before":5,"delta":2,"after":99}', Arithmetic)
    assert result.status == "schema_validation_failed"
    assert repair_spy == []


@pytest.mark.parametrize("raw", [
    '{"fear": "80", "trust": 20',
    '{"fear": 800, "trust": 20',
    '{"fear": 80, "database_table": "characters"',
])
def test_second_validation_failure_rejects_without_second_repair(raw, repair_spy):
    result = validate_structured_output(raw, Emotion)
    assert result.status == "schema_validation_failed"
    assert result.repair_attempted and not result.repair_succeeded
    assert len(repair_spy) == 1


@pytest.mark.parametrize("raw", [
    '{"fear":10,"trust":20} {"fear":30,"trust":40}',
    '{"fear":10,"fear":20,"trust":30}',
    'prose {"fear":10,"trust":20}',
    '```python\n{"fear":10,"trust":20}\n```',
    '', '   ', '{"fear":NaN,"trust":20}', '{"fear":Infinity,"trust":20}',
    '{"fear":10,"trust":20,"__class__":"attack"}',
    '{"fear":10,"trust":20,"note":"\u0001"}',
])
def test_ambiguous_and_dangerous_inputs_fail_closed(raw, repair_spy):
    result = validate_structured_output(raw, Emotion)
    assert not result.accepted
    assert len(repair_spy) <= 1


@pytest.mark.parametrize("raw", [
    '{"fear":10\n{"fear":30,"trust":40}',
    '{"fear":10,"trust":20} [30,40]',
])
def test_lexical_multiple_values_reject_before_repair(raw, repair_spy):
    result = validate_structured_output(raw, Emotion)
    assert result.status == "input_rejected"
    assert repair_spy == []




@pytest.mark.parametrize("raw", [
    "tru fals",
    'nul {"value": 1}',
    "12 13",
    '"a" "b"',
    '12e {"fear":10,"trust":20}',
    'nul [1,2]',
])
def test_malformed_scalar_multiple_values_reject_before_repair(raw, repair_spy):
    result = validate_structured_output(raw, Emotion)
    assert result.status == "input_rejected"
    assert repair_spy == []


def test_limits_and_non_object_top_level(repair_spy):
    small = StructuredOutputPolicy(max_input_bytes=32, max_repair_input_bytes=32)
    assert validate_structured_output('{"fear":10,"trust":20,"mood":"calm"}', Emotion, small).status == "input_rejected"
    deep = StructuredOutputPolicy(max_nesting_depth=2)
    assert validate_structured_output('{"state":{"a":{"b":1}}}', GenericState, deep).status == "input_rejected"
    assert validate_structured_output('[10,20]', Emotion).status == "schema_validation_failed"


@pytest.mark.parametrize("replacement", [
    lambda raw: (_ for _ in ()).throw(ImportError("missing")),
    lambda raw: (_ for _ in ()).throw(RuntimeError("broken")),
    lambda raw: "",
    lambda raw: {"fear": 10},
])
def test_library_failures_are_controlled(monkeypatch, replacement):
    monkeypatch.setattr(recovery, "_repair_json_once", replacement)
    result = validate_structured_output('{"fear":10 "trust":20}', Emotion)
    assert result.status == "syntax_repair_failed"
    assert result.validated_model is None


@pytest.mark.parametrize("model,raw", [
    (StorytellerOutput, '{"narrative":"hello","state_update":{},"extra":1}'),
    (StatePatch, '{"target":{},"operations":[],"idempotency_key":"bad"}'),
    (ConversationalFactCandidate, '{"character_id":1,"text":"fact","fact_type":"rumor_fact"}'),
    (MajorEvent, '{"text":"event","event_type":"Bad Event"}'),
    (Dnd5eHpDamageUpdate, '{"operation":"dnd5e.hp_damage","producer_id":"x","amount":2,"hp_before":5,"hp_after":4,"hp_max":10}'),
])
def test_real_contract_semantic_failures_do_not_repair(model, raw, repair_spy):
    assert validate_structured_output(raw, model).status == "schema_validation_failed"
    assert repair_spy == []


def test_rejected_output_cannot_reach_fake_persistence(repair_spy):
    persisted = []
    result = validate_structured_output('{"fear":800,"trust":20}', Emotion)
    if result.accepted:
        persisted.append(result.validated_model)
    assert persisted == []
    assert result.validated_model is None


def test_policy_defaults_match_engine_configuration():
    configured = EngineConfig().structured_output_recovery
    policy = StructuredOutputPolicy.from_config(configured)
    assert policy.enabled and policy.library == "json_repair" and policy.max_attempts == 1
    assert policy.reject_duplicate_keys and policy.reject_multiple_objects
    assert not policy.secure_debug_raw_output and policy.fail_closed_for_authoritative_state


@pytest.mark.parametrize("change", [
    {"max_attempts": 2}, {"library": "other"}, {"max_input_bytes": 0},
    {"max_repair_input_bytes": 20, "max_input_bytes": 10},
    {"max_error_summary_characters": 0}, {"fail_closed_for_authoritative_state": False},
    {"reject_duplicate_keys": False}, {"reject_multiple_objects": False},
])
def test_invalid_engine_configuration_is_rejected_immediately(change):
    from config import StructuredOutputRecoveryConfig
    with pytest.raises(ValueError):
        StructuredOutputRecoveryConfig(**change)
