import hashlib
import json
from pathlib import Path
import sqlite3
import sys

import pytest


DATABASE_DIR = Path(__file__).parent
sys.path.insert(0, str(DATABASE_DIR))

from db_manager import DatabaseManager
from schema_manifest import (
    REQUIRED_SQL_FRAGMENTS,
    VERSION_1_BASELINE_PATH,
    inspect_schema,
    load_current_manifest,
    schema_differences,
)


CURRENT_SCHEMA_SQL = (DATABASE_DIR / "schema.sql").read_text(encoding="utf-8")
LEGACY_FIXTURE_SQL = (
    DATABASE_DIR / "test_fixtures" / "legacy_campaign.sql"
).read_text(encoding="utf-8")


def execute_sql(db_file, sql):
    conn = sqlite3.connect(db_file)
    conn.executescript(sql)
    conn.close()


def schema_manifest_for(db_file):
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    try:
        return inspect_schema(conn)
    finally:
        conn.close()


def assert_current_parity(db_file):
    differences = schema_differences(
        schema_manifest_for(db_file),
        load_current_manifest(),
        REQUIRED_SQL_FRAGMENTS,
    )
    assert differences == []


def version_of(db_file):
    conn = sqlite3.connect(db_file)
    try:
        row = conn.execute(
            "SELECT version FROM schema_version WHERE id = 1"
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def recreate_table(conn, table, create_sql, copied_columns):
    temporary = f"old_{table}"
    conn.execute(f'ALTER TABLE "{table}" RENAME TO "{temporary}"')
    conn.execute(create_sql)
    if copied_columns:
        columns = ", ".join(f'"{column}"' for column in copied_columns)
        conn.execute(
            f'INSERT INTO "{table}" ({columns}) '
            f'SELECT {columns} FROM "{temporary}"'
        )
    conn.execute(f'DROP TABLE "{temporary}"')


def make_current_variant(db_file, version=1):
    conn = sqlite3.connect(db_file)
    conn.executescript(CURRENT_SCHEMA_SQL)
    conn.execute(
        "INSERT INTO schema_version (id, version) VALUES (1, ?)",
        (version,),
    )
    conn.commit()
    return conn


def make_sparse_version(db_file, version, sentinel_game_day=False):
    status = ", status TEXT DEFAULT 'active'" if version >= 4 else ""
    active = ", is_active BOOLEAN DEFAULT 1" if version >= 5 else ""
    prose = ", prose_fingerprint TEXT" if version >= 3 else ""
    game_day = ", game_day INTEGER DEFAULT 1" if version >= 2 or sentinel_game_day else ""
    execute_sql(
        db_file,
        f"""
        CREATE TABLE schema_version (
            id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL
        );
        INSERT INTO schema_version VALUES (1, {version});
        CREATE TABLE characters (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL,
            type TEXT DEFAULT 'NPC'{prose}{status}{active}
        );
        CREATE TABLE game_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            current_turn INTEGER DEFAULT 0{game_day}
        );
        INSERT INTO characters (id, name, type) VALUES (1, 'Version {version}', 'NPC');
        INSERT INTO game_state (id, current_turn{', game_day' if game_day else ''})
        VALUES (1, 17{', 3' if game_day else ''});
        """,
    )


def test_canonical_version_1_baseline_checksum_and_migration(tmp_path):
    assert hashlib.sha256(VERSION_1_BASELINE_PATH.read_bytes()).hexdigest() == (
        "9fb3ead58626a20c2bdcd7b1fcc9b34555f1735a1b381a8a8350d7b22e7c93c5"
    )
    db_file = tmp_path / "canonical_v1.db"
    execute_sql(db_file, VERSION_1_BASELINE_PATH.read_text(encoding="utf-8"))
    conn = sqlite3.connect(db_file)
    conn.execute("INSERT INTO characters (id, name, type) VALUES (41, 'V1 Hero', 'PC')")
    conn.commit()
    conn.close()

    manager = DatabaseManager(str(db_file))

    assert manager.get_character(41)["name"] == "V1 Hero"
    assert manager.get_character(41)["status"] == "active"
    assert manager.get_character(41)["is_active"] == 1
    assert version_of(db_file) == 6
    assert_current_parity(db_file)
    assert Path(f"{db_file}.pre-v6.bak").is_file()


def test_sparse_legacy_fixture_reconciles_with_data_preserved(tmp_path):
    db_file = tmp_path / "sparse.db"
    execute_sql(db_file, LEGACY_FIXTURE_SQL)

    manager = DatabaseManager(str(db_file))

    character = manager.get_character(12)
    assert character["name"] == "Mara Voss"
    assert character["current_goal"] == "Protect the sealed letter"
    assert character["status"] == "active"
    assert manager.get_emotional_state(12)["trust"] == 63
    assert manager.get_mechanical_stats(12)["hp_current"] == 10
    assert manager.get_active_facts(12)[0]["fact_text"] == "The eastern bridge is watched."
    assert manager.get_active_facts(12)[0]["fact_type"] == "world_fact"
    assert manager.get_active_facts(12)[0]["game_day"] == 1
    assert manager.get_dnd_stats(12) is None
    assert version_of(db_file) == 6
    assert_current_parity(db_file)


def test_pre_dnd_schema_creates_table_without_synthesizing_rows(tmp_path):
    db_file = tmp_path / "pre_dnd.db"
    conn = make_current_variant(db_file)
    conn.execute("DROP TABLE dnd_stats")
    conn.execute("INSERT INTO characters (id, name, type) VALUES (1, 'No Sheet', 'PC')")
    conn.commit()
    conn.close()

    manager = DatabaseManager(str(db_file))

    assert manager.get_dnd_stats(1) is None
    assert_current_parity(db_file)


def test_pre_belief_schema_backfills_world_fact_without_provenance(tmp_path):
    db_file = tmp_path / "pre_belief.db"
    conn = make_current_variant(db_file)
    conn.execute("DROP INDEX idx_facts_character")
    conn.execute("DROP INDEX idx_facts_active")
    conn.execute("DROP INDEX idx_facts_expiry")
    recreate_table(
        conn,
        "conversational_facts",
        """
        CREATE TABLE conversational_facts (
            id TEXT PRIMARY KEY, character_id INTEGER, fact_text TEXT NOT NULL,
            fact_references TEXT, embedding BLOB, importance FLOAT DEFAULT 0.5,
            confidence FLOAT DEFAULT 0.9, source_type TEXT,
            created_turn INTEGER DEFAULT 0, last_referenced_turn INTEGER DEFAULT 0,
            expires_at_turn INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active BOOLEAN DEFAULT 1, game_day INTEGER DEFAULT 1,
            FOREIGN KEY (character_id) REFERENCES characters(id)
        )
        """,
        (),
    )
    conn.execute("INSERT INTO characters (id, name, type) VALUES (1, 'Witness', 'NPC')")
    embedding = b"\x00\x00\x80?"
    conn.execute(
        "INSERT INTO conversational_facts "
        "(id, character_id, fact_text, fact_references, embedding, created_turn) "
        "VALUES ('legacy', 1, 'The bell rang.', '[]', ?, 9)",
        (embedding,),
    )
    conn.commit()
    conn.close()

    fact = DatabaseManager(str(db_file)).get_active_facts(1)[0]

    assert fact["fact_type"] == "world_fact"
    assert fact["source_character_id"] is None
    assert fact["created_turn"] == 9
    assert fact["embedding"] == pytest.approx([1.0])
    assert_current_parity(db_file)


def test_pre_game_day_schema_backfills_fact_and_game_day(tmp_path):
    db_file = tmp_path / "pre_day.db"
    conn = make_current_variant(db_file)
    conn.execute("DROP INDEX idx_facts_character")
    conn.execute("DROP INDEX idx_facts_active")
    conn.execute("DROP INDEX idx_facts_expiry")
    fact_columns = [
        "id", "character_id", "fact_text", "fact_references", "embedding",
        "importance", "confidence", "source_type", "fact_type",
        "source_character_id", "created_turn", "last_referenced_turn",
        "expires_at_turn", "created_at", "updated_at", "is_active",
    ]
    recreate_table(
        conn,
        "conversational_facts",
        """
        CREATE TABLE conversational_facts (
            id TEXT PRIMARY KEY, character_id INTEGER, fact_text TEXT NOT NULL,
            fact_references TEXT, embedding BLOB, importance FLOAT DEFAULT 0.5,
            confidence FLOAT DEFAULT 0.9, source_type TEXT,
            fact_type TEXT DEFAULT 'world_fact', source_character_id INTEGER,
            created_turn INTEGER DEFAULT 0, last_referenced_turn INTEGER DEFAULT 0,
            expires_at_turn INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active BOOLEAN DEFAULT 1,
            FOREIGN KEY (character_id) REFERENCES characters(id),
            FOREIGN KEY (source_character_id) REFERENCES characters(id)
        )
        """,
        fact_columns,
    )
    game_columns = [
        "id", "current_location_id", "current_scene_type", "combat_active",
        "current_turn",
    ]
    recreate_table(
        conn,
        "game_state",
        """
        CREATE TABLE game_state (
            id INTEGER PRIMARY KEY CHECK (id = 1) DEFAULT 1,
            current_location_id INTEGER, current_scene_type TEXT,
            combat_active BOOLEAN DEFAULT 0, current_turn INTEGER DEFAULT 0,
            FOREIGN KEY (current_location_id) REFERENCES locations(id)
        )
        """,
        game_columns,
    )
    conn.execute("INSERT INTO characters (id, name, type) VALUES (1, 'Old Hero', 'PC')")
    conn.execute(
        "INSERT INTO conversational_facts (id, character_id, fact_text, created_turn) "
        "VALUES ('old', 1, 'An old fact', 22)"
    )
    conn.execute("INSERT INTO game_state (id, current_turn) VALUES (1, 22)")
    conn.commit()
    conn.close()

    manager = DatabaseManager(str(db_file))

    assert manager.get_game_state()["game_day"] == 1
    fact = manager.get_active_facts(1)[0]
    assert fact["game_day"] == 1
    assert fact["created_turn"] == 22
    assert_current_parity(db_file)


@pytest.mark.parametrize("version", [2, 3, 4, 5])
def test_partially_migrated_versions_reconcile(version, tmp_path):
    db_file = tmp_path / f"version_{version}.db"
    make_sparse_version(db_file, version)

    manager = DatabaseManager(str(db_file))

    assert manager.get_character(1)["name"] == f"Version {version}"
    assert manager.get_game_state()["current_turn"] == 17
    assert version_of(db_file) == 6
    assert_current_parity(db_file)


def test_existing_migration_sentinel_does_not_mask_missing_schema(tmp_path):
    db_file = tmp_path / "sentinel.db"
    make_sparse_version(db_file, 1, sentinel_game_day=True)

    manager = DatabaseManager(str(db_file))

    assert manager.get_game_state()["game_day"] == 3
    assert manager.get_world_state()["id"] == 1
    assert_current_parity(db_file)


def assert_reconciliation_fails_without_version(db_file, message):
    with pytest.raises(Exception, match=message):
        DatabaseManager(str(db_file))
    conn = sqlite3.connect(db_file)
    try:
        has_version = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'schema_version'"
        ).fetchone()
        assert has_version is None
    finally:
        conn.close()


def test_invalid_character_types_fail_with_ids_and_values(tmp_path):
    db_file = tmp_path / "invalid_type.db"
    execute_sql(
        db_file,
        """
        CREATE TABLE characters (id INTEGER PRIMARY KEY, name TEXT, type TEXT);
        INSERT INTO characters VALUES (7, 'Broken', 'wizard');
        """,
    )

    assert_reconciliation_fails_without_version(
        db_file, "invalid character types: id=7 type='wizard'"
    )


def test_orphaned_character_reference_fails_precisely(tmp_path):
    db_file = tmp_path / "orphan_character.db"
    execute_sql(
        db_file,
        """
        CREATE TABLE characters (id INTEGER PRIMARY KEY, name TEXT, type TEXT);
        CREATE TABLE emotional_state (character_id INTEGER PRIMARY KEY, trust INTEGER);
        INSERT INTO characters VALUES (1, 'Valid', 'PC');
        INSERT INTO emotional_state VALUES (99, 50);
        """,
    )

    assert_reconciliation_fails_without_version(
        db_file,
        "orphaned foreign keys: emotional_state row 99 character_id=99 -> "
        "missing characters.id",
    )


def test_orphaned_location_reference_fails_precisely(tmp_path):
    db_file = tmp_path / "orphan_location.db"
    execute_sql(
        db_file,
        """
        CREATE TABLE characters (
            id INTEGER PRIMARY KEY, name TEXT, type TEXT, current_location_id INTEGER
        );
        CREATE TABLE locations (id INTEGER PRIMARY KEY, name TEXT);
        INSERT INTO characters VALUES (2, 'Lost', 'NPC', 404);
        """,
    )

    assert_reconciliation_fails_without_version(
        db_file,
        "characters row 2 current_location_id=404 -> missing locations.id",
    )


def test_malformed_json_is_preserved_and_reported(tmp_path):
    db_file = tmp_path / "bad_json.db"
    execute_sql(
        db_file,
        """
        CREATE TABLE characters (id INTEGER PRIMARY KEY, name TEXT, type TEXT);
        CREATE TABLE mechanical_stats (character_id INTEGER PRIMARY KEY, conditions TEXT);
        INSERT INTO characters VALUES (1, 'JSON Hero', 'PC');
        INSERT INTO mechanical_stats VALUES (1, '[not-json');
        """,
    )

    assert_reconciliation_fails_without_version(
        db_file, r"malformed JSON: mechanical_stats row 1 field conditions='\[not-json'"
    )
    conn = sqlite3.connect(db_file)
    assert conn.execute("SELECT conditions FROM mechanical_stats").fetchone()[0] == "[not-json"
    conn.close()


def test_unknown_custom_columns_fail_without_data_loss(tmp_path):
    db_file = tmp_path / "custom.db"
    execute_sql(
        db_file,
        """
        CREATE TABLE characters (
            id INTEGER PRIMARY KEY, name TEXT, type TEXT, custom_magic TEXT
        );
        INSERT INTO characters VALUES (3, 'Custom', 'PC', 'preserve me');
        """,
    )

    assert_reconciliation_fails_without_version(
        db_file, "incompatible custom schema: unknown columns in characters: custom_magic"
    )
    conn = sqlite3.connect(db_file)
    assert conn.execute("SELECT custom_magic FROM characters").fetchone()[0] == "preserve me"
    conn.close()


def test_failure_during_rebuild_rolls_back_schema_data_and_version(tmp_path, monkeypatch):
    db_file = tmp_path / "rebuild_failure.db"
    execute_sql(
        db_file,
        """
        CREATE TABLE characters (id INTEGER PRIMARY KEY, name TEXT, type TEXT);
        INSERT INTO characters VALUES (5, 'Rollback', 'PC');
        """,
    )

    def fail_after_rebuild(table):
        raise RuntimeError(f"injected failure after rebuilding {table}")

    monkeypatch.setattr(DatabaseManager, "_MIGRATION_FAILURE_INJECTOR", fail_after_rebuild)
    assert_reconciliation_fails_without_version(db_file, "injected failure")

    conn = sqlite3.connect(db_file)
    assert [row[1] for row in conn.execute("PRAGMA table_info(characters)")] == [
        "id", "name", "type"
    ]
    assert conn.execute("SELECT name FROM characters WHERE id = 5").fetchone()[0] == "Rollback"
    conn.close()
    assert Path(f"{db_file}.pre-v6.bak").is_file()


def test_reopening_and_reinitializing_version_6_is_idempotent(tmp_path):
    db_file = tmp_path / "idempotent.db"
    execute_sql(db_file, LEGACY_FIXTURE_SQL)
    manager = DatabaseManager(str(db_file))
    manager.update_character(12, {"current_goal": "Do not change"})
    before = db_file.read_bytes()
    before_manifest = schema_manifest_for(db_file)

    DatabaseManager(str(db_file))
    DatabaseManager(str(db_file))

    assert schema_manifest_for(db_file) == before_manifest
    assert DatabaseManager(str(db_file)).get_character(12)["current_goal"] == "Do not change"
    assert version_of(db_file) == 6
    # SQLite may update file-level change counters, so compare logical data/schema.
    assert db_file.read_bytes() == before


def test_fresh_canonical_and_sparse_databases_have_exact_schema_parity(tmp_path):
    fresh_file = tmp_path / "fresh.db"
    canonical_file = tmp_path / "canonical.db"
    sparse_file = tmp_path / "sparse.db"
    DatabaseManager(str(fresh_file))
    execute_sql(canonical_file, VERSION_1_BASELINE_PATH.read_text(encoding="utf-8"))
    DatabaseManager(str(canonical_file))
    execute_sql(sparse_file, LEGACY_FIXTURE_SQL)
    DatabaseManager(str(sparse_file))

    fresh = schema_manifest_for(fresh_file)
    for reconciled in (canonical_file, sparse_file):
        assert schema_differences(
            schema_manifest_for(reconciled), fresh, REQUIRED_SQL_FRAGMENTS
        ) == []


def test_reconciled_database_supports_every_manager_subsystem(tmp_path):
    db_file = tmp_path / "operational.db"
    execute_sql(db_file, LEGACY_FIXTURE_SQL)
    manager = DatabaseManager(str(db_file))
    character_id = 12

    manager.update_character(character_id, {"current_goal": "Operational check"})
    assert manager.get_character(character_id)["current_goal"] == "Operational check"

    manager.update_emotional_state(character_id, {"trust": 77})
    assert manager.get_emotional_state(character_id)["trust"] == 77
    manager.update_mechanical_stats(character_id, {"hp_current": 8, "conditions": ["prone"]})
    assert manager.get_mechanical_stats(character_id)["conditions"] == ["prone"]

    assert manager.get_location(7)["name"] == "The Lantern Inn"
    manager.update_ambiance(7, {"lighting": "dim"})
    assert manager.get_ambiance(7)["lighting"] == "dim"

    other_id = manager.create_character("Companion")
    manager.update_relationship(character_id, other_id, "ally", 80)
    assert manager.get_relationships(character_id)[0]["trust_score"] == 80

    manager.update_game_day(4)
    manager.insert_conversational_fact(
        "semantic", character_id, "The moon is full.", ["moon"],
        embedding=[1.0, 0.0], created_turn=1, expires_at_turn=45,
    )
    assert manager.get_facts_by_day_range(character_id, 4, 4)[0]["id"] == "semantic"
    semantic = manager.get_facts_by_day_range_with_similarity(
        character_id, [1.0, 0.0], 4, 4
    )
    assert semantic[0]["id"] == "semantic"
    manager.update_conversational_fact("semantic", new_text="The full moon shines.")
    assert manager.get_active_facts(character_id)[0]["fact_text"] == "The full moon shines."

    manager.log_event("Operational event", character_id=character_id)
    assert manager.get_event_log(character_id)[0]["event_text"] == "Operational event"
    manager.update_world_state({"weather": "rain"})
    assert manager.get_world_state()["weather"] == "rain"
    manager.update_scene_graph(7, "door", "closed", [character_id], "dim")
    assert manager.get_scene_graph(7)[0]["object_state"] == "closed"
    assert manager.get_game_state()["game_day"] == 4
    manager.increment_turn()
    manager.increment_turn()
    manager.increment_turn()
    assert not any(fact["id"] == "semantic" for fact in manager.get_active_facts(character_id))

    manager.store_recent_prose(character_id, "A quiet line.")
    assert manager.get_recent_prose(character_id) == "A quiet line."
    manager.store_knowledge_chunk("Known lore", [0.5, 0.5], "lore", character_id)
    assert manager.get_all_knowledge_chunks(character_id)[0]["embedding"] == pytest.approx([0.5, 0.5])
    manager.insert_scene_history(character_id, "Calmer", 3)
    assert manager.get_scene_history(character_id)[0]["emotional_shift_summary"] == "Calmer"

    encounter = manager.start_combat([character_id, other_id])
    assert manager.get_active_combat()["encounter_id"] == encounter
    manager.update_combat_state({"round_number": 2})
    assert manager.get_active_combat()["round_number"] == 2
    manager.end_combat()
    assert manager.get_active_combat() is None

    assert manager.get_dnd_stats(character_id) is None
    manager.update_dnd_stats(
        character_id,
        {"class": "Fighter", "strength": 16, "skills": {"athletics": {"bonus": 6}}},
    )
    assert manager.get_dnd_stats(character_id)["skills"]["athletics"]["bonus"] == 6
