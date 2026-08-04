from uuid import UUID, uuid4

from pydantic import ValidationError
import pytest

from contracts.ingestion import ParsedIngestedContext, RecentExchange, ResolvedTurnContext


def parsed(**updates):
    values = {
        "request_id": uuid4(),
        "user_message": "Inspect it.",
        "character_card_text": "Name: Mara",
        "is_first_message": True,
    }
    values.update(updates)
    return ParsedIngestedContext.model_validate(values)


def test_parsed_context_needs_no_trusted_character_id_and_hint_is_optional():
    context = parsed()
    assert context.character_id_hint is None
    hinted = parsed(character_id_hint=12)
    assert hinted.character_id_hint == 12
    assert "character_id" not in type(hinted).model_fields


def test_duplicate_current_message_is_rejected():
    with pytest.raises(ValidationError, match="duplicate"):
        parsed(raw_chat_history=[{"role": "user", "content": "Inspect it."}])


def test_raw_payload_is_excluded_from_serialization():
    context = parsed(raw_payload={"api_key": "secret"})
    assert "raw_payload" not in context.model_dump()
    assert "raw_payload" not in context.model_dump_json()


def test_resolved_context_requires_positive_active_id_and_bounded_history():
    context = parsed()
    resolved = ResolvedTurnContext.model_validate({
        "request_id": context.request_id,
        "parsed": context,
        "character_id": 12,
        "character_name": "Mara",
        "entity_kind": "core.character",
        "control_type": "core.non-player",
        "character_is_active": True,
        "history_exchange_limit": 1,
        "recent_exchanges": [{"user": "Hello", "assistant": "Welcome"}],
    })
    assert resolved.character_id == 12
    for field, value in (("character_id", 0), ("character_is_active", False)):
        data = resolved.model_dump()
        data[field] = value
        with pytest.raises(ValidationError):
            ResolvedTurnContext.model_validate(data)
    data = resolved.model_dump()
    data["recent_exchanges"] *= 2
    with pytest.raises(ValidationError, match="history_exchange_limit"):
        ResolvedTurnContext.model_validate(data)


def test_uuid_strict_mode_differs_for_json_and_python():
    value = "ad6c304b-326d-45f3-a699-a004c1407dda"
    payload = (
        '{"request_id":"' + value + '","user_message":"Hi",'
        '"character_card_text":"Name: Mara","is_first_message":true}'
    )
    assert isinstance(ParsedIngestedContext.model_validate_json(payload).request_id, UUID)
    with pytest.raises(ValidationError):
        ParsedIngestedContext.model_validate({
            "request_id": value,
            "user_message": "Hi",
            "character_card_text": "Name: Mara",
            "is_first_message": True,
        })
    assert parsed(request_id=UUID(value)).request_id == UUID(value)
