import base64
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from database.db_manager import DatabaseManager


def _v7_from_current(manager):
    conn = manager._get_connection()
    conn.execute("DELETE FROM legacy_extraction_quarantine")
    conn.execute("DELETE FROM legacy_extraction_items")
    conn.execute("DELETE FROM legacy_extraction_runs")
    conn.execute("DELETE FROM state_documents WHERE namespace LIKE 'legacy.%' OR namespace LIKE 'rules.%.legacy-%'")
    conn.execute("DROP TABLE legacy_extraction_quarantine")
    conn.execute("DROP TABLE legacy_extraction_items")
    conn.execute("DROP TABLE legacy_extraction_runs")
    conn.execute("UPDATE schema_version SET version=7")
    conn.commit()
    return conn


def _migration_module():
    path = Path(__file__).parent / "migrations/008_legacy_state_extraction.py"
    spec = importlib.util.spec_from_file_location("v8_hardening", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_without_rowid_composite_mixed_identity_and_exact_values(tmp_path):
    path = tmp_path / "without-rowid.db"
    manager = DatabaseManager(str(path)); campaign_id = manager.campaign_id
    conn = _v7_from_current(manager)
    conn.execute("DROP TABLE world_state")
    conn.execute('''CREATE TABLE world_state(
        tenant TEXT, "binary key" BLOB, weather TEXT, additional_state TEXT,
        "quoted value" BLOB, "世界" TEXT GENERATED ALWAYS AS (tenant || '-生成') STORED,
        PRIMARY KEY(tenant,"binary key")) WITHOUT ROWID''')
    conn.execute('INSERT INTO world_state(tenant,"binary key",weather,additional_state,"quoted value") VALUES(?,?,?,?,?)',
                 ("café", sqlite3.Binary(b"\x00\xff"), "雷雨", '{"broken":', sqlite3.Binary(b"blob\x00")))
    conn.commit(); conn.close()

    DatabaseManager(str(path), campaign_id=campaign_id)
    conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
    document = json.loads(conn.execute(
        "SELECT state_json FROM state_documents WHERE namespace='legacy.world-additional-state.v1'"
    ).fetchone()[0])
    identity = document["source"]["identity"]
    assert identity["kind"] == "primary-key"
    assert [column["name"] for column in identity["columns"]] == ["tenant", "binary key"]
    assert identity["columns"][0]["value"]["decoded"] == "café"
    assert base64.b64decode(identity["columns"][1]["value"]["base64"]) == b"\x00\xff"
    unknown = json.loads(conn.execute(
        "SELECT state_json FROM state_documents WHERE namespace='legacy.unknown-columns.v1'"
    ).fetchone()[0])
    columns = {column["name"]: column for column in unknown["source"]["columns"]}
    assert base64.b64decode(columns["quoted value"]["value"]["base64"]) == b"blob\x00"
    assert columns["世界"]["hidden"] == 3 and columns["世界"]["value"]["decoded"] == "café-生成"
    assert conn.execute("SELECT count(*) FROM legacy_extraction_quarantine").fetchone()[0] == 1
    conn.close()


def test_preexisting_foreign_key_violation_is_preserved_and_reported(tmp_path):
    path = tmp_path / "orphan.db"
    manager = DatabaseManager(str(path)); campaign_id = manager.campaign_id
    conn = _v7_from_current(manager)
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("INSERT INTO ambiance_state(id,location_id,lighting) VALUES(9,999,'dim')")
    conn.commit(); conn.close()

    DatabaseManager(str(path), campaign_id=campaign_id)
    conn = sqlite3.connect(path)
    report = json.loads(conn.execute("SELECT report_json FROM legacy_extraction_runs").fetchone()[0])
    assert report["foreign_key_baseline"] == report["foreign_key_final"]
    assert report["pre_existing_foreign_key_violation_count"] == 1
    assert conn.execute("SELECT location_id FROM ambiance_state WHERE id=9").fetchone()[0] == 999
    conn.close()


def test_new_migration_owned_foreign_key_violation_fails_and_rolls_back(tmp_path, monkeypatch):
    manager = DatabaseManager(str(tmp_path / "fk-change.db"))
    module = manager._load_controlled_migration(8)
    conn = manager._get_connection()
    before = conn.execute("SELECT count(*) FROM legacy_extraction_runs").fetchone()[0]
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("PRAGMA defer_foreign_keys=ON")
    def introduce_violation(target):
        target.execute("""INSERT INTO state_documents(
            id,campaign_id,namespace,subject_type,subject_id,state_json,revision,content_hash,metadata_json
        ) VALUES('invalid-fk','missing-campaign','legacy.world-state.v1','legacy-world-state-row',
                 'invalid','{}',1,'invalid','{}')""")
    monkeypatch.setattr(module, "_before_foreign_key_validation", introduce_violation)
    with pytest.raises(RuntimeError, match="foreign-key violations changed"):
        module.extract(conn, manager.campaign_id)
    conn.rollback()
    assert conn.execute("SELECT count(*) FROM legacy_extraction_runs").fetchone()[0] == before
    conn.close()


def test_json_diagnostics_and_quarantine_are_exact(tmp_path):
    path = tmp_path / "json-diagnostics.db"
    manager = DatabaseManager(str(path))
    conn = manager._get_connection()
    conn.execute("INSERT INTO characters(id,name) VALUES(1,'Mara')")
    conn.execute("INSERT INTO dnd_stats(character_id,equipment,skills,known_spells,prepared_spells,class_features) VALUES(1,?,?,?,?,?)",
                 ('{"broken":', 7, sqlite3.Binary(b"[]"), None, "[]"))
    conn.commit(); conn.close(); manager.refresh_legacy_extraction()
    conn = manager._get_connection()
    state = json.loads(conn.execute("SELECT state_json FROM state_documents WHERE namespace='rules.dnd5e.legacy-v1'").fetchone()[0])
    reasons = {item["column"]: item["reason_code"] for item in state["extraction"]["json_diagnostics"]}
    assert reasons["equipment"] == "malformed-json"
    assert reasons["skills"] == "json-valid"  # TEXT affinity preserves the scalar JSON text "7".
    assert reasons["known_spells"] == "json-non-text-storage"
    assert reasons["class_features"] == "json-valid"
    assert reasons["prepared_spells"] == "json-null-not-applicable"
    assert conn.execute("SELECT count(*) FROM legacy_extraction_quarantine").fetchone()[0] == 1
    assert conn.execute("SELECT reason_code FROM legacy_extraction_quarantine").fetchone()[0] == "malformed-json"
    conn.close()


def test_json_invalid_encoding_and_parser_failure_reason_codes(monkeypatch):
    module = _migration_module()
    columns = [{"name":"value","value":{"storage_class":"text","base64":"/w==","byte_length":1,
                "encoding":"utf-8","decoding_status":"invalid"}}]
    assert module._parse_json(columns, ("value",))[3][0]["reason_code"] == "invalid-text-encoding"
    for storage_class, value in (("integer", {"decimal":"7","signed_big_endian_base64":"Bw=="}),
                                 ("real", {"decimal":"1.5","ieee754_binary64_hex":"3ff8000000000000"}),
                                 ("blob", {"base64":"e30=","byte_length":2})):
        typed = [{"name":"value","value":{"storage_class":storage_class, **value}}]
        assert module._parse_json(typed, ("value",))[3][0]["reason_code"] == "json-non-text-storage"
    columns[0]["value"].update({"decoding_status":"valid","decoded":"{}"})
    monkeypatch.setattr(module.json, "loads", lambda _value: (_ for _ in ()).throw(RuntimeError("parser")))
    assert module._parse_json(columns, ("value",))[3][0]["reason_code"] == "json-parser-failure"


def _fingerprint_database(path, reversed_order=False):
    conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
    conn.executescript('''CREATE TABLE sample(
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, payload BLOB,
        generated TEXT GENERATED ALWAYS AS (name || '!') STORED);
        CREATE INDEX sample_name ON sample(name);
        CREATE TRIGGER sample_trigger AFTER UPDATE ON sample BEGIN SELECT 1; END;''')
    rows = [(1,"one",b"1"),(2,"two",b"2")]
    for row in reversed(rows) if reversed_order else rows:
        conn.execute("INSERT INTO sample(id,name,payload) VALUES(?,?,?)", row)
    conn.commit()
    return conn


def test_fingerprint_is_deterministic_and_covers_values_sequence_and_generated_metadata(tmp_path):
    module = _migration_module()
    first = _fingerprint_database(tmp_path / "first.db")
    second = _fingerprint_database(tmp_path / "second.db", reversed_order=True)
    initial = module._legacy_fingerprint(first)
    assert initial == module._legacy_fingerprint(second)
    assert any(column["hidden"] for column in module._columns(first, "sample"))
    first.close(); first = sqlite3.connect(tmp_path / "first.db"); first.row_factory = sqlite3.Row
    assert module._legacy_fingerprint(first) == initial
    first.execute("UPDATE sample SET payload=? WHERE id=1", (sqlite3.Binary(b"changed"),)); first.commit()
    changed = module._legacy_fingerprint(first); assert changed != initial
    first.execute("UPDATE sample SET payload=? WHERE id=1", (sqlite3.Binary(b"1"),))
    first.execute("UPDATE sqlite_sequence SET seq=99 WHERE name='sample'"); first.commit()
    assert module._legacy_fingerprint(first) != initial
    first.close(); second.close()


def test_large_blob_streaming_and_oversize_rollback(tmp_path, monkeypatch):
    streamed = tmp_path / "streamed.db"
    manager = DatabaseManager(str(streamed)); campaign_id = manager.campaign_id
    conn = _v7_from_current(manager)
    payload = b"z" * (1024 * 1024 + 17)
    conn.execute("ALTER TABLE characters ADD COLUMN large_blob BLOB")
    conn.execute("INSERT INTO characters(id,name,large_blob) VALUES(1,'Mara',?)", (sqlite3.Binary(payload),))
    conn.commit(); conn.close()
    monkeypatch.setenv("ZERO_CONTEXT_EXTRACTION_BLOB_STREAM_THRESHOLD", "1024")
    DatabaseManager(str(streamed), campaign_id=campaign_id)
    conn = sqlite3.connect(streamed)
    state = json.loads(conn.execute("SELECT state_json FROM state_documents WHERE namespace='legacy.unknown-columns.v1'").fetchone()[0])
    assert base64.b64decode(state["source"]["columns"][0]["value"]["base64"]) == payload
    conn.close()

    oversized = tmp_path / "oversized.db"
    manager = DatabaseManager(str(oversized)); campaign_id = manager.campaign_id
    conn = _v7_from_current(manager)
    conn.execute("INSERT INTO characters(id,name,full_card_text) VALUES(1,'Mara','12345')")
    conn.commit(); conn.close()
    monkeypatch.setenv("ZERO_CONTEXT_EXTRACTION_MAX_VALUE_BYTES", "4")
    with pytest.raises(RuntimeError, match="extraction safeguard"):
        DatabaseManager(str(oversized), campaign_id=campaign_id)
    conn = sqlite3.connect(oversized)
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 7
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='legacy_extraction_runs'").fetchone() is None
    assert conn.execute("SELECT full_card_text FROM characters").fetchone()[0] == "12345"
    conn.close()


def test_conflict_sidecar_survives_rollback_without_overwrite(tmp_path):
    path = tmp_path / "conflict.db"
    manager = DatabaseManager(str(path))
    conn = manager._get_connection(); conn.execute("INSERT INTO world_state(id,weather) VALUES(1,'rain')"); conn.commit(); conn.close()
    manager.refresh_legacy_extraction()
    conn = manager._get_connection()
    document = conn.execute("SELECT id,state_json,revision FROM state_documents WHERE namespace='legacy.world-state.v1'").fetchone()
    conn.execute("UPDATE state_documents SET state_json='{}' WHERE id=?", (document[0],)); conn.commit(); conn.close()
    with pytest.raises(RuntimeError, match="compatibility document conflict"):
        manager.refresh_legacy_extraction()
    sidecars = sorted(path.parent.glob(f"{path.name}.v8-conflict.*.json"))
    assert len(sidecars) == 1
    report = json.loads(sidecars[0].read_text())
    assert report["document_id"] == document[0] and report["status"] == "conflict"
    conn = manager._get_connection()
    assert tuple(conn.execute("SELECT state_json,revision FROM state_documents WHERE id=?", (document[0],)).fetchone()) == ("{}", document[2])
    conn.close()
