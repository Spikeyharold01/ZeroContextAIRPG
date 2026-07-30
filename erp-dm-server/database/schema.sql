-- ============================================================
-- 1. CORE CHARACTERS
-- ============================================================
CREATE TABLE characters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT CHECK(type IN ('PC', 'NPC', 'Monster')) DEFAULT 'NPC',
    full_card_text TEXT,
    character_core TEXT,
    speech_patterns TEXT,
    mannerisms TEXT,
    physical_description TEXT,
    goals TEXT,
    scenario_plot TEXT,
    current_goal TEXT,
    hidden_goal TEXT,
    immediate_beat TEXT,
    long_arc TEXT,
    tension FLOAT DEFAULT 0.5,
    current_location_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (current_location_id) REFERENCES locations(id)
);

-- ============================================================
-- 2. LOCATIONS
-- ============================================================
CREATE TABLE locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    region TEXT,
    description TEXT
);

-- ============================================================
-- 3. AMBIANCE
-- ============================================================
CREATE TABLE ambiance_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id INTEGER UNIQUE,
    lighting TEXT,
    weather TEXT,
    soundscape TEXT,
    vibe TEXT,
    smell TEXT,
    FOREIGN KEY (location_id) REFERENCES locations(id)
);

-- ============================================================
-- 4. EMOTIONAL STATE
-- ============================================================
CREATE TABLE emotional_state (
    character_id INTEGER PRIMARY KEY,
    trust INTEGER DEFAULT 50,
    fear INTEGER DEFAULT 10,
    arousal INTEGER DEFAULT 20,
    tension INTEGER DEFAULT 30,
    intimacy INTEGER DEFAULT 40,
    mood TEXT DEFAULT 'neutral',
    emotional_shift TEXT,
    FOREIGN KEY (character_id) REFERENCES characters(id)
);

-- ============================================================
-- 5. MECHANICAL STATS
-- ============================================================
CREATE TABLE mechanical_stats (
    character_id INTEGER PRIMARY KEY,
    strength INTEGER DEFAULT 10,
    dexterity INTEGER DEFAULT 10,
    constitution INTEGER DEFAULT 10,
    intelligence INTEGER DEFAULT 10,
    wisdom INTEGER DEFAULT 10,
    charisma INTEGER DEFAULT 10,
    hp_current INTEGER DEFAULT 10,
    hp_max INTEGER DEFAULT 10,
    armor_class INTEGER DEFAULT 10,
    proficiency_bonus INTEGER DEFAULT 2,
    level INTEGER DEFAULT 1,
    conditions TEXT DEFAULT '[]',
    FOREIGN KEY (character_id) REFERENCES characters(id)
);

-- ============================================================
-- 6. INVENTORY
-- ============================================================
CREATE TABLE inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER,
    item_name TEXT NOT NULL,
    item_type TEXT,
    quantity INTEGER DEFAULT 1,
    is_equipped BOOLEAN DEFAULT 0,
    damage_dice TEXT,
    FOREIGN KEY (character_id) REFERENCES characters(id)
);

-- ============================================================
-- 7. RELATIONSHIPS
-- ============================================================
CREATE TABLE relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_a_id INTEGER,
    character_b_id INTEGER,
    relationship_type TEXT,
    trust_score INTEGER DEFAULT 50,
    FOREIGN KEY (character_a_id) REFERENCES characters(id),
    FOREIGN KEY (character_b_id) REFERENCES characters(id)
);

-- ============================================================
-- 8. COMBAT STATE
-- ============================================================
CREATE TABLE combat_state (
    encounter_id INTEGER PRIMARY KEY AUTOINCREMENT,
    is_active BOOLEAN DEFAULT 0,
    turn_order TEXT,
    current_turn INTEGER DEFAULT 0,
    round_number INTEGER DEFAULT 1
);

-- ============================================================
-- 9. SCENE HISTORY
-- ============================================================
CREATE TABLE scene_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    emotional_shift_summary TEXT,
    turn INTEGER,
    FOREIGN KEY (character_id) REFERENCES characters(id)
);

-- ============================================================
-- 10. CONVERSATIONAL FACTS (with Provenance)
-- ============================================================
CREATE TABLE conversational_facts (
    id TEXT PRIMARY KEY,
    character_id INTEGER,
    fact_text TEXT NOT NULL,
    references TEXT,
    embedding BLOB,
    importance FLOAT DEFAULT 0.5,
    confidence FLOAT DEFAULT 0.9,
    source_type TEXT,
    created_turn INTEGER,
    last_referenced_turn INTEGER,
    expires_at_turn INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT 1,
    FOREIGN KEY (character_id) REFERENCES characters(id)
);

-- ============================================================
-- 11. EVENT LOG
-- ============================================================
CREATE TABLE event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_text TEXT NOT NULL,
    event_type TEXT,
    turn INTEGER,
    importance FLOAT DEFAULT 0.5,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    character_id INTEGER,
    FOREIGN KEY (character_id) REFERENCES characters(id)
);

-- ============================================================
-- 12. WORKING MEMORY
-- ============================================================
CREATE TABLE working_memory (
    character_id INTEGER PRIMARY KEY,
    prose_snippet TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (character_id) REFERENCES characters(id)
);

-- ============================================================
-- 13. KNOWLEDGE CHUNKS (RAG)
-- ============================================================
CREATE TABLE knowledge_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_text TEXT NOT NULL,
    embedding BLOB,
    source_type TEXT,
    associated_character_id INTEGER,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (associated_character_id) REFERENCES characters(id)
);

-- ============================================================
-- 14. WORLD STATE
-- ============================================================
CREATE TABLE world_state (
    id INTEGER PRIMARY KEY CHECK (id = 1) DEFAULT 1,
    war_active BOOLEAN DEFAULT 0,
    bridge_destroyed BOOLEAN DEFAULT 0,
    festival_active BOOLEAN DEFAULT 0,
    moon_phase TEXT,
    weather TEXT,
    additional_state TEXT
);

-- ============================================================
-- 15. SCENE GRAPH
-- ============================================================
CREATE TABLE scene_graph (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id INTEGER,
    object_name TEXT,
    object_state TEXT,
    npc_present TEXT,
    visibility TEXT,
    FOREIGN KEY (location_id) REFERENCES locations(id)
);

-- ============================================================
-- 16. GAME STATE (with Turn Counter)
-- ============================================================
CREATE TABLE game_state (
    id INTEGER PRIMARY KEY CHECK (id = 1) DEFAULT 1,
    current_location_id INTEGER,
    current_scene_type TEXT,
    combat_active BOOLEAN DEFAULT 0,
    current_turn INTEGER DEFAULT 0,
    FOREIGN KEY (current_location_id) REFERENCES locations(id)
);