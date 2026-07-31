import pytest
import sqlite3
import json
import os
import sys
from pathlib import Path

# Add the database directory to Python's search path
sys.path.insert(0, str(Path(__file__).parent))

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
    """Test 9: Verifies that day_range filtering works (game_day defaults to 1)."""
    char_id = db.create_character("TimeTraveler")
    
    # Insert standard fact (will default to game_day = 1)
    db.insert_conversational_fact("f_day_1", char_id, "Day 1 happened.", [])
    
    # Should catch game_day 1
    assert len(db.get_facts_by_day_range(char_id, 1, 1)) == 1
    # Should exclude game_day 1
    assert len(db.get_facts_by_day_range(char_id, 2, 5)) == 0


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