import json
import sqlite3
import threading
import time

from fastapi.testclient import TestClient

from proxy_server.app import create_app
from proxy_server.services.storyteller import DeterministicMockStoryteller
from proxy_server.services.structured_tail import START


def _request(content, **extra):
    value = {"model": "stage2b-mock", "messages": [{"role": "user", "content": content}], "stream": False}
    value.update(extra)
    return value


def _turn_and_docs(session):
    conn = sqlite3.connect(session.database_path)
    turn = conn.execute("SELECT current_turn FROM campaigns").fetchone()[0]
    docs = {row[0]: (row[1], json.loads(row[2])) for row in conn.execute(
        "SELECT namespace,revision,state_json FROM state_documents WHERE namespace LIKE 'narrative.%' ORDER BY namespace")}
    conn.close()
    return turn, docs


def test_default_mock_end_to_end_visible_only_and_rules_free(rules_free_campaign):
    app = create_app(rules_free_campaign)
    assert isinstance(app.state.storyteller, DeterministicMockStoryteller)
    response = TestClient(app).post("/v1/chat/completions", headers={"X-Idempotency-Key": "valid-1"},
                                    json=_request("Hello. __TEST_TRIGGER_VALID__"))
    assert response.status_code == 200
    content = response.json()["choices"][0]["message"]["content"]
    assert START not in content and "state_patches" not in content
    turn, docs = _turn_and_docs(rules_free_campaign)
    assert turn == 1
    assert "narrative.dialogue" in docs and "narrative.memory" in docs
    assert "narrative.time" not in docs
    conn = sqlite3.connect(rules_free_campaign.database_path)
    assert conn.execute("SELECT content FROM conversation_turn_messages WHERE role='user'").fetchone()[0] == "Hello. __TEST_TRIGGER_VALID__"
    assert all(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
               for table in ("mechanical_stats", "dnd_stats", "combat_state"))
    conn.close()


def test_story_time_only_changes_by_validated_patch(rules_free_campaign):
    client = TestClient(create_app(rules_free_campaign))
    assert client.post("/v1/chat/completions", headers={"X-Idempotency-Key": "ordinary"},
                       json=_request("We keep talking. __TEST_TRIGGER_STORY_TIME_NONE__")).status_code == 200
    turn, docs = _turn_and_docs(rules_free_campaign)
    assert turn == 1 and "narrative.time" not in docs
    assert client.post("/v1/chat/completions", headers={"X-Idempotency-Key": "sleep"},
                       json=_request("I sleep. __TEST_TRIGGER_STORY_TIME_SLEEP__")).status_code == 200
    turn, docs = _turn_and_docs(rules_free_campaign)
    assert turn == 2
    assert docs["narrative.time"][1]["elapsed_duration"] == {"value": 8, "unit": "hours", "precision": "resolved"}
    assert client.post("/v1/chat/completions", headers={"X-Idempotency-Key": "travel"},
                       json=_request("I travel. __TEST_TRIGGER_STORY_TIME_TRAVEL__")).status_code == 200
    turn, docs = _turn_and_docs(rules_free_campaign)
    assert turn == 3 and docs["narrative.time"][0] == 2
    assert docs["narrative.time"][1]["elapsed_duration"]["value"] == 6


def test_movement_is_changed_only_by_success_patch(rules_free_campaign):
    client = TestClient(create_app(rules_free_campaign))
    blocked = client.post("/v1/chat/completions", headers={"X-Idempotency-Key": "blocked"},
                          json=_request("I head south to the village. __TEST_TRIGGER_MOVE_BLOCKED__"))
    assert blocked.status_code == 200
    _turn, docs = _turn_and_docs(rules_free_campaign)
    assert not ("narrative.location" in docs and "current_location_id" in docs["narrative.location"][1])
    moved = client.post("/v1/chat/completions", headers={"X-Idempotency-Key": "moved"},
                        json=_request("I head south to the village. __TEST_TRIGGER_MOVE_SUCCEEDS__"))
    assert moved.status_code == 200
    turn, docs = _turn_and_docs(rules_free_campaign)
    # The fixture also has a topology document in this namespace; query the player target directly.
    conn = sqlite3.connect(rules_free_campaign.database_path)
    state = json.loads(conn.execute("SELECT state_json FROM state_documents WHERE namespace='narrative.location' "
                                    "AND subject_type='narrative.entity' AND subject_id='1'").fetchone()[0])
    conn.close()
    assert turn == 2 and state["current_location_id"] == "2"


def test_repair_commits_and_schema_failure_rolls_back(rules_free_campaign):
    client = TestClient(create_app(rules_free_campaign))
    repaired = client.post("/v1/chat/completions", headers={"X-Idempotency-Key": "repair"},
                           json=_request("Now. __TEST_TRIGGER_MALFORMED_JSON__"))
    assert repaired.status_code == 200
    conn = sqlite3.connect(rules_free_campaign.database_path)
    assert conn.execute("SELECT repair_status FROM conversation_turn_commits").fetchone()[0] == "repaired"
    before = conn.execute("SELECT current_turn FROM campaigns").fetchone()[0]
    revisions = conn.execute("SELECT namespace,revision FROM state_documents ORDER BY namespace").fetchall()
    conn.close()
    failed = client.post("/v1/chat/completions", headers={"X-Idempotency-Key": "schema"},
                         json=_request("Bad. __TEST_TRIGGER_SCHEMA_FAILURE__"))
    assert failed.status_code == 502
    assert "state_patches" not in failed.text
    conn = sqlite3.connect(rules_free_campaign.database_path)
    assert conn.execute("SELECT current_turn FROM campaigns").fetchone()[0] == before
    assert conn.execute("SELECT namespace,revision FROM state_documents ORDER BY namespace").fetchall() == revisions
    conn.close()


def test_idempotent_replay_returns_identical_response_without_second_call(rules_free_campaign):
    class CountingStoryteller(DeterministicMockStoryteller):
        def __init__(self): self.calls = 0
        def tell(self, *args):
            self.calls += 1
            return super().tell(*args)
    storyteller = CountingStoryteller()
    client = TestClient(create_app(rules_free_campaign, storyteller))
    first = client.post("/v1/chat/completions", headers={"X-Idempotency-Key": "same"}, json=_request("Hello"))
    second = client.post("/v1/chat/completions", headers={"X-Idempotency-Key": "same"}, json=_request("Hello"))
    assert first.json() == second.json()
    assert storyteller.calls == 1
    conn = sqlite3.connect(rules_free_campaign.database_path)
    assert conn.execute("SELECT current_turn FROM campaigns").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM conversation_turn_messages").fetchone()[0] == 2
    conn.close()


def test_identical_headerless_message_on_next_turn_is_new_request(rules_free_campaign):
    class CountingStoryteller(DeterministicMockStoryteller):
        def __init__(self): self.calls = 0
        def tell(self, *args): self.calls += 1; return super().tell(*args)
    storyteller = CountingStoryteller()
    client = TestClient(create_app(rules_free_campaign, storyteller))
    first = client.post("/v1/chat/completions", json=_request("Hello without custom header"))
    second = client.post("/v1/chat/completions", json=_request("Hello without custom header"))
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] != second.json()["id"]
    assert storyteller.calls == 2
    conn = sqlite3.connect(rules_free_campaign.database_path)
    assert conn.execute("SELECT current_turn FROM campaigns").fetchone()[0] == 2
    assert conn.execute("SELECT count(*) FROM conversation_turn_messages WHERE role='assistant'").fetchone()[0] == 2
    conn.close()


def test_same_client_key_different_hash_conflicts(rules_free_campaign):
    client = TestClient(create_app(rules_free_campaign))
    assert client.post("/v1/chat/completions", headers={"X-Idempotency-Key": "collision"},
                       json=_request("First")).status_code == 200
    conflict = client.post("/v1/chat/completions", headers={"X-Idempotency-Key": "collision"},
                           json=_request("Different"))
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "duplicate_request_conflict"


def test_concurrent_identical_fallback_requests_only_one_commits(rules_free_campaign):
    class Slow(DeterministicMockStoryteller):
        calls = 0
        def tell(self, *args):
            self.calls += 1; time.sleep(.2); return super().tell(*args)
    storyteller = Slow(); app = create_app(rules_free_campaign, storyteller); results = []
    payload = _request("Identical")
    def submit():
        response = TestClient(app).post("/v1/chat/completions", json=payload)
        results.append((response.status_code, response.json()))
    threads = [threading.Thread(target=submit) for _ in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert sorted(status for status, _ in results) == [200, 409]
    assert next(body for status, body in results if status == 409)["error"]["code"] == "request_in_progress"
    assert storyteller.calls == 1


def test_concurrent_snapshot_allows_only_one_next_conversation_turn(rules_free_campaign):
    barrier = threading.Barrier(2)
    class BarrierStoryteller(DeterministicMockStoryteller):
        def tell(self, *args):
            barrier.wait(timeout=5)
            return super().tell(*args)
    app = create_app(rules_free_campaign, BarrierStoryteller())
    results = []
    def submit(key):
        response = TestClient(app).post("/v1/chat/completions", headers={"X-Idempotency-Key": key},
                                        json=_request(f"Concurrent {key}"))
        results.append((response.status_code, response.json()))
    threads = [threading.Thread(target=submit, args=(key,)) for key in ("one", "two")]
    for thread in threads: thread.start()
    for thread in threads: thread.join(timeout=10)
    assert sorted(status for status, _body in results) == [200, 409]
    failure = next(body for status, body in results if status == 409)
    assert failure["error"]["code"] == "conversation_turn_conflict"
    conn = sqlite3.connect(rules_free_campaign.database_path)
    assert conn.execute("SELECT current_turn FROM campaigns").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM conversation_turn_commits").fetchone()[0] == 1
    assert conn.execute("SELECT count(DISTINCT conversation_turn_after) FROM conversation_turn_commits").fetchone()[0] == 1
    conn.close()


def test_substituted_storyteller_exception_uses_safe_boundary(rules_free_campaign):
    class FailingStoryteller:
        def tell(self, *_args): raise RuntimeError("private details")
    response = TestClient(create_app(rules_free_campaign, FailingStoryteller())).post(
        "/v1/chat/completions", json=_request("Hello"))
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "storyteller_error"
    assert "private details" not in response.text


def test_explicit_replay_changes_no_state_revision_or_audit(rules_free_campaign):
    client = TestClient(create_app(rules_free_campaign)); payload = _request("Sleep __TEST_TRIGGER_STORY_TIME_SLEEP__")
    first = client.post("/v1/chat/completions", headers={"X-Idempotency-Key":"replay-all"}, json=payload)
    conn = sqlite3.connect(rules_free_campaign.database_path)
    before = {
        "turn": conn.execute("SELECT current_turn FROM campaigns").fetchone()[0],
        "docs": conn.execute("SELECT id,state_json,revision FROM state_documents ORDER BY id").fetchall(),
        "patches": conn.execute("SELECT count(*) FROM state_patch_log").fetchone()[0],
        "idem": conn.execute("SELECT count(*) FROM state_idempotency").fetchone()[0],
        "messages": conn.execute("SELECT count(*) FROM conversation_turn_messages").fetchone()[0],
    }; conn.close()
    replay = client.post("/v1/chat/completions", headers={"X-Idempotency-Key":"replay-all"}, json=payload)
    conn = sqlite3.connect(rules_free_campaign.database_path)
    after = {
        "turn": conn.execute("SELECT current_turn FROM campaigns").fetchone()[0],
        "docs": conn.execute("SELECT id,state_json,revision FROM state_documents ORDER BY id").fetchall(),
        "patches": conn.execute("SELECT count(*) FROM state_patch_log").fetchone()[0],
        "idem": conn.execute("SELECT count(*) FROM state_idempotency").fetchone()[0],
        "messages": conn.execute("SELECT count(*) FROM conversation_turn_messages").fetchone()[0],
    }; conn.close()
    assert first.json() == replay.json() and before == after


def test_oversized_hidden_tail_is_safe_error(rules_free_campaign):
    class Oversized:
        def tell(self, *_args):
            from proxy_server.services.structured_tail import END, START
            return f"Visible\n{START}\n" + ("x" * (1024 * 1024 + 1)) + f"\n{END}"
    response = TestClient(create_app(rules_free_campaign, Oversized())).post(
        "/v1/chat/completions", json=_request("Hello"))
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "malformed_structured_output"


def test_streaming_and_unsupported_messages_are_rejected(rules_free_campaign):
    client = TestClient(create_app(rules_free_campaign))
    stream = client.post("/v1/chat/completions", json={**_request("Hello"), "stream": True})
    assert stream.status_code == 400 and stream.json()["error"]["code"] == "unsupported_streaming"
    tools = client.post("/v1/chat/completions", json={"model": "mock", "messages": [
        {"role": "user", "content": "Hello", "tool_calls": []}], "stream": False})
    assert tools.status_code == 422 and tools.json()["error"]["code"] == "invalid_request"
    direct_state = client.post("/v1/chat/completions", json={**_request("Hello"), "state_patches": []})
    assert direct_state.status_code == 422
