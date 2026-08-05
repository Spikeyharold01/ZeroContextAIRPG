"""Integration checks against the real installed json-repair distribution."""

import importlib.metadata
import json

import pytest
import json_repair
from pydantic import BaseModel, ConfigDict, Field, RootModel

from structured_output import validate_structured_output


class Emotion(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    fear: int = Field(ge=0, le=100)
    trust: int = Field(ge=0, le=100)


class Emotions(RootModel[list[Emotion]]):
    pass


@pytest.mark.parametrize("raw,model", [
    ('{"fear":80 "trust":20}', Emotion),
    ('{"fear":80,"trust":20,}', Emotion),
    ('{"fear":80,"trust":20', Emotion),
    ('[{"fear":80,"trust":20}', Emotions),
    ("{'fear':80,'trust':20}", Emotion),
])
def test_real_json_repair_api_and_contract(raw, model):
    repaired = json_repair.repair_json(raw, return_objects=False)
    assert isinstance(repaired, str)
    json.loads(repaired)
    result = validate_structured_output(raw, model)
    assert result.status == "repaired"
    assert result.validated_model is not None
    assert result.diagnostics.repair_attempts == 1


def test_real_library_with_markdown_envelope_needs_no_repair():
    result = validate_structured_output('```json\n{"fear":80,"trust":20}\n```', Emotion)
    assert result.status == "valid"
    assert result.diagnostics.envelope_extracted
    assert result.diagnostics.repair_attempts == 0


def test_real_library_is_called_at_most_once(monkeypatch):
    original = json_repair.repair_json
    calls = []
    monkeypatch.setattr(json_repair, "repair_json", lambda *args, **kwargs: calls.append(args[0]) or original(*args, **kwargs))
    assert validate_structured_output('{"fear":80 "trust":20}', Emotion).status == "repaired"
    assert len(calls) == 1


def test_real_library_duplicate_keys_are_rejected_without_acceptance():
    result = validate_structured_output('{"fear":80,"fear":20,"trust":20}', Emotion)
    assert result.status == "input_rejected"
    assert result.validated_model is None


def test_real_library_multiple_values_reject_before_repair(monkeypatch):
    calls = []
    original = json_repair.repair_json
    monkeypatch.setattr(json_repair, "repair_json", lambda *args, **kwargs: calls.append(args[0]) or original(*args, **kwargs))
    result = validate_structured_output('{"fear":80,"trust":20} {"fear":10,"trust":10}', Emotion)
    assert result.status == "input_rejected"
    assert result.validated_model is None
    assert calls == []


def test_real_library_semantic_invalid_output_is_rejected_without_repair(monkeypatch):
    calls = []
    original = json_repair.repair_json
    monkeypatch.setattr(json_repair, "repair_json", lambda *args, **kwargs: calls.append(args[0]) or original(*args, **kwargs))
    result = validate_structured_output('{"fear":800,"trust":20}', Emotion)
    assert result.status == "schema_validation_failed"
    assert result.validated_model is None
    assert calls == []


def test_installed_distribution_is_within_selected_series():
    version = importlib.metadata.version("json-repair")
    major, minor, *_ = (int(part) for part in version.split(".") if part.isdigit())
    assert major == 0 and 39 <= minor < 60
