-- database/schema.sql

-- 1. Core Characters
CREATE TABLE characters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT CHECK(type IN ('PC', 'NPC', 'Monster')) DEFAULT 'NPC',
    full_card_text TEXT,
    current_location_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. Locations
CREATE TABLE locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    region TEXT,
    description TEXT
);

-- 3. Ambiance (Sensory details)
CREATE TABLE ambiance_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id INTEGER,
    lighting TEXT,
    weather TEXT,
    soundscape TEXT,
    vibe TEXT,
    smell TEXT,
    FOREIGN KEY (location_id) REFERENCES locations(id)
);

-- 4. Emotional Axes (ERP / Social)
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

-- 5. Mechanical Stats (D&D / Rules)
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
    conditions TEXT DEFAULT '[]', -- JSON array
    FOREIGN KEY (character_id) REFERENCES characters(id)
);

-- 6. Inventory
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

-- 7. Relationships
CREATE TABLE relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_a_id INTEGER,
    character_b_id INTEGER,
    relationship_type TEXT,
    trust_score INTEGER DEFAULT 50,
    FOREIGN KEY (character_a_id) REFERENCES characters(id),
    FOREIGN KEY (character_b_id) REFERENCES characters(id)
);

-- 8. Combat State
CREATE TABLE combat_state (
    encounter_id INTEGER PRIMARY KEY AUTOINCREMENT,
    is_active BOOLEAN DEFAULT 0,
    turn_order JSON,
    current_turn INTEGER DEFAULT 0,
    round_number INTEGER DEFAULT 1
);