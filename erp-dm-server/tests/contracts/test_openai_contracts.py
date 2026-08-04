from pydantic import TypeAdapter, ValidationError
import pytest

from contracts.openai import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    NonStreamingChatCompletionRequest,
    StreamingChatCompletionRequest,
    StringChatMessage,
)


def request(**updates):
    value = {
        "model": "proxy",
        "messages": [{"role": "user", "content": "Inspect the lock."}],
    }
    value.update(updates)
    return value


def test_valid_string_messages_and_unknown_top_level_option():
    model = NonStreamingChatCompletionRequest.model_validate({
        **request(),
        "provider_extension": "ignored",
    })
    assert model.messages[0].content == "Inspect the lock."
    assert not hasattr(model, "provider_extension")


@pytest.mark.parametrize(
    "message",
    [
        {"role": "user", "content": [{"type": "text", "text": "Hi"}]},
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]},
        {"role": "user", "content": [{"type": "audio", "audio": {"data": "x"}}]},
        {"role": "tool", "content": "result"},
        {"role": "developer", "content": "instruction"},
        {"role": "assistant", "content": "x", "tool_calls": []},
        {"role": "assistant", "content": "x", "tool_call_id": "1"},
        {"role": "assistant", "content": "x", "function_call": {}},
        {"role": "user", "content": "x", "unknown": True},
    ],
)
def test_unsupported_message_shapes_are_rejected(message):
    with pytest.raises(ValidationError):
        NonStreamingChatCompletionRequest.model_validate(request(messages=[message]))


def test_streaming_and_non_streaming_requests_are_distinct():
    assert StreamingChatCompletionRequest.model_validate(request(stream=True)).stream is True
    assert NonStreamingChatCompletionRequest.model_validate(request()).stream is False
    with pytest.raises(ValidationError):
        StreamingChatCompletionRequest.model_validate(request(stream=False))
    with pytest.raises(ValidationError):
        NonStreamingChatCompletionRequest.model_validate(request(stream=True))

    adapter = TypeAdapter(ChatCompletionRequest)
    streaming = adapter.validate_python(request(stream=True))
    assert isinstance(streaming, StreamingChatCompletionRequest)
    assert isinstance(adapter.validate_python(request(stream=False)), NonStreamingChatCompletionRequest)
    assert isinstance(adapter.validate_json(adapter.dump_json(streaming)), StreamingChatCompletionRequest)
    schema = adapter.json_schema()
    assert schema["discriminator"]["propertyName"] == "stream"


def test_token_totals_are_validated():
    valid = {
        "id": "chatcmpl-1",
        "created": 1,
        "model": "proxy",
        "choices": [{
            "index": 0,
            "message": {"content": "Done."},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
    }
    assert ChatCompletionResponse.model_validate(valid).usage.total_tokens == 12
    valid["usage"]["total_tokens"] = 11
    with pytest.raises(ValidationError, match="total_tokens"):
        ChatCompletionResponse.model_validate(valid)


def test_hidden_state_cannot_appear_in_stream_delta():
    with pytest.raises(ValidationError):
        ChatCompletionChunk.model_validate({
            "id": "chatcmpl-1",
            "created": 1,
            "model": "proxy",
            "choices": [{
                "index": 0,
                "delta": {"content": "Visible", "state_update": {"secret": True}},
                "finish_reason": None,
            }],
        })


def test_strict_json_and_python_behavior():
    payload = '{"model":"proxy","messages":[{"role":"user","content":"Hi"}],"stream":true,"max_tokens":600,"temperature":0.85}'
    assert StreamingChatCompletionRequest.model_validate_json(payload).max_tokens == 600
    assert StreamingChatCompletionRequest.model_validate(request(
        stream=True, max_tokens=600, temperature=0.85
    )).max_tokens == 600
    with pytest.raises(ValidationError):
        StreamingChatCompletionRequest.model_validate(request(stream=True, max_tokens="600"))
    with pytest.raises(ValidationError):
        StreamingChatCompletionRequest.model_validate_json(
            '{"model":"proxy","messages":[{"role":"user","content":"Hi"}],"stream":"true"}'
        )
