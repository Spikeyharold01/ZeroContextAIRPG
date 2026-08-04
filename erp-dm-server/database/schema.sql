-- ============================================================
-- ADAPTIVE RPG/ERP ENGINE – DATABASE SCHEMA VERSION 7
-- ============================================================

CREATE TABLE schema_version (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL
);

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
    prose_fingerprint TEXT,
    status TEXT DEFAULT 'active',
    is_active BOOLEAN DEFAULT 1,
    
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

-- ============================================================
-- D&D 5e STATS (System-Specific)
-- ============================================================
CREATE TABLE dnd_stats (
    character_id INTEGER PRIMARY KEY,
    
    -- Class & Level
    class TEXT,
    subclass TEXT,
    level INTEGER DEFAULT 1,
    experience_points INTEGER DEFAULT 0,
    
    -- Ability Scores (RAW)
    strength INTEGER DEFAULT 10,
    dexterity INTEGER DEFAULT 10,
    constitution INTEGER DEFAULT 10,
    intelligence INTEGER DEFAULT 10,
    wisdom INTEGER DEFAULT 10,
    charisma INTEGER DEFAULT 10,
    
    -- Combat Stats
    armor_class INTEGER DEFAULT 10,
    hp_current INTEGER DEFAULT 10,
    hp_max INTEGER DEFAULT 10,
    speed INTEGER DEFAULT 30,
    initiative_bonus INTEGER DEFAULT 0,
    proficiency_bonus INTEGER DEFAULT 2,
    
    -- Saving Throws (with proficiency)
    strength_save_bonus INTEGER DEFAULT 0,
    strength_save_proficiency BOOLEAN DEFAULT 0,
    dexterity_save_bonus INTEGER DEFAULT 0,
    dexterity_save_proficiency BOOLEAN DEFAULT 0,
    constitution_save_bonus INTEGER DEFAULT 0,
    constitution_save_proficiency BOOLEAN DEFAULT 0,
    intelligence_save_bonus INTEGER DEFAULT 0,
    intelligence_save_proficiency BOOLEAN DEFAULT 0,
    wisdom_save_bonus INTEGER DEFAULT 0,
    wisdom_save_proficiency BOOLEAN DEFAULT 0,
    charisma_save_bonus INTEGER DEFAULT 0,
    charisma_save_proficiency BOOLEAN DEFAULT 0,
    
    -- Skills (JSON for flexibility)
    skills TEXT,                    -- JSON: {"acrobatics": {"bonus": 6, "proficiency": true}, ...}
    
    -- Senses
    passive_perception INTEGER DEFAULT 10,
    darkvision INTEGER DEFAULT 0,
    
    -- Proficiencies (JSON arrays)
    armor_proficiencies TEXT,       -- JSON: ["light", "medium", "heavy", "shields"]
    weapon_proficiencies TEXT,      -- JSON: ["simple", "martial"]
    tool_proficiencies TEXT,        -- JSON: ["thieves_tools", "smiths_tools"]
    language_proficiencies TEXT,    -- JSON: ["Common", "Elvish", "Dwarvish"]
    
    -- Spellcasting
    spellcasting_ability TEXT,      -- 'strength', 'dexterity', 'constitution', 'intelligence', 'wisdom', 'charisma'
    spell_save_dc INTEGER DEFAULT 10,
    spell_attack_bonus INTEGER DEFAULT 4,
    cantrips_known INTEGER DEFAULT 0,
    spells_known INTEGER DEFAULT 0,
    spell_slots_level_1 INTEGER DEFAULT 0,
    spell_slots_level_2 INTEGER DEFAULT 0,
    spell_slots_level_3 INTEGER DEFAULT 0,
    spell_slots_level_4 INTEGER DEFAULT 0,
    spell_slots_level_5 INTEGER DEFAULT 0,
    spell_slots_level_6 INTEGER DEFAULT 0,
    spell_slots_level_7 INTEGER DEFAULT 0,
    spell_slots_level_8 INTEGER DEFAULT 0,
    spell_slots_level_9 INTEGER DEFAULT 0,
    
    -- Spells (JSON arrays)
    prepared_spells TEXT,           -- JSON: ["spell_name_1", "spell_name_2"]
    known_spells TEXT,              -- JSON: ["spell_name_1", "spell_name_2"]
    
    -- Features & Traits (JSON arrays)
    racial_traits TEXT,             -- JSON: ["Fey Ancestry", "Darkvision"]
    class_features TEXT,            -- JSON: ["Fighting Style", "Action Surge"]
    feats TEXT,                     -- JSON: ["Feat Name 1", "Feat Name 2"]
    
    -- Equipment (JSON array)
    equipment TEXT,                 -- JSON: [{"name": "Longsword +1", "damage_dice": "1d8+4", "attack_bonus": 7}, ...]
    
    -- Maneuvers (JSON array)
    maneuvers TEXT,                 -- JSON: ["Commander's Strike", "Rally", "Trip Attack"]
    
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
-- NOTE: Column name is 'fact_references' (not 'references') to avoid SQL keyword conflict
CREATE TABLE conversational_facts (
    id TEXT PRIMARY KEY,
    character_id INTEGER,
    fact_text TEXT NOT NULL,
    fact_references TEXT,
    embedding BLOB,
    importance FLOAT DEFAULT 0.5,
    confidence FLOAT DEFAULT 0.9,
    source_type TEXT,                  -- 'narrative', 'user', 'system'
    fact_type TEXT DEFAULT 'world_fact',   -- 'world_fact', 'belief_fact', 'rumor_fact'
    source_character_id INTEGER,            -- Who expressed this belief/rumor
    created_turn INTEGER DEFAULT 0,
    last_referenced_turn INTEGER DEFAULT 0,
    expires_at_turn INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT 1,
	game_day INTEGER DEFAULT 1,
    FOREIGN KEY (character_id) REFERENCES characters(id),
    FOREIGN KEY (source_character_id) REFERENCES characters(id)
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
	game_day INTEGER DEFAULT 1, 
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

-- ============================================================
-- 17. GENERIC STATE PERSISTENCE (ADDITIVE; LEGACY REMAINS AUTHORITATIVE)
-- One database file contains exactly one non-deleted campaign.
-- ============================================================
CREATE TABLE campaigns (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL DEFAULT 'active'
        CHECK (lifecycle_status IN ('active', 'deleted')),
    rules_profile_id TEXT,
    active_scene_id TEXT,
    current_turn INTEGER NOT NULL DEFAULT 0 CHECK (current_turn >= 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TEXT,
    CHECK ((lifecycle_status = 'deleted' AND deleted_at IS NOT NULL)
        OR (lifecycle_status = 'active' AND deleted_at IS NULL))
);

CREATE UNIQUE INDEX idx_campaigns_one_live
    ON campaigns((1)) WHERE lifecycle_status != 'deleted';

CREATE TABLE state_documents (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    namespace TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    state_json TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    lifecycle_status TEXT NOT NULL DEFAULT 'active'
        CHECK (lifecycle_status IN ('active', 'deleted')),
    content_hash TEXT NOT NULL,
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TEXT,
    FOREIGN KEY (campaign_id) REFERENCES campaigns(id),
    UNIQUE (campaign_id, namespace, subject_type, subject_id),
    CHECK ((lifecycle_status = 'deleted' AND deleted_at IS NOT NULL)
        OR (lifecycle_status = 'active' AND deleted_at IS NULL))
);

CREATE INDEX idx_state_documents_subject
    ON state_documents(campaign_id, subject_type, subject_id);
CREATE INDEX idx_state_documents_namespace
    ON state_documents(campaign_id, namespace, lifecycle_status, id);

CREATE TABLE state_patch_log (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    state_document_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_id TEXT,
    producer_type TEXT NOT NULL,
    producer_id TEXT,
    turn_number INTEGER,
    base_revision INTEGER,
    prior_revision INTEGER NOT NULL,
    resulting_revision INTEGER NOT NULL,
    patch_json TEXT NOT NULL,
    patch_hash TEXT NOT NULL,
    prior_content_hash TEXT NOT NULL,
    result_content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (campaign_id) REFERENCES campaigns(id),
    FOREIGN KEY (state_document_id) REFERENCES state_documents(id),
    UNIQUE (campaign_id, idempotency_key),
    UNIQUE (state_document_id, resulting_revision),
    CHECK (resulting_revision = prior_revision + 1)
);

CREATE INDEX idx_state_patch_document_revision
    ON state_patch_log(state_document_id, resulting_revision);

CREATE TABLE state_idempotency (
    campaign_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    target_fingerprint TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    state_document_id TEXT NOT NULL,
    resulting_revision INTEGER NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (campaign_id, idempotency_key),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(id),
    FOREIGN KEY (state_document_id) REFERENCES state_documents(id)
);

CREATE TABLE state_projection_definitions (
    id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    path_json TEXT NOT NULL,
    value_type TEXT NOT NULL
        CHECK (value_type IN ('null', 'text', 'integer', 'real', 'boolean')),
    definition_version INTEGER NOT NULL DEFAULT 1 CHECK (definition_version >= 1),
    lifecycle_status TEXT NOT NULL DEFAULT 'active'
        CHECK (lifecycle_status IN ('active', 'deleted')),
    UNIQUE (namespace, subject_type, path_json, definition_version)
);

CREATE TABLE state_projection_values (
    campaign_id TEXT NOT NULL,
    state_document_id TEXT NOT NULL,
    projection_id TEXT NOT NULL,
    source_revision INTEGER NOT NULL,
    value_type TEXT NOT NULL,
    text_value TEXT,
    integer_value INTEGER,
    real_value REAL,
    boolean_value INTEGER,
    value_hash TEXT NOT NULL,
    PRIMARY KEY (state_document_id, projection_id),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(id),
    FOREIGN KEY (state_document_id) REFERENCES state_documents(id),
    FOREIGN KEY (projection_id) REFERENCES state_projection_definitions(id)
);

CREATE INDEX idx_state_projection_lookup
    ON state_projection_values(campaign_id, projection_id, value_type);
