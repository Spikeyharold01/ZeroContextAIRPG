PRAGMA foreign_keys = ON;

CREATE TABLE locations (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    region TEXT,
    description TEXT
);

CREATE TABLE characters (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    current_goal TEXT,
    tension FLOAT DEFAULT 0.5,
    plot_state TEXT,
    current_location_id INTEGER,
    FOREIGN KEY (current_location_id) REFERENCES locations(id)
);

CREATE TABLE emotional_state (
    character_id INTEGER PRIMARY KEY,
    trust INTEGER,
    fear INTEGER,
    mood TEXT,
    FOREIGN KEY (character_id) REFERENCES characters(id)
);

CREATE TABLE conversational_facts (
    id TEXT PRIMARY KEY,
    character_id INTEGER,
    fact_text TEXT NOT NULL,
    fact_references TEXT,
    importance FLOAT,
    confidence FLOAT,
    is_active BOOLEAN DEFAULT 1,
    FOREIGN KEY (character_id) REFERENCES characters(id)
);

CREATE TABLE game_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    current_location_id INTEGER,
    current_scene_type TEXT,
    combat_active BOOLEAN DEFAULT 0,
    current_turn INTEGER DEFAULT 0,
    FOREIGN KEY (current_location_id) REFERENCES locations(id)
);

INSERT INTO locations (id, name, region, description)
VALUES (7, 'The Lantern Inn', 'North Road', 'A crowded coaching inn.');

INSERT INTO characters (
    id, name, type, current_goal, tension, plot_state, current_location_id
) VALUES (
    12, 'Mara Voss', 'NPC', 'Protect the sealed letter', 0.75,
    '{"letter_hidden": true}', 7
);

INSERT INTO emotional_state (character_id, trust, fear, mood)
VALUES (12, 63, 27, 'guarded');

INSERT INTO conversational_facts (
    id, character_id, fact_text, fact_references, importance, confidence, is_active
) VALUES (
    'fact_bridge', 12, 'The eastern bridge is watched.',
    '["eastern bridge", "watchers"]', 0.8, 0.95, 1
);

INSERT INTO game_state (
    id, current_location_id, current_scene_type, combat_active, current_turn
) VALUES (1, 7, 'investigation', 0, 42);
