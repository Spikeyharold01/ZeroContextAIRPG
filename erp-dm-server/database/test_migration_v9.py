import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from database.db_manager import DatabaseManager


TABLES = ("conversation_turn_messages", "conversation_turn_commits", "conversation_turn_requests")


def _downgrade_to_v8(path):
    schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    schema = schema.split("-- 19. ACCEPTED CONVERSATION EXCHANGES", 1)[0]
    campaign_id = str(uuid4())
    conn = sqlite3.connect(path)
    conn.executescript(schema)
    conn.execute("INSERT INTO schema_version(id,version) VALUES(1,8)")
    conn.execute("INSERT INTO campaigns(id,display_name) VALUES(?,'Canonical v8')", (campaign_id,))
    conn.execute("INSERT INTO locations(name) VALUES('Preserved')")
    conn.commit(); conn.close()
    return campaign_id


def test_fresh_database_is_v9_with_turn_tables(tmp_path):
    manager = DatabaseManager(str(tmp_path / "fresh.db"))
    conn = manager._get_connection()
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 9
    assert all(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
               for name in TABLES)
    conn.close()


def test_v8_upgrade_backs_up_preserves_data_and_reopens(tmp_path):
    path = tmp_path / "upgrade.db"
    campaign_id = _downgrade_to_v8(path)
    DatabaseManager(str(path), campaign_id=campaign_id)
    assert path.with_name(path.name + ".pre-v9.bak").is_file()
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT name FROM locations").fetchone()[0] == "Preserved"
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()
    DatabaseManager(str(path), campaign_id=campaign_id)


def test_v9_constraints_and_foreign_key_ownership(tmp_path):
    manager = DatabaseManager(str(tmp_path / "constraints.db"))
    conn = manager._get_connection()
    campaign = manager.campaign_id
    values = ("r", campaign, "key", "hash", "trace", "trace", "in_progress", 0, "player")
    conn.execute("INSERT INTO conversation_turn_requests(id,campaign_id,idempotency_key,request_hash,request_id,last_request_id,status,snapshot_conversation_turn,active_player_subject_id) VALUES(?,?,?,?,?,?,?,?,?)", values)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO conversation_turn_requests(id,campaign_id,idempotency_key,request_hash,request_id,last_request_id,status,snapshot_conversation_turn,active_player_subject_id) VALUES('r2',?,?,?,?,?,?,?,?)", values[1:])
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO conversation_turn_commits(id,campaign_id,conversation_turn_request_id,conversation_turn_before,conversation_turn_after,active_player_subject_id,storyteller_output_hash,visible_narrative_hash,repair_status) VALUES('c',?,'r',0,2,'player','a','b','valid')", (campaign,))
    conn.rollback(); conn.close()


def test_v9_composite_foreign_keys_enforce_campaign_ownership(tmp_path):
    manager = DatabaseManager(str(tmp_path / "ownership.db"))
    conn = manager._get_connection()
    first = manager.campaign_id
    conn.execute("UPDATE campaigns SET lifecycle_status='deleted',deleted_at='now' WHERE id=?", (first,))
    conn.execute("INSERT INTO campaigns(id,display_name) VALUES('second','Second')")
    for request_id, campaign in (("r1", first), ("r2", "second")):
        conn.execute("INSERT INTO conversation_turn_requests(id,campaign_id,idempotency_key,request_hash,request_id,last_request_id,status,snapshot_conversation_turn,active_player_subject_id) "
                     "VALUES(?,?,?,?,?,?,'in_progress',0,'player')",
                     (request_id, campaign, request_id, "hash", request_id, request_id))
    conn.execute("INSERT INTO conversation_turn_commits(id,campaign_id,conversation_turn_request_id,conversation_turn_before,conversation_turn_after,active_player_subject_id,storyteller_output_hash,visible_narrative_hash,repair_status) "
                 "VALUES('c1',?,'r1',0,1,'player','a','b','valid')", (first,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO conversation_turn_commits(id,campaign_id,conversation_turn_request_id,conversation_turn_before,conversation_turn_after,active_player_subject_id,storyteller_output_hash,visible_narrative_hash,repair_status) "
                     "VALUES('cross','second','r1',0,1,'player','a','b','valid')")
    conn.execute("INSERT INTO conversation_turn_messages(id,campaign_id,conversation_turn_commit_id,role,content,message_index,source) "
                 "VALUES('m1',?,'c1','user','ok',0,'http_request')", (first,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO conversation_turn_messages(id,campaign_id,conversation_turn_commit_id,role,content,message_index,source) "
                     "VALUES('cross-message','second','c1','user','bad',0,'http_request')")
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.rollback(); conn.close()


def test_v9_failure_rolls_back_tables_and_version(tmp_path, monkeypatch):
    path = tmp_path / "rollback.db"
    campaign_id = _downgrade_to_v8(path)
    DatabaseManager._MIGRATION_FAILURE_INJECTOR = lambda _point: (_ for _ in ()).throw(RuntimeError("injected"))
    try:
        with pytest.raises(RuntimeError, match="injected"):
            DatabaseManager(str(path), campaign_id=campaign_id)
    finally:
        DatabaseManager._MIGRATION_FAILURE_INJECTOR = None
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 8
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='conversation_turn_requests'").fetchone()
    conn.close()
