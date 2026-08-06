"""Add request idempotency, accepted exchange commits, and chat history."""

DDL = """
CREATE TABLE conversation_turn_requests (
 id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
 request_hash TEXT NOT NULL, request_id TEXT NOT NULL, last_request_id TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('in_progress','completed','failed')),
 attempt_number INTEGER NOT NULL DEFAULT 1 CHECK(attempt_number >= 1), lease_expires_at TEXT,
 snapshot_conversation_turn INTEGER NOT NULL CHECK(snapshot_conversation_turn >= 0),
 active_player_subject_id TEXT NOT NULL, response_json TEXT, error_code TEXT,
 retryable INTEGER NOT NULL DEFAULT 0 CHECK(retryable IN (0,1)),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, completed_at TEXT,
 FOREIGN KEY(campaign_id) REFERENCES campaigns(id), UNIQUE(campaign_id,idempotency_key), UNIQUE(campaign_id,id));
CREATE INDEX idx_conversation_turn_requests_status ON conversation_turn_requests(campaign_id,status,created_at,id);
CREATE INDEX idx_conversation_turn_requests_lease ON conversation_turn_requests(campaign_id,status,lease_expires_at,id);
CREATE TABLE conversation_turn_commits (
 id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL,
 conversation_turn_request_id TEXT NOT NULL,
 conversation_turn_before INTEGER NOT NULL CHECK(conversation_turn_before >= 0),
 conversation_turn_after INTEGER NOT NULL, active_player_subject_id TEXT NOT NULL,
 storyteller_output_hash TEXT NOT NULL, visible_narrative_hash TEXT NOT NULL,
 repair_status TEXT NOT NULL CHECK(repair_status IN ('valid','repaired')),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(campaign_id) REFERENCES campaigns(id),
 FOREIGN KEY(campaign_id,conversation_turn_request_id) REFERENCES conversation_turn_requests(campaign_id,id),
 CHECK(conversation_turn_after = conversation_turn_before + 1),
 UNIQUE(campaign_id,conversation_turn_after), UNIQUE(campaign_id,conversation_turn_request_id), UNIQUE(campaign_id,id));
CREATE INDEX idx_conversation_turn_commits_campaign ON conversation_turn_commits(campaign_id,conversation_turn_after);
CREATE TABLE conversation_turn_messages (
 id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL,
 conversation_turn_commit_id TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('system','user','assistant')),
 content TEXT NOT NULL, message_index INTEGER NOT NULL CHECK(message_index >= 0),
 source TEXT NOT NULL CHECK(source IN ('http_request','storyteller_response')),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(campaign_id) REFERENCES campaigns(id),
 FOREIGN KEY(campaign_id,conversation_turn_commit_id) REFERENCES conversation_turn_commits(campaign_id,id),
 UNIQUE(conversation_turn_commit_id,message_index));
CREATE INDEX idx_conversation_turn_messages_history ON conversation_turn_messages(campaign_id,created_at,conversation_turn_commit_id,message_index);
"""

_failure_injector = None


def migrate(conn, *_args, **_kwargs):
    """Apply the additive migration inside the manager-owned transaction."""
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='conversation_turn_requests'").fetchone():
        return
    for statement in DDL.split(";"):
        if statement.strip():
            conn.execute(statement)
    if _failure_injector is not None:
        _failure_injector("conversation_turns_v9")
