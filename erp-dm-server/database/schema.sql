-- ============================================================
-- ADAPTIVE RPG/ERP ENGINE v5.2 – COMPLETE SCHEMA
-- ============================================================

-- 1. CORE CHARACTERS
CREATE TABLE characters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT CHECK(type IN ('PC', 'NPC', 'Monster')) DEFAULT 'NPC',
    
    -- Raw card (backup only – never used in prompts)
    full_card_text TEXT,
    
    -- Compressed fields (populated once on upload)
    character_core TEXT,
    speech_patterns TEXT,
    mannerisms TEXT,
    physical_description TEXT,
    goals TEXT,
    scenario_plot TEXT,
    
    -- Narrative goals (expanded plot state)
    current_goal TEXT,
    hidden_goal TEXT,
    immediate_beat TEXT,
    long_arc TEXT,
    tension FLOAT DEFAULT 0.5,
    
    -- Generic plot state (miscellaneous JSON)
    plot_state TEXT,
    
    -- Location
    current_location_id INTEGER,
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (current_location_id) REFERENCES locations(id)
);

-- 2. LOCATIONS
CREATE TABLE locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    region TEXT,
    description TEXT
);

-- 3. AMBIANCE STATE
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

-- 4. EMOTIONAL STATE
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

-- 5. MECHANICAL STATS
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
    conditions TEXT DEFAULT '[]',   -- JSON array
    FOREIGN KEY (character_id) REFERENCES characters(id)
);

-- 6. INVENTORY
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

-- 7. RELATIONSHIPS
CREATE TABLE relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_a_id INTEGER,
    character_b_id INTEGER,
    relationship_type TEXT,
    trust_score INTEGER DEFAULT 50,
    FOREIGN KEY (character_a_id) REFERENCES characters(id),
    FOREIGN KEY (character_b_id) REFERENCES characters(id)
);

-- 8. COMBAT STATE
CREATE TABLE combat_state (
    encounter_id INTEGER PRIMARY KEY AUTOINCREMENT,
    is_active BOOLEAN DEFAULT 0,
    turn_order TEXT,          -- JSON array
    current_turn INTEGER DEFAULT 0,
    round_number INTEGER DEFAULT 1
);

-- 9. SCENE HISTORY (emotional shifts log)
CREATE TABLE scene_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER,
    emotional_shift_summary TEXT,
    turn INTEGER,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (character_id) REFERENCES characters(id)
);

-- 10. CONVERSATIONAL FACTS (with provenance)
CREATE TABLE conversational_facts (
    id TEXT PRIMARY KEY,
    character_id INTEGER,
    fact_text TEXT NOT NULL,
    references TEXT,                  -- JSON array of keywords
    embedding BLOB,                   -- float32 vector
    importance FLOAT DEFAULT 0.5,
    confidence FLOAT DEFAULT 0.9,
    source_type TEXT,                 -- 'narrative', 'user', 'system'
    created_turn INTEGER DEFAULT 0,
    last_referenced_turn INTEGER DEFAULT 0,
    expires_at_turn INTEGER,          -- NULL = never expires
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT 1,
    FOREIGN KEY (character_id) REFERENCES characters(id)
);

-- 11. EVENT LOG (major events)
CREATE TABLE event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_text TEXT NOT NULL,
    event_type TEXT,
    turn INTEGER,
    importance FLOAT DEFAULT 0.5,
    character_id INTEGER,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (character_id) REFERENCES characters(id)
);

-- 12. WORKING MEMORY (last 300 tokens of prose)
CREATE TABLE working_memory (
    character_id INTEGER PRIMARY KEY,
    prose_snippet TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (character_id) REFERENCES characters(id)
);

-- 13. KNOWLEDGE CHUNKS (RAG)
CREATE TABLE knowledge_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_text TEXT NOT NULL,
    embedding BLOB,               -- float32 vector
    source_type TEXT,
    associated_character_id INTEGER,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (associated_character_id) REFERENCES characters(id)
);

-- 14. WORLD STATE (global simulation state)
CREATE TABLE world_state (
    id INTEGER PRIMARY KEY CHECK (id = 1) DEFAULT 1,
    war_active BOOLEAN DEFAULT 0,
    bridge_destroyed BOOLEAN DEFAULT 0,
    festival_active BOOLEAN DEFAULT 0,
    moon_phase TEXT,
    weather TEXT,
    additional_state TEXT        -- JSON for extensibility
);

-- 15. SCENE GRAPH (objects, NPCs, visibility per location)
CREATE TABLE scene_graph (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id INTEGER,
    object_name TEXT,
    object_state TEXT,          -- e.g. 'open', 'closed', 'locked', 'unlocked', 'lit', 'extinguished'
    npc_present TEXT,           -- JSON array of character IDs
    visibility TEXT,            -- 'clear', 'dim', 'dark'
    FOREIGN KEY (location_id) REFERENCES locations(id)
);

-- 16. GAME STATE (single row)
CREATE TABLE game_state (
    id INTEGER PRIMARY KEY CHECK (id = 1) DEFAULT 1,
    current_location_id INTEGER,
    current_scene_type TEXT,
    combat_active BOOLEAN DEFAULT 0,
    current_turn INTEGER DEFAULT 0,
    FOREIGN KEY (current_location_id) REFERENCES locations(id)
);

-- ============================================================
-- INDEXES FOR PERFORMANCE
-- ============================================================
CREATE INDEX idx_characters_location ON characters(current_location_id);
CREATE INDEX idx_characters_name ON characters(name);
CREATE INDEX idx_facts_character ON conversational_facts(character_id);
CREATE INDEX idx_facts_active ON conversational_facts(is_active);
CREATE INDEX idx_facts_expiry ON conversational_facts(expires_at_turn);
CREATE INDEX idx_knowledge_chunks_character ON knowledge_chunks(associated_character_id);
CREATE INDEX idx_event_log_character ON event_log(character_id);
CREATE INDEX idx_event_log_turn ON event_log(turn);
CREATE INDEX idx_scene_history_character ON scene_history(character_id);
CREATE INDEX idx_relationships_characters ON relationships(character_a_id, character_b_id);
CREATE INDEX idx_inventory_character ON inventory(character_id);
CREATE INDEX idx_working_memory_character ON working_memory(character_id);
CREATE INDEX idx_scene_graph_location ON scene_graph(location_id);