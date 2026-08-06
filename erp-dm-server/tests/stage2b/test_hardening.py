import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from contracts.openai import NonStreamingChatCompletionRequest
from proxy_server.app import create_app
from proxy_server.errors import TurnError
from proxy_server.services.context_retrieval import retrieve_context
from proxy_server.services.conversation_turn_service import ConversationTurnService, canonical_request_hash
from proxy_server.services.storyteller import DeterministicMockStoryteller


def request(content="Hello"):
    return NonStreamingChatCompletionRequest.model_validate(
        {"model": "stage2b-mock", "messages": [{"role": "user", "content": content}], "stream": False})


def context_for(session, content="Hello"):
    return retrieve_context(str(session.database_path), session.campaign_id, content, "review")


def row(session, key):
    conn = sqlite3.connect(session.database_path); conn.row_factory = sqlite3.Row
    value = conn.execute("SELECT * FROM conversation_turn_requests WHERE idempotency_key=?", (key,)).fetchone()
    conn.close(); return value


def test_raw_message_whitespace_is_exact_in_storyteller_prompt_hash_and_storage(rules_free_campaign):
    raw = "  line one\n\t```text\n  quoted  dialogue\n```\u2003 "
    class Capture(DeterministicMockStoryteller):
        seen = None
        prompt = None
        def tell(self, prompt, raw_user_message, context):
            self.seen, self.prompt = raw_user_message, prompt
            return super().tell(prompt, raw_user_message, context)
    storyteller = Capture()
    response = TestClient(create_app(rules_free_campaign, storyteller)).post(
        "/v1/chat/completions", headers={"X-Idempotency-Key": "raw"},
        json={"model": "stage2b-mock", "messages": [{"role": "user", "content": raw}], "stream": False})
    assert response.status_code == 200
    assert storyteller.seen == raw and raw in storyteller.prompt
    conn = sqlite3.connect(rules_free_campaign.database_path)
    assert conn.execute("SELECT content FROM conversation_turn_messages WHERE role='user'").fetchone()[0] == raw
    conn.close()
    assert canonical_request_hash(request(" Hello")) != canonical_request_hash(request("Hello "))


def test_whitespace_only_message_rejected(rules_free_campaign):
    response = TestClient(create_app(rules_free_campaign)).post("/v1/chat/completions", json={
        "model": "mock", "messages": [{"role": "user", "content": "\t \u2003"}], "stream": False})
    assert response.status_code == 422


def test_unexpired_then_expired_reservation_reclaim(rules_free_campaign):
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    service = ConversationTurnService(rules_free_campaign, DeterministicMockStoryteller(), clock=lambda: now)
    req = request(); context = context_for(rules_free_campaign); digest = canonical_request_hash(req)
    assert service._reserve_or_replay("lease", digest, "first", context, True).reservation.attempt_number == 1
    with pytest.raises(TurnError) as active:
        service._reserve_or_replay("lease", digest, "second", context, True)
    assert active.value.code == "request_in_progress" and active.value.retryable
    conn = sqlite3.connect(rules_free_campaign.database_path)
    conn.execute("UPDATE conversation_turn_requests SET lease_expires_at=? WHERE idempotency_key='lease'",
                 ((now - timedelta(seconds=1)).isoformat(),)); conn.commit(); conn.close()
    assert service._reserve_or_replay("lease", digest, "third", context, True).reservation.attempt_number == 2
    assert row(rules_free_campaign, "lease")["attempt_number"] == 2


@pytest.mark.parametrize("retryable,expected", [(1, None), (0, "duplicate_request_conflict")])
def test_failed_reservation_retry_policy(rules_free_campaign, retryable, expected):
    service = ConversationTurnService(rules_free_campaign, DeterministicMockStoryteller())
    req = request(); context = context_for(rules_free_campaign); digest = canonical_request_hash(req)
    service._reserve_or_replay("failed", digest, "first", context, True)
    conn = sqlite3.connect(rules_free_campaign.database_path)
    conn.execute("UPDATE conversation_turn_requests SET status='failed',retryable=?,lease_expires_at=NULL WHERE idempotency_key='failed'", (retryable,))
    conn.commit(); conn.close()
    if expected:
        with pytest.raises(TurnError) as captured:
            service._reserve_or_replay("failed", digest, "second", context, True)
        assert captured.value.code == expected
    else:
        assert service._reserve_or_replay("failed", digest, "second", context, True).reservation.attempt_number == 2
        assert row(rules_free_campaign, "failed")["attempt_number"] == 2


def test_expired_reclaim_is_atomic_between_two_threads(rules_free_campaign):
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    service = ConversationTurnService(rules_free_campaign, DeterministicMockStoryteller(), clock=lambda: now)
    req = request(); context = context_for(rules_free_campaign); digest = canonical_request_hash(req)
    service._reserve_or_replay("crash", digest, "original", context, True)
    conn = sqlite3.connect(rules_free_campaign.database_path)
    conn.execute("UPDATE conversation_turn_requests SET lease_expires_at=? WHERE idempotency_key='crash'",
                 ((now - timedelta(seconds=1)).isoformat(),)); conn.commit(); conn.close()
    results = []
    def reclaim(name):
        try: results.append(service._reserve_or_replay("crash", digest, name, context, True).reservation)
        except TurnError as error: results.append(error.code)
    threads = [threading.Thread(target=reclaim, args=(name,)) for name in ("a", "b")]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert sum(value is not None and not isinstance(value, str) for value in results) == 1
    assert results.count("request_in_progress") == 1
    assert row(rules_free_campaign, "crash")["attempt_number"] == 2


def test_expired_or_failed_row_with_different_hash_conflicts(rules_free_campaign):
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    service = ConversationTurnService(rules_free_campaign, DeterministicMockStoryteller(), clock=lambda: now)
    context = context_for(rules_free_campaign); first = canonical_request_hash(request("First"))
    service._reserve_or_replay("different", first, "one", context, True)
    conn = sqlite3.connect(rules_free_campaign.database_path)
    conn.execute("UPDATE conversation_turn_requests SET lease_expires_at=? WHERE idempotency_key='different'",
                 ((now - timedelta(seconds=1)).isoformat(),)); conn.commit(); conn.close()
    with pytest.raises(TurnError) as captured:
        service._reserve_or_replay("different", canonical_request_hash(request("Second")), "two", context, True)
    assert captured.value.code == "duplicate_request_conflict"


def test_database_busy_failed_reservation_is_retryable(rules_free_campaign):
    service = ConversationTurnService(rules_free_campaign, DeterministicMockStoryteller())
    req = request(); context = context_for(rules_free_campaign); digest = canonical_request_hash(req)
    service._reserve_or_replay("busy-retry", digest, "one", context, True)
    conn = sqlite3.connect(rules_free_campaign.database_path)
    conn.execute("UPDATE conversation_turn_requests SET status='failed',retryable=1,error_code='database_busy',lease_expires_at=NULL "
                 "WHERE idempotency_key='busy-retry'"); conn.commit(); conn.close()
    assert service._reserve_or_replay("busy-retry", digest, "two", context, True).reservation.attempt_number == 2
    assert row(rules_free_campaign, "busy-retry")["attempt_number"] == 2


def test_timeout_failed_row_can_retry_same_client_key(rules_free_campaign):
    class Once(DeterministicMockStoryteller):
        calls = 0
        def tell(self, *args):
            self.calls += 1
            if self.calls == 1: raise TimeoutError("once")
            return super().tell(*args)
    client = TestClient(create_app(rules_free_campaign, Once()))
    payload = {"model": "mock", "messages": [{"role": "user", "content": "Hello"}], "stream": False}
    assert client.post("/v1/chat/completions", headers={"X-Idempotency-Key": "retry"}, json=payload).status_code == 504
    assert client.post("/v1/chat/completions", headers={"X-Idempotency-Key": "retry"}, json=payload).status_code == 200
    assert row(rules_free_campaign, "retry")["attempt_number"] == 2


def test_reclaimed_lease_rejects_stale_owner_and_current_owner_commits(rules_free_campaign):
    """A/attempt-1 cannot commit after B reclaims attempt-2."""
    entered_a = threading.Event(); entered_b = threading.Event()
    release_a = threading.Event(); release_b = threading.Event()

    class Coordinated(DeterministicMockStoryteller):
        def __init__(self, entered, release):
            self.entered, self.release = entered, release
        def tell(self, *args):
            self.entered.set()
            assert self.release.wait(5)
            return super().tell(*args)

    request_value = request("Hello")
    conn = sqlite3.connect(rules_free_campaign.database_path)
    documents_before = conn.execute(
        "SELECT id,state_json,revision,content_hash FROM state_documents ORDER BY id").fetchall()
    projections_before = conn.execute(
        "SELECT * FROM state_projection_values ORDER BY state_document_id,projection_id").fetchall()
    conn.close()
    first = ConversationTurnService(rules_free_campaign, Coordinated(entered_a, release_a), lease_seconds=0)
    second = ConversationTurnService(rules_free_campaign, Coordinated(entered_b, release_b), lease_seconds=30)
    results = {}

    def run(name, service):
        try:
            results[name] = service.complete(request_value, "lease-race")
        except TurnError as error:
            results[name] = error

    thread_a = threading.Thread(target=run, args=("a", first)); thread_a.start()
    assert entered_a.wait(5)
    thread_b = threading.Thread(target=run, args=("b", second)); thread_b.start()
    assert entered_b.wait(5)

    release_a.set(); thread_a.join(5)
    assert isinstance(results["a"], TurnError)
    assert results["a"].code == "request_lease_lost"
    conn = sqlite3.connect(rules_free_campaign.database_path)
    assert conn.execute("SELECT current_turn FROM campaigns").fetchone()[0] == 0
    for table in ("conversation_turn_commits", "conversation_turn_messages", "state_patch_log", "state_idempotency"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert conn.execute("SELECT id,state_json,revision,content_hash FROM state_documents ORDER BY id").fetchall() == documents_before
    assert conn.execute("SELECT * FROM state_projection_values ORDER BY state_document_id,projection_id").fetchall() == projections_before
    assert conn.execute("SELECT status,attempt_number,last_request_id,response_json FROM conversation_turn_requests").fetchone()[:2] == ("in_progress", 2)
    conn.close()

    release_b.set(); thread_b.join(5)
    assert not thread_b.is_alive() and not isinstance(results["b"], Exception)
    conn = sqlite3.connect(rules_free_campaign.database_path)
    status, attempts, owner, response_json = conn.execute(
        "SELECT status,attempt_number,last_request_id,response_json FROM conversation_turn_requests").fetchone()
    assert status == "completed" and attempts == 2 and response_json
    assert results["b"].id.endswith(owner)
    assert conn.execute("SELECT current_turn FROM campaigns").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM conversation_turn_commits").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM conversation_turn_messages").fetchone()[0] == 2
    conn.close()


def test_context_integrity_error_is_safe(rules_free_campaign):
    conn = sqlite3.connect(rules_free_campaign.database_path)
    conn.execute("UPDATE state_documents SET state_json='not-json' WHERE id='memory'"); conn.commit(); conn.close()
    response = TestClient(create_app(rules_free_campaign)).post("/v1/chat/completions", json={
        "model": "mock", "messages": [{"role": "user", "content": "Hello"}], "stream": False})
    assert response.status_code == 500
    body = response.json()["error"]
    assert body["code"] == "unresolved_campaign_context" and body["request_id"] and not body["retryable"]
    assert "not-json" not in response.text and rules_free_campaign.campaign_id not in response.text


def test_zero_and_ambiguous_active_players_are_safe(rules_free_campaign):
    client = TestClient(create_app(rules_free_campaign))
    conn = sqlite3.connect(rules_free_campaign.database_path)
    conn.execute("UPDATE characters SET is_active=0 WHERE type='PC'"); conn.commit(); conn.close()
    zero = client.post("/v1/chat/completions", json={"model":"m","messages":[{"role":"user","content":"Hi"}],"stream":False})
    assert zero.json()["error"]["code"] == "no_active_player_entity"
    conn = sqlite3.connect(rules_free_campaign.database_path)
    conn.execute("UPDATE characters SET is_active=1 WHERE id=1")
    conn.execute("INSERT INTO characters(name,type,status,is_active,current_location_id) VALUES('Other','PC','active',1,1)")
    conn.commit(); conn.close()
    ambiguous = client.post("/v1/chat/completions", json={"model":"m","messages":[{"role":"user","content":"Hi"}],"stream":False})
    assert ambiguous.json()["error"]["code"] == "ambiguous_player_entity"
