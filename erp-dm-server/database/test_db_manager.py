import pytest
import sqlite3
import json
import os
from db_manager import DatabaseManager

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
    # Pytest cleans up the tmp_path automatically

# ==========================================
# TESTS
# ==========================================

def test_schema_executes_cleanly(db):
    """Test 1: Schema executes cleanly into a new database."""
    names = db.get_all_character_names()
    assert len(names) == 0  # No crash means schema executed successfully


def test_character_creation_creates_related_rows(db):
    """Test 2: Character creation creates char, emotional state, and mechanical stats."""
    char_id = db.create_character(name="Barnaby", character_type="NPC")
    
    char = db.get_character(char_id)
    assert char["name"] == "Barnaby"
    assert char["type"] == "NPC"

    emotion = db.get_emotional_state(char_id)
    assert emotion is not None
    assert emotion["mood"] == "neutral"  # Default value from schema/creation

    stats = db.get_mechanical_stats(char_id)
    assert stats is not None
    assert stats["hp_current"] == 10  # Default value from schema/creation


def test_character_type_constraint(db):
    """Test 3: Character type constraint (PC, NPC, Monster) is respected."""
    # SQLite CHECK constraints will throw an IntegrityError if violated
    with pytest.raises(sqlite3.IntegrityError):
        db.create_character(name="Glitch", character_type="ALIEN")


def test_narrative_goals_update_and_read(db):
    """Test 4: Narrative goals update/read correctly."""
    char_id = db.create_character("Natasha")
    
    db.update_narrative_goals(
        char_id,
        current_goal="Infiltrate the base",
        hidden_goal="Steal the drive",
        tension=0.85
    )
    
    goals = db.get_narrative_goals(char_id)
    assert goals["current_goal"] == "Infiltrate the base"
    assert goals["hidden_goal"] == "Steal the drive"
    assert goals["tension"] == 0.85
    assert goals["immediate_beat"] is None  # untouched


def test_plot_state_update_and_read(db):
    """Test 5: Plot state (JSON) update/read works."""
    char_id = db.create_character("Grom")
    
    plot_json = json.dumps({"quest_stage": 2, "betrayal_unlocked": True})
    db.update_plot_state(char_id, plot_json)
    
    char = db.get_character(char_id)
    loaded_plot = json.loads(char["plot_state"])
    assert loaded_plot["quest_stage"] == 2
    assert loaded_plot["betrayal_unlocked"] is True


def test_fact_insert_and_read_decodes(db):
    """Test 6: Fact insert/read decodes JSON references and embeddings correctly."""
    char_id = db.create_character("Scholar")
    
    # Insert a fact with JSON-like references and a float vector
    db.insert_conversational_fact(
        fact_id="fact_123",
        character_id=char_id,
        fact_text="The king is allergic to apples.",
        references=["king", "apples", "allergy"],
        embedding=[0.123, -0.456, 0.789]
    )
    
    facts = db.get_active_facts(char_id)
    assert len(facts) == 1
    
    fact = facts[0]
    assert fact["fact_text"] == "The king is allergic to apples."
    assert fact["references"] == ["king", "apples", "allergy"]
    
    # Use pytest.approx because embeddings cast through float32 BLOBs slightly alter float64 precision
    assert fact["embedding"] == pytest.approx([0.123, -0.456, 0.789], rel=1e-5)


def test_fact_update_changes_fields(db):
    """Test 7: Fact update changes text/confidence/reference fields."""
    char_id = db.create_character("Peasant")
    
    db.insert_conversational_fact("f_1", char_id, "Old text", ["old"], confidence=0.5)
    
    db.update_conversational_fact(
        "f_1", 
        new_text="New text", 
        new_references=["new"], 
        confidence=0.95
    )
    
    facts = db.get_active_facts(char_id)
    assert facts[0]["fact_text"] == "New text"
    assert facts[0]["references"] == ["new"]
    assert facts[0]["confidence"] == 0.95


def test_turn_increment_expires_facts(db):
    """Test 8: Turn increment expires expired facts."""
    char_id = db.create_character("Merchant")
    
    # Fact expires at turn 5
    db.insert_conversational_fact("f_expire", char_id, "Sale today only", [], expires_at_turn=5)
    
    # Set current turn to 4
    db.update_game_state({"current_turn": 4})
    
    assert len(db.get_active_facts(char_id)) == 1
    
    # Increment turn (now 5). Facts expiring at <= 5 should disappear.
    new_turn = db.increment_turn()
    assert new_turn == 5
    
    assert len(db.get_active_facts(char_id)) == 0


def test_world_state_upserts(db):
    """Test 9: World state upserts correctly."""
    db.update_world_state({
        "war_active": 1,
        "moon_phase": "crescent",
        "weather": "stormy"
    })
    
    state = db.get_world_state()
    assert state["war_active"] == 1
    assert state["moon_phase"] == "crescent"
    assert state["weather"] == "stormy"


def test_scene_graph_insert_update(db):
    """Test 10: Scene graph insert/update works."""
    # First, insert a location (required for foreign key)
    conn = db._get_connection()
    conn.execute("INSERT INTO locations (id, name) VALUES (1, 'Tavern')")
    conn.commit()
    conn.close()
    
    # Insert object
    db.update_scene_graph(
        location_id=1, 
        object_name="front_door", 
        object_state="closed", 
        npc_present=[2, 3], 
        visibility="dim"
    )
    
    graph = db.get_scene_graph(1)
    assert len(graph) == 1
    assert graph[0]["object_name"] == "front_door"
    assert graph[0]["object_state"] == "closed"
    assert graph[0]["npc_present"] == [2, 3] # Evaluates valid JSON array
    
    # Update same object
    db.update_scene_graph(1, "front_door", object_state="open", npc_present=[2])
    
    graph = db.get_scene_graph(1)
    assert len(graph) == 1  # Did not duplicate
    assert graph[0]["object_state"] == "open"
    assert graph[0]["npc_present"] == [2]


def test_knowledge_chunk_embeddings_round_trip(db):
    """Test 11: Knowledge chunk embeddings round-trip correctly."""
    char_id = db.create_character("Librarian")
    
    db.store_knowledge_chunk(
        chunk_text="The ancient elves built the monolithic bridge.",
        embedding=[0.88, 0.77, 0.66],
        source_type="lore_book",
        associated_character_id=char_id
    )
    
    chunks = db.get_all_knowledge_chunks(char_id)
    assert len(chunks) == 1
    assert chunks[0]["chunk_text"] == "The ancient elves built the monolithic bridge."
    assert chunks[0]["source_type"] == "lore_book"
    assert chunks[0]["embedding"] == pytest.approx([0.88, 0.77, 0.66], rel=1e-5)