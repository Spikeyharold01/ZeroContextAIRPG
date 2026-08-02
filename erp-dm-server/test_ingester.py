import pytest

from ingester import Ingester


def marked_payload(**overrides):
    payload = {
        "messages": [
            {
                "role": "system",
                "content": "=CHARACTER CARD=\nName: Tanis Half-Elven\nA wary half-elf.",
            },
            {
                "role": "system",
                "content": "=SYSTEM PROMPT=\nRemain in character.",
            },
            {
                "role": "system",
                "content": "=SCENARIO=\nA ruined bridge at dusk.",
            },
            {
                "role": "system",
                "content": "=EXAMPLES=\nUser: Who goes there?\nTanis: Show yourself.",
            },
            {
                "role": "system",
                "content": "=USER=\nName: Flint\nClass: Fighter",
            },
            {"role": "user", "content": "Do you remember this place?"},
        ],
        "temperature": 0.7,
        "max_tokens": 450,
    }
    payload.update(overrides)
    return payload


def test_ingester_extracts_marked_sections_and_request_values():
    context = Ingester().ingest(marked_payload())

    assert context.character_name == "Tanis Half-Elven"
    assert context.character_card_text == "Name: Tanis Half-Elven\nA wary half-elf."
    assert context.system_prompt == "Remain in character."
    assert context.scenario == "A ruined bridge at dusk."
    assert context.examples.startswith("User: Who goes there?")
    assert context.user_character == "Name: Flint\nClass: Fighter"
    assert context.user_message == "Do you remember this place?"
    assert context.sampling_params["temperature"] == 0.7
    assert context.sampling_params["max_tokens"] == 450
    assert context.is_first_message is True


def test_ingester_rejects_payload_without_character_card_marker():
    payload = marked_payload()
    payload["messages"] = [
        message
        for message in payload["messages"]
        if not message.get("content", "").startswith("=CHARACTER CARD=")
    ]

    with pytest.raises(ValueError, match="Required section '=CHARACTER CARD=' not found"):
        Ingester().ingest(payload)


def test_ingester_keeps_prior_exchange_as_history():
    payload = marked_payload()
    payload["messages"][-1:-1] = [
        {"role": "user", "content": "We crossed it before."},
        {"role": "assistant", "content": "And barely survived."},
    ]

    context = Ingester().ingest(payload)

    assert context.is_first_message is False
    assert {"role": "assistant", "content": "And barely survived."} in context.chat_history
    assert context.user_message == "Do you remember this place?"
