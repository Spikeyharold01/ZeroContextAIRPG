# database/db_manager.py

import sqlite3
import json
import os
from typing import Optional, Dict, List, Any, Union

class DatabaseManager:
    """Complete database manager for the Adaptive RPG/ERP Engine v5.2"""

    def __init__(self, db_path: str = "data/game.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize the database with schema.sql if it doesn't exist."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        # Check if tables exist
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='characters'")
        exists = cursor.fetchone()
        conn.close()
        
        if not exists:
            # Load schema from file
            schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
            if os.path.exists(schema_path):
                with open(schema_path, "r") as f:
                    schema = f.read()
                conn = self._get_connection()
                conn.executescript(schema)
                conn.commit()
                conn.close()
                print("Database initialized with schema.sql")
            else:
                print("WARNING: schema.sql not found. Please ensure the database is initialized.")

    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection with dictionary row factory."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _safe_json(self, data: Any) -> str:
        """Safely convert data to JSON string."""
        if data is None:
            return "[]"
        if isinstance(data, str):
            return data
        try:
            return json.dumps(data)
        except:
            return "[]"

    # ========================================================================
    # CHARACTERS
    # ========================================================================

    def create_character(self, name: str, character_type: str = "NPC", full_card_text: str = "") -> int:
        """Create a new character. Returns character_id."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO characters (name, type, full_card_text)
            VALUES (?, ?, ?)
        """, (name, character_type, full_card_text))
        character_id = cursor.lastrowid
        conn.commit()
        conn.close()

        # Create default emotional state
        self.update_emotional_state(character_id, {
            "trust": 50,
            "fear": 10,
            "arousal": 20,
            "tension": 30,
            "intimacy": 40,
            "mood": "neutral",
            "emotional_shift": None
        })

        # Create default mechanical stats
        self.update_mechanical_stats(character_id, {
            "strength": 10,
            "dexterity": 10,
            "constitution": 10,
            "intelligence": 10,
            "wisdom": 10,
            "charisma": 10,
            "hp_current": 10,
            "hp_max": 10,
            "armor_class": 10,
            "proficiency_bonus": 2,
            "level": 1,
            "conditions": "[]"
        })

        return character_id

    def get_character(self, character_id: int) -> Optional[Dict]:
        """Get full character record by ID."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM characters WHERE id = ?", (character_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_character_by_name(self, name: str) -> Optional[Dict]:
        """Get character by name."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM characters WHERE name = ?", (name,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_all_character_names(self) -> List[str]:
        """Get all character names (for NER cache)."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM characters")
        rows = cursor.fetchall()
        conn.close()
        return [row["name"] for row in rows]

    def update_character(self, character_id: int, updates: Dict):
        """Generic update for character fields."""
        conn = self._get_connection()
        cursor = conn.cursor()
        set_clause = ", ".join([f"{key} = ?" for key in updates.keys()])
        values = list(updates.values()) + [character_id]
        cursor.execute(f"UPDATE characters SET {set_clause} WHERE id = ?", values)
        conn.commit()
        conn.close()

    def get_character_core(self, character_id: int) -> Optional[str]:
        """Get only the character_core field."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT character_core FROM characters WHERE id = ?", (character_id,))
        row = cursor.fetchone()
        conn.close()
        return row["character_core"] if row else None

    def update_character_compressed(self, character_id: int, core: str, speech: str,
                                    mannerisms: str, physical: str, goals: str,
                                    scenario_plot: str, plot_state: str = "{}"):
        """Update all compressed character fields at once."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE characters 
            SET character_core = ?,
                speech_patterns = ?,
                mannerisms = ?,
                physical_description = ?,
                goals = ?,
                scenario_plot = ?,
                plot_state = ?
            WHERE id = ?
        """, (core, speech, mannerisms, physical, goals, scenario_plot, plot_state, character_id))
        conn.commit()
        conn.close()

    def update_narrative_goals(self, character_id: int, current_goal: str = None,
                               hidden_goal: str = None, immediate_beat: str = None,
                               long_arc: str = None, tension: float = None):
        """Update narrative goals for a character."""
        conn = self._get_connection()
        cursor = conn.cursor()
        updates = []
        values = []
        if current_goal is not None:
            updates.append("current_goal = ?")
            values.append(current_goal)
        if hidden_goal is not None:
            updates.append("hidden_goal = ?")
            values.append(hidden_goal)
        if immediate_beat is not None:
            updates.append("immediate_beat = ?")
            values.append(immediate_beat)
        if long_arc is not None:
            updates.append("long_arc = ?")
            values.append(long_arc)
        if tension is not None:
            updates.append("tension = ?")
            values.append(tension)
        if updates:
            values.append(character_id)
            cursor.execute(f"UPDATE characters SET {', '.join(updates)} WHERE id = ?", values)
            conn.commit()
        conn.close()

    def get_narrative_goals(self, character_id: int) -> Dict:
        """Get all narrative goals for a character."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT current_goal, hidden_goal, immediate_beat, long_arc, tension
            FROM characters WHERE id = ?
        """, (character_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return dict(row)
        return {
            "current_goal": None,
            "hidden_goal": None,
            "immediate_beat": None,
            "long_arc": None,
            "tension": 0.5
        }

    # ========================================================================
    # EMOTIONAL STATE
    # ========================================================================

    def get_emotional_state(self, character_id: int) -> Optional[Dict]:
        """Get emotional state for a character."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM emotional_state WHERE character_id = ?", (character_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def update_emotional_state(self, character_id: int, updates: Dict):
        """Update emotional state. Inserts if not exists."""
        conn = self._get_connection()
        cursor = conn.cursor()
        # Check if exists
        cursor.execute("SELECT 1 FROM emotional_state WHERE character_id = ?", (character_id,))
        exists = cursor.fetchone()
        
        if exists:
            set_clause = ", ".join([f"{key} = ?" for key in updates.keys()])
            values = list(updates.values()) + [character_id]
            cursor.execute(f"UPDATE emotional_state SET {set_clause} WHERE character_id = ?", values)
        else:
            columns = ", ".join(["character_id"] + list(updates.keys()))
            placeholders = ", ".join(["?"] + ["?"] * len(updates))
            values = [character_id] + list(updates.values())
            cursor.execute(f"INSERT INTO emotional_state ({columns}) VALUES ({placeholders})", values)
        conn.commit()
        conn.close()

    # ========================================================================
    # MECHANICAL STATS
    # ========================================================================

    def get_mechanical_stats(self, character_id: int) -> Optional[Dict]:
        """Get mechanical stats for a character."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM mechanical_stats WHERE character_id = ?", (character_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            data = dict(row)
            if 'conditions' in data and data['conditions']:
                try:
                    data['conditions'] = json.loads(data['conditions'])
                except:
                    data['conditions'] = []
            return data
        return None

    def update_mechanical_stats(self, character_id: int, updates: Dict):
        """Update mechanical stats. Inserts if not exists."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM mechanical_stats WHERE character_id = ?", (character_id,))
        exists = cursor.fetchone()
        
        # Handle JSON serialization
        if 'conditions' in updates and isinstance(updates['conditions'], (list, dict)):
            updates['conditions'] = json.dumps(updates['conditions'])
        
        if exists:
            set_clause = ", ".join([f"{key} = ?" for key in updates.keys()])
            values = list(updates.values()) + [character_id]
            cursor.execute(f"UPDATE mechanical_stats SET {set_clause} WHERE character_id = ?", values)
        else:
            columns = ", ".join(["character_id"] + list(updates.keys()))
            placeholders = ", ".join(["?"] + ["?"] * len(updates))
            values = [character_id] + list(updates.values())
            cursor.execute(f"INSERT INTO mechanical_stats ({columns}) VALUES ({placeholders})", values)
        conn.commit()
        conn.close()

    # ========================================================================
    # LOCATIONS & AMBIANCE
    # ========================================================================

    def get_location(self, location_id: int) -> Optional[Dict]:
        """Get location by ID."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM locations WHERE id = ?", (location_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_ambiance(self, location_id: int) -> Optional[Dict]:
        """Get ambiance for a location."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM ambiance_state WHERE location_id = ?", (location_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def update_ambiance(self, location_id: int, updates: Dict):
        """Update ambiance for a location."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM ambiance_state WHERE location_id = ?", (location_id,))
        exists = cursor.fetchone()
        if exists:
            set_clause = ", ".join([f"{key} = ?" for key in updates.keys()])
            values = list(updates.values()) + [location_id]
            cursor.execute(f"UPDATE ambiance_state SET {set_clause} WHERE location_id = ?", values)
        else:
            columns = ", ".join(["location_id"] + list(updates.keys()))
            placeholders = ", ".join(["?"] + ["?"] * len(updates))
            values = [location_id] + list(updates.values())
            cursor.execute(f"INSERT INTO ambiance_state ({columns}) VALUES ({placeholders})", values)
        conn.commit()
        conn.close()

    def get_all_location_names(self) -> List[str]:
        """Get all location names (for NER cache)."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM locations")
        rows = cursor.fetchall()
        conn.close()
        return [row["name"] for row in rows]

    def get_present_npcs(self, location_id: int) -> List[Dict]:
        """Get all NPCs at a location."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM characters 
            WHERE current_location_id = ? AND type = 'NPC'
        """, (location_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def update_character_location(self, character_id: int, location_id: int):
        """Move a character to a new location."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE characters SET current_location_id = ? WHERE id = ?", (location_id, character_id))
        conn.commit()
        conn.close()

    # ========================================================================
    # RELATIONSHIPS
    # ========================================================================

    def get_relationships(self, character_id: int) -> List[Dict]:
        """Get all relationships for a character."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM relationships 
            WHERE character_a_id = ? OR character_b_id = ?
        """, (character_id, character_id))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def update_relationship(self, char_a: int, char_b: int, relationship_type: str, trust_score: int = 50):
        """Create or update a relationship."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id FROM relationships 
            WHERE (character_a_id = ? AND character_b_id = ?) 
               OR (character_a_id = ? AND character_b_id = ?)
        """, (char_a, char_b, char_b, char_a))
        row = cursor.fetchone()
        if row:
            cursor.execute("""
                UPDATE relationships 
                SET relationship_type = ?, trust_score = ? 
                WHERE id = ?
            """, (relationship_type, trust_score, row['id']))
        else:
            cursor.execute("""
                INSERT INTO relationships (character_a_id, character_b_id, relationship_type, trust_score)
                VALUES (?, ?, ?, ?)
            """, (char_a, char_b, relationship_type, trust_score))
        conn.commit()
        conn.close()

    # ========================================================================
    # CONVERSATIONAL FACTS (Proxy-Managed)
    # ========================================================================

    def get_active_facts(self, character_id: int = None) -> List[Dict]:
        """Get all active facts for a character (or all characters if none)."""
        conn = self._get_connection()
        cursor = conn.cursor()
        if character_id:
            cursor.execute("""
                SELECT * FROM conversational_facts 
                WHERE is_active = 1 AND character_id = ?
                ORDER BY created_turn DESC
            """, (character_id,))
        else:
            cursor.execute("""
                SELECT * FROM conversational_facts 
                WHERE is_active = 1
                ORDER BY created_turn DESC
            """)
        rows = cursor.fetchall()
        conn.close()
        result = []
        for row in rows:
            data = dict(row)
            if 'references' in data and data['references']:
                try:
                    data['references'] = json.loads(data['references'])
                except:
                    data['references'] = []
            # Convert embedding from BLOB if present
            if 'embedding' in data and data['embedding']:
                import array
                try:
                    data['embedding'] = array.array('f', data['embedding']).tolist()
                except:
                    pass
            result.append(data)
        return result

    def insert_conversational_fact(
        self,
        fact_id: str,
        character_id: int,
        fact_text: str,
        references: Union[str, List[str]],
        embedding: bytes = None,
        importance: float = 0.5,
        confidence: float = 0.9,
        source_type: str = "narrative",
        created_turn: int = 0,
        last_referenced_turn: int = 0,
        expires_at_turn: int = None
    ):
        """Insert a new conversational fact."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        if isinstance(references, list):
            references = json.dumps(references)
        
        cursor.execute("""
            INSERT OR REPLACE INTO conversational_facts 
            (id, character_id, fact_text, references, embedding, importance, confidence,
             source_type, created_turn, last_referenced_turn, expires_at_turn, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """, (
            fact_id, character_id, fact_text, references, embedding,
            importance, confidence, source_type, created_turn, last_referenced_turn, expires_at_turn
        ))
        conn.commit()
        conn.close()

    def update_conversational_fact(
        self,
        fact_id: str,
        new_text: str = None,
        new_references: Union[str, List[str]] = None,
        importance: float = None,
        confidence: float = None,
        last_referenced_turn: int = None,
        expires_at_turn: int = None
    ):
        """Update an existing conversational fact."""
        conn = self._get_connection()
        cursor = conn.cursor()
        updates = []
        values = []
        
        if new_text is not None:
            updates.append("fact_text = ?")
            values.append(new_text)
        if new_references is not None:
            if isinstance(new_references, list):
                new_references = json.dumps(new_references)
            updates.append("references = ?")
            values.append(new_references)
        if importance is not None:
            updates.append("importance = ?")
            values.append(importance)
        if confidence is not None:
            updates.append("confidence = ?")
            values.append(confidence)
        if last_referenced_turn is not None:
            updates.append("last_referenced_turn = ?")
            values.append(last_referenced_turn)
        if expires_at_turn is not None:
            updates.append("expires_at_turn = ?")
            values.append(expires_at_turn)
        
        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            values.append(fact_id)
            cursor.execute(f"UPDATE conversational_facts SET {', '.join(updates)} WHERE id = ?", values)
            conn.commit()
        conn.close()

    def delete_conversational_fact(self, fact_id: str):
        """Soft delete a conversational fact."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE conversational_facts SET is_active = 0 WHERE id = ?", (fact_id,))
        conn.commit()
        conn.close()

    def expire_facts_by_turn(self, current_turn: int):
        """Expire all facts with expires_at_turn <= current_turn."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE conversational_facts 
            SET is_active = 0, updated_at = CURRENT_TIMESTAMP
            WHERE expires_at_turn IS NOT NULL AND expires_at_turn <= ?
        """, (current_turn,))
        conn.commit()
        conn.close()

    # ========================================================================
    # EVENT LOG
    # ========================================================================

    def log_event(self, event_text: str, event_type: str = "narrative", turn: int = 0,
                  importance: float = 0.5, character_id: int = None):
        """Log a major event."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO event_log (event_text, event_type, turn, importance, character_id)
            VALUES (?, ?, ?, ?, ?)
        """, (event_text, event_type, turn, importance, character_id))
        conn.commit()
        conn.close()

    def get_event_log(self, character_id: int = None, limit: int = 100) -> List[Dict]:
        """Get event log (chronological, most recent first)."""
        conn = self._get_connection()
        cursor = conn.cursor()
        if character_id:
            cursor.execute("""
                SELECT * FROM event_log 
                WHERE character_id = ? OR character_id IS NULL
                ORDER BY turn DESC LIMIT ?
            """, (character_id, limit))
        else:
            cursor.execute("SELECT * FROM event_log ORDER BY turn DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    # ========================================================================
    # WORLD STATE
    # ========================================================================

    def get_world_state(self) -> Dict:
        """Get the current world state (single row)."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM world_state WHERE id = 1")
        row = cursor.fetchone()
        conn.close()
        if row:
            data = dict(row)
            if 'additional_state' in data and data['additional_state']:
                try:
                    data['additional_state'] = json.loads(data['additional_state'])
                except:
                    data['additional_state'] = {}
            return data
        # Default if not exists
        return {
            "id": 1,
            "war_active": 0,
            "bridge_destroyed": 0,
            "festival_active": 0,
            "moon_phase": "full",
            "weather": "clear",
            "additional_state": {}
        }

    def update_world_state(self, updates: Dict):
        """Update world state."""
        conn = self._get_connection()
        cursor = conn.cursor()
        # Ensure row exists
        cursor.execute("SELECT 1 FROM world_state WHERE id = 1")
        if not cursor.fetchone():
            cursor.execute("INSERT INTO world_state (id) VALUES (1)")
        
        set_clause = ", ".join([f"{key} = ?" for key in updates.keys()])
        values = list(updates.values())
        cursor.execute(f"UPDATE world_state SET {set_clause} WHERE id = 1", values)
        conn.commit()
        conn.close()

    # ========================================================================
    # SCENE GRAPH
    # ========================================================================

    def get_scene_graph(self, location_id: int) -> List[Dict]:
        """Get all objects in a scene."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM scene_graph WHERE location_id = ?", (location_id,))
        rows = cursor.fetchall()
        conn.close()
        result = []
        for row in rows:
            data = dict(row)
            if 'npc_present' in data and data['npc_present']:
                try:
                    data['npc_present'] = json.loads(data['npc_present'])
                except:
                    data['npc_present'] = []
            result.append(data)
        return result

    def update_scene_graph(self, location_id: int, object_name: str, object_state: str,
                           npc_present: List[int] = None, visibility: str = None):
        """Add or update a scene graph entry."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Check if entry exists
        cursor.execute("""
            SELECT id FROM scene_graph WHERE location_id = ? AND object_name = ?
        """, (location_id, object_name))
        row = cursor.fetchone()
        
        npc_json = json.dumps(npc_present) if npc_present is not None else None
        
        if row:
            updates = ["object_state = ?", "object_name = object_name"]
            values = [object_state]
            if npc_json is not None:
                updates.append("npc_present = ?")
                values.append(npc_json)
            if visibility is not None:
                updates.append("visibility = ?")
                values.append(visibility)
            values.append(location_id)
            values.append(object_name)
            cursor.execute(f"""
                UPDATE scene_graph SET {', '.join(updates)} 
                WHERE location_id = ? AND object_name = ?
            """, values)
        else:
            cursor.execute("""
                INSERT INTO scene_graph (location_id, object_name, object_state, npc_present, visibility)
                VALUES (?, ?, ?, ?, ?)
            """, (location_id, object_name, object_state, npc_json, visibility))
        
        conn.commit()
        conn.close()

    # ========================================================================
    # GAME STATE (Turn Counter, etc.)
    # ========================================================================

    def get_game_state(self) -> Dict:
        """Get global game state."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM game_state WHERE id = 1")
        row = cursor.fetchone()
        conn.close()
        if row:
            return dict(row)
        # Default
        return {
            "id": 1,
            "current_location_id": None,
            "current_scene_type": "narrative",
            "combat_active": 0,
            "current_turn": 0
        }

    def update_game_state(self, updates: Dict):
        """Update game state."""
        conn = self._get_connection()
        cursor = conn.cursor()
        # Ensure row exists
        cursor.execute("SELECT 1 FROM game_state WHERE id = 1")
        if not cursor.fetchone():
            cursor.execute("INSERT INTO game_state (id) VALUES (1)")
        
        set_clause = ", ".join([f"{key} = ?" for key in updates.keys()])
        values = list(updates.values())
        cursor.execute(f"UPDATE game_state SET {set_clause} WHERE id = 1", values)
        conn.commit()
        conn.close()

    def increment_turn(self) -> int:
        """Increment the global turn counter and return the new value."""
        state = self.get_game_state()
        new_turn = state.get('current_turn', 0) + 1
        self.update_game_state({'current_turn': new_turn})
        # Expire facts based on new turn
        self.expire_facts_by_turn(new_turn)
        return new_turn

    # ========================================================================
    # WORKING MEMORY
    # ========================================================================

    def store_recent_prose(self, character_id: int, prose_snippet: str):
        """Store the last ~300 tokens of prose for a character."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO working_memory (character_id, prose_snippet, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        """, (character_id, prose_snippet))
        conn.commit()
        conn.close()

    def get_recent_prose(self, character_id: int) -> Optional[str]:
        """Get the stored prose snippet."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT prose_snippet FROM working_memory WHERE character_id = ?", (character_id,))
        row = cursor.fetchone()
        conn.close()
        return row["prose_snippet"] if row else None

    # ========================================================================
    # KNOWLEDGE CHUNKS (RAG)
    # ========================================================================

    def store_knowledge_chunk(self, chunk_text: str, embedding: bytes, source_type: str,
                              associated_character_id: int = None):
        """Store a knowledge chunk for RAG retrieval."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO knowledge_chunks (chunk_text, embedding, source_type, associated_character_id)
            VALUES (?, ?, ?, ?)
        """, (chunk_text, embedding, source_type, associated_character_id))
        conn.commit()
        conn.close()

    def get_all_knowledge_chunks(self, character_id: int = None) -> List[Dict]:
        """Get all knowledge chunks for a character or all."""
        conn = self._get_connection()
        cursor = conn.cursor()
        if character_id:
            cursor.execute("""
                SELECT * FROM knowledge_chunks 
                WHERE associated_character_id = ? OR associated_character_id IS NULL
                ORDER BY timestamp DESC
            """, (character_id,))
        else:
            cursor.execute("SELECT * FROM knowledge_chunks ORDER BY timestamp DESC")
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    # ========================================================================
    # SCENE HISTORY
    # ========================================================================

    def insert_scene_history(self, character_id: int, summary: str, turn: int = 0):
        """Log an emotional shift to scene history."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO scene_history (character_id, emotional_shift_summary, turn)
            VALUES (?, ?, ?)
        """, (character_id, summary, turn))
        conn.commit()
        conn.close()

    def get_scene_history(self, character_id: int, limit: int = 10) -> List[Dict]:
        """Get recent scene history."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM scene_history 
            WHERE character_id = ?
            ORDER BY turn DESC LIMIT ?
        """, (character_id, limit))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    # ========================================================================
    # COMBAT STATE
    # ========================================================================

    def get_active_combat(self) -> Optional[Dict]:
        """Get the current active combat encounter."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM combat_state WHERE is_active = 1 LIMIT 1")
        row = cursor.fetchone()
        conn.close()
        if row:
            data = dict(row)
            if 'turn_order' in data and data['turn_order']:
                try:
                    data['turn_order'] = json.loads(data['turn_order'])
                except:
                    data['turn_order'] = []
            return data
        return None

    def start_combat(self, turn_order: List[int]) -> int:
        """Start a new combat encounter."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO combat_state (is_active, turn_order, current_turn, round_number)
            VALUES (1, ?, 0, 1)
        """, (json.dumps(turn_order),))
        encounter_id = cursor.lastrowid
        conn.commit()
        conn.close()
        # Update game state
        self.update_game_state({"combat_active": 1, "current_scene_type": "combat"})
        return encounter_id

    def end_combat(self):
        """End the current combat."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE combat_state SET is_active = 0 WHERE is_active = 1")
        conn.commit()
        conn.close()
        self.update_game_state({"combat_active": 0, "current_scene_type": "narrative"})

    def update_combat_state(self, updates: Dict):
        """Update current combat state."""
        conn = self._get_connection()
        cursor = conn.cursor()
        if 'turn_order' in updates and isinstance(updates['turn_order'], list):
            updates['turn_order'] = json.dumps(updates['turn_order'])
        set_clause = ", ".join([f"{key} = ?" for key in updates.keys()])
        values = list(updates.values())
        cursor.execute(f"UPDATE combat_state SET {set_clause} WHERE is_active = 1", values)
        conn.commit()
        conn.close()