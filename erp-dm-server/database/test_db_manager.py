import pytest
import sqlite3
import json
import os
import shutil
import sys
from pathlib import Path

# Add the database directory to Python's search path
sys.path.insert(0, str(Path(__file__).parent))

import db_manager as db_manager_module
from db_manager import DatabaseManager, cosine_similarity

# ==========================================
# FIXTURE: FRESH DATABASE PER TEST
# ==========================================
@pytest.fixture
def db(tmp_path):
    """
    Creates a fresh, isolated database in a temporary directory for every test.
    This acts just as fast as :memory: but allows db_manager.py's os.makedirs to work safely.
    """
    db_file = tmp_path / "test_game.db"
    manager = DatabaseManager(str(db_file))
    yield manager

# ==========================================
# TESTS
# ==========================================

def test_schema_executes_cleanly(db, tmp_path):
    """A fresh database contains the architecture's required schema."""
    db_path = Path(db.db_path)
    assert db_path.parent == tmp_path
    assert db_path.is_file()

    required_tables = {
        "schema_version",
        "characters",
        "locations",
        "emotional_state",
        "dnd_stats",
        "conversational_facts",
        "event_log",
        "working_memory",
        "knowledge_chunks",
        "world_state",
        "scene_graph",
        "game_state",
    }
    required_columns = {
        "characters": {"prose_fingerprint", "status", "is_active"},
        "conversational_facts": {"game_day"},
        "game_state": {"game_day"},
    }

    conn = db._get_connection()
    actual_tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    missing_tables = required_tables - actual_tables

    missing_columns = {
        table: sorted(
            columns
            - {
                row["name"]
                for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            }
        )
        for table, columns in required_columns.items()
    }
    missing_columns = {
        table: columns for table, columns in missing_columns.items() if columns
    }

    version = conn.execute(
        "SELECT version FROM schema_version WHERE id = 1"
    ).fetchone()["version"]
    conn.close()

    assert not missing_tables, f"Missing required schema tables: {sorted(missing_tables)}"
    assert not missing_columns, f"Missing critical schema columns: {missing_columns}"
    assert version == DatabaseManager.LATEST_SCHEMA_VERSION


def _create_versioned_database(db_file, version):
    conn = sqlite3.connect(db_file)
    conn.executescript(f"""
        CREATE TABLE schema_version (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            version INTEGER NOT NULL
        );
        INSERT INTO schema_version (id, version) VALUES (1, {version});
        CREATE TABLE characters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
        );
        CREATE TABLE game_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            current_turn INTEGER DEFAULT 0,
            game_day INTEGER DEFAULT 1
        );
        INSERT INTO characters (name) VALUES ('Versioned Hero');
        INSERT INTO game_state (id, current_turn, game_day) VALUES (1, 19, 6);
    """)
    conn.close()


def _use_temporary_migrations(monkeypatch, tmp_path):
    database_dir = tmp_path / "database_code"
    migrations_dir = database_dir / "migrations"
    migrations_dir.mkdir(parents=True)
    source_dir = Path(db_manager_module.__file__).parent / "migrations"
    for migration in source_dir.glob("*.sql"):
        shutil.copy2(migration, migrations_dir / migration.name)
    monkeypatch.setattr(db_manager_module, "__file__", str(database_dir / "db_manager.py"))
    return migrations_dir


def test_unversioned_legacy_database_is_migrated_without_losing_data(tmp_path):
    db_file = tmp_path / "legacy_game.db"
    conn = sqlite3.connect(db_file)
    conn.executescript("""
        CREATE TABLE characters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
        );
        CREATE TABLE game_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            current_turn INTEGER DEFAULT 0
        );
        INSERT INTO characters (name) VALUES ('Legacy Hero');
        INSERT INTO game_state (id, current_turn) VALUES (1, 42);
    """)
    conn.close()

    DatabaseManager(str(db_file))
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    character = conn.execute("SELECT * FROM characters WHERE id = 1").fetchone()
    game_state = conn.execute("SELECT * FROM game_state WHERE id = 1").fetchone()
    version = conn.execute(
        "SELECT version FROM schema_version WHERE id = 1"
    ).fetchone()["version"]
    conn.close()

    assert character["name"] == "Legacy Hero"
    assert character["prose_fingerprint"] is None
    assert game_state["current_turn"] == 42
    assert game_state["game_day"] == 1
    assert version == DatabaseManager.LATEST_SCHEMA_VERSION


def test_version_2_database_receives_only_remaining_migration(tmp_path):
    db_file = tmp_path / "version_2.db"
    _create_versioned_database(db_file, version=2)

    DatabaseManager(str(db_file))
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    character = conn.execute("SELECT * FROM characters WHERE id = 1").fetchone()
    state = conn.execute("SELECT * FROM game_state WHERE id = 1").fetchone()
    version = conn.execute(
        "SELECT version FROM schema_version WHERE id = 1"
    ).fetchone()["version"]
    conn.close()

    assert character["name"] == "Versioned Hero"
    assert character["prose_fingerprint"] is None
    assert state["current_turn"] == 19
    assert state["game_day"] == 6
    assert version == DatabaseManager.LATEST_SCHEMA_VERSION


def test_current_database_can_be_opened_repeatedly_without_changes(tmp_path):
    db_file = tmp_path / "current.db"
    manager = DatabaseManager(str(db_file))
    character_id = manager.create_character("Persistent Hero", full_card_text="Original card")
    manager.update_game_day(8)

    for _ in range(3):
        DatabaseManager(str(db_file))

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    character = conn.execute(
        "SELECT name, full_card_text FROM characters WHERE id = ?", (character_id,)
    ).fetchone()
    state = conn.execute(
        "SELECT current_turn, game_day FROM game_state WHERE id = 1"
    ).fetchone()
    version = conn.execute(
        "SELECT version FROM schema_version WHERE id = 1"
    ).fetchone()["version"]
    conn.close()

    assert dict(character) == {
        "name": "Persistent Hero",
        "full_card_text": "Original card",
    }
    assert dict(state) == {"current_turn": 0, "game_day": 8}
    assert version == DatabaseManager.LATEST_SCHEMA_VERSION


def test_future_schema_version_is_rejected_without_modifying_data(tmp_path):
    db_file = tmp_path / "future.db"
    future_version = DatabaseManager.LATEST_SCHEMA_VERSION + 1
    _create_versioned_database(db_file, version=future_version)

    with pytest.raises(RuntimeError, match="is newer than supported"):
        DatabaseManager(str(db_file))

    conn = sqlite3.connect(db_file)
    name = conn.execute("SELECT name FROM characters WHERE id = 1").fetchone()[0]
    state = conn.execute(
        "SELECT current_turn, game_day FROM game_state WHERE id = 1"
    ).fetchone()
    version = conn.execute(
        "SELECT version FROM schema_version WHERE id = 1"
    ).fetchone()[0]
    conn.close()

    assert name == "Versioned Hero"
    assert state == (19, 6)
    assert version == future_version


def test_missing_migration_file_leaves_version_and_data_unchanged(
    tmp_path, monkeypatch
):
    migrations_dir = _use_temporary_migrations(monkeypatch, tmp_path)
    (migrations_dir / "003_add_prose_fingerprint.sql").unlink()
    db_file = tmp_path / "missing_migration.db"
    _create_versioned_database(db_file, version=2)

    with pytest.raises(FileNotFoundError, match="003_add_prose_fingerprint.sql"):
        DatabaseManager(str(db_file))

    conn = sqlite3.connect(db_file)
    columns = {
        row[1] for row in conn.execute('PRAGMA table_info("characters")').fetchall()
    }
    name = conn.execute("SELECT name FROM characters WHERE id = 1").fetchone()[0]
    state = conn.execute(
        "SELECT current_turn, game_day FROM game_state WHERE id = 1"
    ).fetchone()
    version = conn.execute(
        "SELECT version FROM schema_version WHERE id = 1"
    ).fetchone()[0]
    conn.close()

    assert "prose_fingerprint" not in columns
    assert name == "Versioned Hero"
    assert state == (19, 6)
    assert version == 2


def test_failed_migration_rolls_back_schema_version_and_prior_migrations(
    tmp_path, monkeypatch
):
    migrations_dir = _use_temporary_migrations(monkeypatch, tmp_path)
    (migrations_dir / "003_add_prose_fingerprint.sql").write_text(
        "ALTER TABLE characters ADD COLUMN prose_fingerprint TEXT NOT NULL;",
        encoding="utf-8",
    )
    db_file = tmp_path / "failed_migration.db"
    conn = sqlite3.connect(db_file)
    conn.executescript("""
        CREATE TABLE characters (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE game_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            current_turn INTEGER DEFAULT 0
        );
        INSERT INTO characters (id, name) VALUES (1, 'Rollback Hero');
        INSERT INTO game_state (id, current_turn) VALUES (1, 27);
    """)
    conn.close()

    with pytest.raises(sqlite3.OperationalError, match="NOT NULL"):
        DatabaseManager(str(db_file))

    conn = sqlite3.connect(db_file)
    character_columns = {
        row[1] for row in conn.execute('PRAGMA table_info("characters")').fetchall()
    }
    game_state_columns = {
        row[1] for row in conn.execute('PRAGMA table_info("game_state")').fetchall()
    }
    name = conn.execute("SELECT name FROM characters WHERE id = 1").fetchone()[0]
    current_turn = conn.execute(
        "SELECT current_turn FROM game_state WHERE id = 1"
    ).fetchone()[0]
    version_row = conn.execute(
        "SELECT version FROM schema_version WHERE id = 1"
    ).fetchone()
    conn.close()

    assert "prose_fingerprint" not in character_columns
    assert "game_day" not in game_state_columns
    assert name == "Rollback Hero"
    assert current_turn == 27
    assert version_row is None


def test_realistic_legacy_campaign_fixture_preserves_representative_data(tmp_path):
    fixture_source = Path(__file__).parent / "test_fixtures" / "legacy_campaign.sql"
    fixture_copy = tmp_path / "legacy_campaign.sql"
    shutil.copy2(fixture_source, fixture_copy)
    db_file = tmp_path / "legacy_campaign.db"
    conn = sqlite3.connect(db_file)
    conn.executescript(fixture_copy.read_text(encoding="utf-8"))
    before = {
        "location": conn.execute(
            "SELECT name, region, description FROM locations WHERE id = 7"
        ).fetchone(),
        "character": conn.execute(
            "SELECT name, type, current_goal, tension, plot_state, current_location_id "
            "FROM characters WHERE id = 12"
        ).fetchone(),
        "emotion": conn.execute(
            "SELECT trust, fear, mood FROM emotional_state WHERE character_id = 12"
        ).fetchone(),
        "fact": conn.execute(
            "SELECT fact_text, fact_references, importance, confidence, is_active "
            "FROM conversational_facts WHERE id = 'fact_bridge'"
        ).fetchone(),
        "game_state": conn.execute(
            "SELECT current_location_id, current_scene_type, combat_active, current_turn "
            "FROM game_state WHERE id = 1"
        ).fetchone(),
    }
    conn.close()

    DatabaseManager(str(db_file))

    conn = sqlite3.connect(db_file)
    after = {
        "location": conn.execute(
            "SELECT name, region, description FROM locations WHERE id = 7"
        ).fetchone(),
        "character": conn.execute(
            "SELECT name, type, current_goal, tension, plot_state, current_location_id "
            "FROM characters WHERE id = 12"
        ).fetchone(),
        "emotion": conn.execute(
            "SELECT trust, fear, mood FROM emotional_state WHERE character_id = 12"
        ).fetchone(),
        "fact": conn.execute(
            "SELECT fact_text, fact_references, importance, confidence, is_active "
            "FROM conversational_facts WHERE id = 'fact_bridge'"
        ).fetchone(),
        "game_state": conn.execute(
            "SELECT current_location_id, current_scene_type, combat_active, current_turn "
            "FROM game_state WHERE id = 1"
        ).fetchone(),
    }
    migrated_values = conn.execute(
        "SELECT prose_fingerprint FROM characters WHERE id = 12"
    ).fetchone()[0], conn.execute(
        "SELECT game_day FROM game_state WHERE id = 1"
    ).fetchone()[0]
    version = conn.execute(
        "SELECT version FROM schema_version WHERE id = 1"
    ).fetchone()[0]
    conn.close()

    assert before == {
        "location": ("The Lantern Inn", "North Road", "A crowded coaching inn."),
        "character": (
            "Mara Voss", "NPC", "Protect the sealed letter", 0.75,
            '{"letter_hidden": true}', 7,
        ),
        "emotion": (63, 27, "guarded"),
        "fact": (
            "The eastern bridge is watched.",
            '["eastern bridge", "watchers"]', 0.8, 0.95, 1,
        ),
        "game_state": (7, "investigation", 0, 42),
    }
    assert after == before
    assert migrated_values == (None, 1)
    assert version == DatabaseManager.LATEST_SCHEMA_VERSION


def test_character_creation_creates_related_rows(db):
    """Test 2: Character creation creates char, emotional state, and mechanical stats."""
    char_id = db.create_character(name="Barnaby", character_type="NPC")
    
    char = db.get_character(char_id)
    assert char["name"] == "Barnaby"

    emotion = db.get_emotional_state(char_id)
    assert emotion is not None
    assert emotion["mood"] == "neutral"

    stats = db.get_mechanical_stats(char_id)
    assert stats is not None


def test_character_type_constraint(db):
    """Test 3: Character type constraint (PC, NPC, Monster) is respected."""
    with pytest.raises(sqlite3.IntegrityError):
        db.create_character(name="Glitch", character_type="ALIEN")


def test_narrative_goals_update_and_read(db):
    """Test 4: Narrative goals update/read correctly."""
    char_id = db.create_character("Natasha")
    db.update_narrative_goals(char_id, current_goal="Infiltrate the base", tension=0.85)
    
    goals = db.get_narrative_goals(char_id)
    assert goals["current_goal"] == "Infiltrate the base"
    assert goals["tension"] == 0.85


def test_plot_state_update_and_read(db):
    """Test 5: Plot state (JSON) update/read works."""
    char_id = db.create_character("Grom")
    db.update_plot_state(char_id, json.dumps({"quest_stage": 2}))
    
    char = db.get_character(char_id)
    assert json.loads(char["plot_state"])["quest_stage"] == 2


def test_fact_insert_and_read_decodes(db):
    """Test 6: Fact insert/read decodes JSON references, embeddings, and new fact_type logic."""
    char_id = db.create_character("Scholar")
    source_id = db.create_character("Peasant")
    
    db.insert_conversational_fact(
        fact_id="fact_123",
        character_id=char_id,
        fact_text="The king is allergic to apples.",
        references=["king", "apples"],
        embedding=[0.123, -0.456, 0.789],
        fact_type="rumor_fact",
        source_character_id=source_id
    )
    
    facts = db.get_active_facts(char_id)
    assert len(facts) == 1
    
    fact = facts[0]
    assert fact["fact_text"] == "The king is allergic to apples."
    assert fact["references"] == ["king", "apples"]
    assert fact["fact_type"] == "rumor_fact"
    assert fact["source_character_id"] == source_id
    assert fact["embedding"] == pytest.approx([0.123, -0.456, 0.789], rel=1e-5)


def test_fact_update_changes_fields(db):
    """Test 7: Fact update changes text/confidence/reference and newly added type fields."""
    char_id = db.create_character("Peasant")
    
    db.insert_conversational_fact("f_1", char_id, "Old text", ["old"], confidence=0.5)
    
    db.update_conversational_fact(
        "f_1", 
        new_text="New text", 
        new_references=["new"], 
        confidence=0.95,
        fact_type="belief_fact",
        source_character_id=99
    )
    
    facts = db.get_active_facts(char_id)
    assert facts[0]["fact_text"] == "New text"
    assert facts[0]["fact_type"] == "belief_fact"
    assert facts[0]["source_character_id"] == 99


def test_fact_filtering_by_type(db):
    """Test 8: Ensures get_facts_by_type, get_belief_facts, and get_rumor_facts work."""
    char_id = db.create_character("Spy")
    informant_id = db.create_character("Informant")
    
    # Insert one of each type
    db.insert_conversational_fact("f_world", char_id, "Sky is blue", [], fact_type="world_fact")
    db.insert_conversational_fact("f_belief", char_id, "Trusts the king", [], fact_type="belief_fact", source_character_id=informant_id)
    db.insert_conversational_fact("f_rumor", char_id, "Gold in the hills", [], fact_type="rumor_fact")
    
    assert len(db.get_facts_by_type(char_id, "world_fact")) == 1
    assert len(db.get_belief_facts_by_source(informant_id)) == 1
    assert len(db.get_rumor_facts(char_id)) == 1


def test_get_facts_by_day_range(db):
    """Facts use the current campaign day unless an explicit day is supplied."""
    char_id = db.create_character("TimeTraveler")

    db.insert_conversational_fact("f_day_1", char_id, "Day 1 happened.", [])
    db.update_game_day(5)
    db.insert_conversational_fact("f_day_5", char_id, "Day 5 happened.", [])
    db.insert_conversational_fact(
        "f_day_3",
        char_id,
        "A remembered event from day 3.",
        [],
        game_day=3,
    )

    assert [fact["id"] for fact in db.get_facts_by_day_range(char_id, 1, 1)] == ["f_day_1"]
    assert [fact["id"] for fact in db.get_facts_by_day_range(char_id, 3, 3)] == ["f_day_3"]
    assert [fact["id"] for fact in db.get_facts_by_day_range(char_id, 5, 5)] == ["f_day_5"]

    active_facts = {fact["id"]: fact for fact in db.get_active_facts(char_id)}
    assert active_facts["f_day_1"]["game_day"] == 1
    assert active_facts["f_day_3"]["game_day"] == 3
    assert active_facts["f_day_5"]["game_day"] == 5


@pytest.mark.parametrize("invalid_day", [0, -1, 1.5, "2", True])
def test_insert_fact_rejects_invalid_game_day(db, invalid_day):
    char_id = db.create_character("Chronologist")

    with pytest.raises(ValueError, match="game_day must be a positive integer"):
        db.insert_conversational_fact(
            "f_invalid_day",
            char_id,
            "This fact has an invalid date.",
            [],
            game_day=invalid_day,
        )

    assert db.get_active_facts(char_id) == []


def test_get_facts_by_day_range_decodes_references_and_embeddings(db):
    """Temporal retrieval returns the same decoded shape as normal fact retrieval."""
    char_id = db.create_character("Archivist")
    db.insert_conversational_fact(
        "f_temporal",
        char_id,
        "The bridge collapsed.",
        ["bridge", "collapse"],
        embedding=[0.25, 0.75],
    )

    facts = db.get_facts_by_day_range(char_id, 1, 1)

    assert facts[0]["references"] == ["bridge", "collapse"]
    assert facts[0]["embedding"] == pytest.approx([0.25, 0.75])


def test_cosine_similarity_handles_common_vector_relationships():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_similarity_returns_zero_for_empty_or_zero_vectors():
    assert cosine_similarity([], []) == 0.0
    assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0


def test_cosine_similarity_rejects_dimension_mismatch():
    with pytest.raises(ValueError, match="Embedding dimensions must match"):
        cosine_similarity([1.0], [1.0, 2.0])


def test_similarity_retrieval_ranks_and_limits_day_range_results(db):
    """Combined retrieval uses cosine similarity after temporal filtering."""
    char_id = db.create_character("Historian")
    db.insert_conversational_fact(
        "f_similar",
        char_id,
        "A goblin attacked at the bridge.",
        ["goblin", "bridge"],
        embedding=[1.0, 0.0],
    )
    db.insert_conversational_fact(
        "f_less_similar",
        char_id,
        "The innkeeper mentioned wolves.",
        ["innkeeper", "wolves"],
        embedding=[0.5, 0.5],
    )

    facts = db.get_facts_by_day_range_with_similarity(
        char_id,
        [1.0, 0.0],
        start_day=1,
        end_day=1,
        limit=1,
    )

    assert [fact["id"] for fact in facts] == ["f_similar"]
    assert facts[0]["similarity"] == pytest.approx(1.0)


def test_game_day_can_be_updated_and_advanced(db):
    """The campaign day persists and advances one day at a time."""
    assert db.get_game_state()["game_day"] == 1

    db.update_game_day(7)
    assert db.get_game_state()["game_day"] == 7
    assert db.advance_game_day() == 8
    assert db.get_game_state()["game_day"] == 8


def test_prose_fingerprint_can_be_replaced(db):
    """Only the character's current prose fingerprint is retained."""
    char_id = db.create_character("Bard")
    assert db.get_prose_fingerprint(char_id) is None

    db.update_prose_fingerprint(char_id, "Measured, lyrical, and melancholy.")
    assert db.get_prose_fingerprint(char_id) == "Measured, lyrical, and melancholy."

    db.update_prose_fingerprint(char_id, "Clipped sentences with rising urgency.")
    assert db.get_prose_fingerprint(char_id) == "Clipped sentences with rising urgency."


def test_turn_increment_expires_facts(db):
    """Test 10: Turn increment expires expired facts."""
    char_id = db.create_character("Merchant")
    db.insert_conversational_fact("f_expire", char_id, "Sale today only", [], expires_at_turn=5)
    db.update_game_state({"current_turn": 4})
    
    assert len(db.get_active_facts(char_id)) == 1
    
    # Increment turn (now 5). Facts expiring at <= 5 should disappear.
    new_turn = db.increment_turn()
    assert len(db.get_active_facts(char_id)) == 0


def test_dnd_stats_upsert_and_json_parsing(db):
    """Test 11: Validates that dnd_stats correctly manages JSON fields and normal fields."""
    char_id = db.create_character("Paladin")
    
    updates = {
        "class": "Paladin",
        "level": 5,
        "strength": 18,
        "skills": {"athletics": {"bonus": 7, "proficiency": True}},
        "armor_proficiencies": ["light", "medium", "heavy", "shields"],
        "equipment": [
            {"name": "Longsword +1", "damage_dice": "1d8+5"},
            {"name": "Chain Mail", "ac": 16}
        ]
    }
    
    db.update_dnd_stats(char_id, updates)
    stats = db.get_dnd_stats(char_id)
    
    assert stats["class"] == "Paladin"
    assert stats["strength"] == 18
    # JSON Arrays/Objects should be properly deserialized
    assert stats["skills"]["athletics"]["proficiency"] is True
    assert "heavy" in stats["armor_proficiencies"]
    assert len(stats["equipment"]) == 2
    assert stats["equipment"][0]["name"] == "Longsword +1"


def test_world_state_upserts(db):
    """Test 12: World state upserts correctly."""
    db.update_world_state({"war_active": 1, "moon_phase": "crescent"})
    state = db.get_world_state()
    assert state["war_active"] == 1
    assert state["moon_phase"] == "crescent"


def test_scene_graph_insert_update(db):
    """Test 13: Scene graph insert/update works with lists."""
    conn = db._get_connection()
    conn.execute("INSERT INTO locations (id, name) VALUES (1, 'Tavern')")
    conn.commit()
    conn.close()
    
    db.update_scene_graph(1, "front_door", "closed", [2, 3], "dim")
    graph = db.get_scene_graph(1)
    
    assert len(graph) == 1
    assert graph[0]["npc_present"] == [2, 3] # Valid JSON Array


def test_knowledge_chunk_embeddings_round_trip(db):
    """Test 14: Knowledge chunk embeddings round-trip correctly."""
    char_id = db.create_character("Librarian")
    db.store_knowledge_chunk("Elves built the bridge.", [0.88, 0.77], "lore_book", char_id)
    
    chunks = db.get_all_knowledge_chunks(char_id)
    assert chunks[0]["embedding"] == pytest.approx([0.88, 0.77], rel=1e-5)
