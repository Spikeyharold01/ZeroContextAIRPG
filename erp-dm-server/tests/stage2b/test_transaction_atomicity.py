import json
import sqlite3

import pytest

from contracts.openai import NonStreamingChatCompletionRequest
from proxy_server.errors import TurnError
from proxy_server.services.conversation_turn_service import ConversationTurnService
from proxy_server.services.storyteller import DeterministicMockStoryteller
from proxy_server.services.structured_tail import END, START, extract_tail


POINTS = ["first_state_patch", "state_patch_audit", "state_idempotency", "projection_update",
          "story_time_write", "memory_write", "dialogue_focus_write", "conversation_turn_commit",
          "user_message", "assistant_message", "campaign_conversation_turn", "completed_response"]


def request(content):
    return NonStreamingChatCompletionRequest.model_validate(
        {"model":"mock","messages":[{"role":"user","content":content}],"stream":False})


def authoritative_snapshot(session):
    conn = sqlite3.connect(session.database_path)
    result = {
        "turn": conn.execute("SELECT current_turn FROM campaigns").fetchone()[0],
        "documents": conn.execute("SELECT id,state_json,revision,content_hash FROM state_documents ORDER BY id").fetchall(),
        "patch_log": conn.execute("SELECT * FROM state_patch_log ORDER BY id").fetchall(),
        "state_idempotency": conn.execute("SELECT * FROM state_idempotency ORDER BY idempotency_key").fetchall(),
        "projections": conn.execute("SELECT * FROM state_projection_values ORDER BY state_document_id,projection_id").fetchall(),
        "commits": conn.execute("SELECT * FROM conversation_turn_commits ORDER BY id").fetchall(),
        "messages": conn.execute("SELECT * FROM conversation_turn_messages ORDER BY id").fetchall(),
    }
    conn.close(); return result


@pytest.mark.parametrize("point", POINTS)
def test_injected_failure_rolls_back_every_authoritative_effect(rules_free_campaign, point):
    before = authoritative_snapshot(rules_free_campaign)
    def inject(actual):
        if actual == point: raise RuntimeError(f"injected:{point}")
    content = "Sleep __TEST_TRIGGER_STORY_TIME_SLEEP__" if point == "story_time_write" else "Hello"
    service = ConversationTurnService(rules_free_campaign, DeterministicMockStoryteller(), failure_injector=inject)
    with pytest.raises(TurnError) as captured:
        service.complete(request(content), f"failure-{point}")
    assert captured.value.code == "persistence_failure"
    assert authoritative_snapshot(rules_free_campaign) == before
    conn = sqlite3.connect(rules_free_campaign.database_path)
    status, response = conn.execute("SELECT status,response_json FROM conversation_turn_requests").fetchone()
    conn.close()
    assert status == "failed" and response is None


def test_second_patch_failure_rolls_back_first_patch(rules_free_campaign):
    class SecondPatchStale(DeterministicMockStoryteller):
        def tell(self, prompt, raw, context):
            block = extract_tail(super().tell(prompt, raw, context), request_id="test")
            payload = json.loads(block.payload)
            payload["state_update"]["state_patches"][1]["base_revision"] = 999
            return f"{block.narrative}\n{START}\n{json.dumps(payload)}\n{END}"
    before = authoritative_snapshot(rules_free_campaign)
    with pytest.raises(TurnError) as captured:
        ConversationTurnService(rules_free_campaign, SecondPatchStale()).complete(request("Hello"), "multi")
    assert captured.value.code == "state_patch_conflict"
    assert authoritative_snapshot(rules_free_campaign) == before


@pytest.mark.parametrize("namespace,content", [
    ("narrative.memory", "Hello"),
    ("narrative.dialogue", "Hello"),
    ("narrative.time", "Sleep __TEST_TRIGGER_STORY_TIME_SLEEP__"),
    ("narrative.location", "Village __TEST_TRIGGER_MOVE_SUCCEEDS__"),
])
def test_stale_document_revision_rolls_back_whole_turn(rules_free_campaign, namespace, content):
    class StaleTarget(DeterministicMockStoryteller):
        def tell(self, prompt, raw, context):
            block = extract_tail(super().tell(prompt, raw, context), request_id="test")
            payload = json.loads(block.payload)
            target = next(patch for patch in payload["state_update"]["state_patches"]
                          if patch["target"]["namespace"] == namespace)
            target["base_revision"] = 999
            return f"{block.narrative}\n{START}\n{json.dumps(payload)}\n{END}"
    before = authoritative_snapshot(rules_free_campaign)
    with pytest.raises(TurnError):
        ConversationTurnService(rules_free_campaign, StaleTarget()).complete(request(content), f"stale-{namespace}")
    assert authoritative_snapshot(rules_free_campaign) == before
