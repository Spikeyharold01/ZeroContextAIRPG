import json
import sqlite3

import pytest
from pydantic import ValidationError

from contracts.state import StateTarget
from database.db_manager import DatabaseManager


def test_v8_extracts_malformed_json_losslessly_without_rules_activation(tmp_path):
    path = tmp_path / "legacy.db"
    manager = DatabaseManager(str(path))
    conn = manager._get_connection()
    conn.execute("INSERT INTO characters(id,name,plot_state) VALUES(7,'Mara',?)", ('{"broken":',))
    conn.execute("INSERT INTO dnd_stats(character_id,class,hp_current) VALUES(7,'Wizard',3)")
    conn.execute("UPDATE schema_version SET version=7")
    for table in ("legacy_extraction_quarantine", "legacy_extraction_items", "legacy_extraction_runs"):
        conn.execute(f"DELETE FROM {table}")
    conn.execute("DELETE FROM state_documents WHERE namespace LIKE 'legacy.%' OR namespace LIKE 'rules.%.legacy-%'")
    conn.commit(); conn.close()

    manager = DatabaseManager(str(path), campaign_id=manager.campaign_id)
    conn = manager._get_connection()
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 9
    assert conn.execute("SELECT rules_profile_id FROM campaigns").fetchone()[0] is None
    raw = conn.execute("SELECT state_json FROM state_documents WHERE namespace='legacy.character-plot-state.v1'").fetchone()[0]
    document = json.loads(raw)
    value = document["source"]["columns"][0]["value"]
    assert value["decoded"] == '{"broken":'
    assert document["extraction"]["parse_status"] == "invalid"
    assert conn.execute("SELECT count(*) FROM legacy_extraction_quarantine").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM state_patch_log").fetchone()[0] == 0
    conn.close()


def test_v8_explicit_refresh_is_idempotent_then_increments_once(tmp_path):
    manager = DatabaseManager(str(tmp_path / "campaign.db"))
    conn = manager._get_connection()
    conn.execute("INSERT INTO world_state(id,weather) VALUES(1,'rain')")
    conn.commit(); conn.close()
    manager.refresh_legacy_extraction()
    conn = manager._get_connection()
    first = conn.execute("SELECT id,revision FROM state_documents WHERE namespace='legacy.world-state.v1'").fetchone()
    conn.close()
    manager.refresh_legacy_extraction()
    conn = manager._get_connection()
    assert conn.execute("SELECT revision FROM state_documents WHERE id=?",(first[0],)).fetchone()[0] == first[1]
    conn.execute("UPDATE world_state SET weather='snow' WHERE id=1"); conn.commit(); conn.close()
    manager.refresh_legacy_extraction()
    conn = manager._get_connection()
    assert conn.execute("SELECT revision FROM state_documents WHERE id=?",(first[0],)).fetchone()[0] == first[1] + 1
    assert conn.execute("SELECT count(*) FROM state_patch_log").fetchone()[0] == 0
    conn.close()


@pytest.mark.parametrize("namespace", ["legacy.foo", "rules.dnd5e.legacy-v1"])
def test_compatibility_namespaces_are_reserved(namespace):
    with pytest.raises(ValidationError):
        StateTarget(namespace=namespace, subject_type="character", subject_id="1")
