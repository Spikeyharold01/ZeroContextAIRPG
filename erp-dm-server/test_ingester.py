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


def test_character_card_without_optional_sections_is_accepted():
    payload = {
        "messages": [
            {"role": "system", "content": "=CHARACTER CARD=\nName: Kira"},
            {"role": "user", "content": "Hello."},
        ]
    }

    context = Ingester().ingest(payload)

    assert context.character_card_text == "Name: Kira"
    assert context.system_prompt == ""
    assert context.scenario == ""
    assert context.examples == ""
    assert context.user_character == ""


@pytest.mark.parametrize(
    "content",
    [
        "  =CHARACTER CARD=\nName: Indented",
        "Introduction first.\n=CHARACTER CARD=\nName: Prefaced",
    ],
)
def test_character_card_marker_must_be_at_character_zero(content):
    payload = {
        "messages": [
            {"role": "system", "content": content},
            {"role": "user", "content": "Hello."},
        ]
    }

    with pytest.raises(ValueError, match="Required section '=CHARACTER CARD=' not found"):
        Ingester().ingest(payload)


def test_only_leading_marker_is_interpreted_in_one_system_message():
    payload = {
        "messages": [
            {
                "role": "system",
                "content": (
                    "=CHARACTER CARD=\nName: Kira\n"
                    "=SCENARIO=\nThis remains character-card text."
                ),
            },
            {"role": "user", "content": "Hello."},
        ]
    }

    context = Ingester().ingest(payload)

    assert context.character_card_text == (
        "Name: Kira\n=SCENARIO=\nThis remains character-card text."
    )
    assert context.scenario == ""


def test_repeated_markers_use_the_last_section():
    payload = {
        "messages": [
            {"role": "system", "content": "=CHARACTER CARD=\nName: First"},
            {"role": "system", "content": "=CHARACTER CARD=\nName: Final"},
            {"role": "system", "content": "=SCENARIO=\nOld scenario"},
            {"role": "system", "content": "=SCENARIO=\nFinal scenario"},
            {"role": "user", "content": "Hello."},
        ]
    }

    context = Ingester().ingest(payload)

    assert context.character_card_text == "Name: Final"
    assert context.character_name == "Final"
    assert context.scenario == "Final scenario"


def test_empty_optional_sections_are_accepted():
    payload = {
        "messages": [
            {"role": "system", "content": "=CHARACTER CARD=\nName: Kira"},
            {"role": "system", "content": "=SYSTEM PROMPT=   \n"},
            {"role": "system", "content": "=SCENARIO="},
            {"role": "system", "content": "=EXAMPLES=\n\n"},
            {"role": "system", "content": "=USER=  "},
            {"role": "user", "content": "Hello."},
        ]
    }

    context = Ingester().ingest(payload)

    assert context.system_prompt == ""
    assert context.scenario == ""
    assert context.examples == ""
    assert context.user_character == ""


def test_empty_character_card_is_rejected():
    payload = {
        "messages": [
            {"role": "system", "content": "=CHARACTER CARD=  \n"},
            {"role": "user", "content": "Hello."},
        ]
    }

    with pytest.raises(ValueError, match="Required section '=CHARACTER CARD=' not found"):
        Ingester().ingest(payload)


@pytest.mark.parametrize("content", [None, 42, ["structured"], {"text": "structured"}])
def test_non_string_or_structured_message_content_is_rejected(content):
    payload = {
        "messages": [
            {"role": "system", "content": "=CHARACTER CARD=\nName: Kira"},
            {"role": "user", "content": content},
        ]
    }

    with pytest.raises(ValueError, match=r"messages\[1\]\.content must be a string"):
        Ingester().ingest(payload)


def test_several_prior_exchanges_are_preserved_and_final_user_is_selected():
    messages = [
        {"role": "system", "content": "=CHARACTER CARD=\nName: Kira"},
        {"role": "user", "content": "First question"},
        {"role": "assistant", "content": "First answer"},
        {"role": "user", "content": "Second question"},
        {"role": "assistant", "content": "Second answer"},
        {"role": "user", "content": "Final question"},
    ]

    context = Ingester().ingest({"messages": messages})

    assert context.user_message == "Final question"
    assert context.chat_history == messages[:-1]
    assert context.is_first_message is False


def test_final_user_is_selected_even_when_followed_by_non_user_message():
    trailing_system = {"role": "system", "content": "Unmarked trailing note"}
    payload = {
        "messages": [
            {"role": "system", "content": "=CHARACTER CARD=\nName: Kira"},
            {"role": "user", "content": "Earlier question"},
            {"role": "assistant", "content": "Earlier answer"},
            {"role": "user", "content": "Final user question"},
            trailing_system,
        ]
    }

    context = Ingester().ingest(payload)

    assert context.user_message == "Final user question"
    assert {"role": "user", "content": "Earlier question"} in context.chat_history
    assert trailing_system in context.chat_history
    assert {"role": "user", "content": "Final user question"} not in context.chat_history


def test_is_first_message_is_true_without_prior_assistant_message():
    payload = {
        "messages": [
            {"role": "system", "content": "=CHARACTER CARD=\nName: Kira"},
            {"role": "user", "content": "Only user request"},
        ]
    }

    assert Ingester().ingest(payload).is_first_message is True
