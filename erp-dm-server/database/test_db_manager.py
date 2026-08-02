import pytest
import sqlite3
import json
import os
import sys
from pathlib import Path

# Add the database directory to Python's search path
sys.path.insert(0, str(Path(__file__).parent))

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

def test_schema_executes_cleanly(db):
    """Test 1: Schema executes cleanly into a new database."""
    names = db.get_all_character_names()
    assert len(names) == 0  # No crash means schema executed successfully

    conn = db._get_connection()
    version = conn.execute(
        "SELECT version FROM schema_version WHERE id = 1"
    ).fetchone()["version"]
    conn.close()
    assert version == DatabaseManager.LATEST_SCHEMA_VERSION


def test_legacy_database_is_migrated_without_losing_data(tmp_path):
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

    manager = DatabaseManager(str(db_file))
    conn = manager._get_connection()
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

    # Reopening an up-to-date database must not reapply ALTER TABLE statements.
    DatabaseManager(str(db_file))


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
