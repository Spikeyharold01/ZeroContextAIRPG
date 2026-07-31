# database/db_manager.py

import sqlite3
import json
import os
import array
from typing import Optional, Dict, List, Any, Union
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class EmbeddingUtils:
    """Standardised conversion between list of floats and BLOB."""
    
    @staticmethod
    def to_bytes(vector: Union[List[float], None]) -> bytes:
        """Convert a list of floats to a compact BLOB."""
        if not vector:
            return b''
        try:
            return array.array('f', vector).tobytes()
        except Exception as e:
            logger.error(f"Embedding to_bytes failed: {e}")
            return b''
    
    @staticmethod
    def from_bytes(data: Union[bytes, None]) -> List[float]:
        """Convert a BLOB back to a list of floats."""
        if not data:
            return []
        try:
            return list(array.array('f', data))
        except Exception as e:
            logger.error(f"Embedding from_bytes failed: {e}")
            return []


class DatabaseManager:
    """Complete database manager for the Adaptive RPG/ERP Engine v5.2."""

    # ---------- WHITELISTS for dynamic update methods ----------
    _WHITELISTS = {
        "emotional_state": {
            "trust", "fear", "arousal", "tension", "intimacy", "mood", "emotional_shift"
        },
        "mechanical_stats": {
            "strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma",
            "hp_current", "hp_max", "armor_class", "proficiency_bonus", "level", "conditions"
        },
        "characters": {
            "name", "type", "character_core", "speech_patterns", "mannerisms",
            "physical_description", "goals", "scenario_plot", "plot_state",
            "current_goal", "hidden_goal", "immediate_beat", "long_arc", "tension",
            "current_location_id"
        },
        "world_state": {
            "war_active", "bridge_destroyed", "festival_active", "moon_phase",
            "weather", "additional_state"
        },
        "game_state": {
            "current_location_id", "current_scene_type", "combat_active", "current_turn"
        },
        "combat_state": {
            "is_active", "turn_order", "current_turn", "round_number"
        },
        "ambiance_state": {
            "lighting", "weather", "soundscape", "vibe", "smell"
        },
        "dnd_stats": {
        "class", "subclass", "level", "experience_points",
        "strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma",
        "armor_class", "hp_current", "hp_max", "speed", "initiative_bonus", "proficiency_bonus",
        "strength_save_bonus", "strength_save_proficiency",
        "dexterity_save_bonus", "dexterity_save_proficiency",
        "constitution_save_bonus", "constitution_save_proficiency",
        "intelligence_save_bonus", "intelligence_save_proficiency",
        "wisdom_save_bonus", "wisdom_save_proficiency",
        "charisma_save_bonus", "charisma_save_proficiency",
        "skills", "passive_perception", "darkvision",
        "armor_proficiencies", "weapon_proficiencies", "tool_proficiencies", "language_proficiencies",
        "spellcasting_ability", "spell_save_dc", "spell_attack_bonus",
        "cantrips_known", "spells_known",
        "spell_slots_level_1", "spell_slots_level_2", "spell_slots_level_3",
        "spell_slots_level_4", "spell_slots_level_5", "spell_slots_level_6",
        "spell_slots_level_7", "spell_slots_level_8", "spell_slots_level_9",
        "prepared_spells", "known_spells",
        "racial_traits", "class_features", "feats", "equipment", "maneuvers"
        }
    }

    def __init__(self, db_path: str = "data/game.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Create database directory and initialise schema."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='characters'")
        exists = cursor.fetchone()
        conn.close()
        
        if not exists:
            schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
            if os.path.exists(schema_path):
                with open(schema_path, "r") as f:
                    schema = f.read()
                conn = self._get_connection()
                conn.executescript(schema)
                conn.commit()
                conn.close()
                logger.info("Database initialised with schema.sql")
            else:
                logger.warning("schema.sql not found. Please initialise manually.")

    def _get_connection(self) -> sqlite3.Connection:
        """Return a connection with row_factory set to sqlite3.Row."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _validate_updates(self, table: str, updates: dict) -> dict:
        """
        Filter updates through a whitelist to prevent arbitrary column writes.
        Returns only allowed keys.
        """
        if table not in self._WHITELISTS:
            raise ValueError(f"Unknown table '{table}' for update")
        allowed = self._WHITELISTS[table]
        valid = {}
        invalid = []
        for key, value in updates.items():
            if key in allowed:
                valid[key] = value
            else:
                invalid.append(key)
        if invalid:
            logger.warning(f"Rejected invalid keys for {table}: {invalid}")
        return valid

    # ========================================================================
    # CHARACTERS
    # ========================================================================

    def create_character(self, name: str, character_type: str = "NPC", full_card_text: str = "") -> int:
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
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM characters WHERE id = ?", (character_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_character_by_name(self, name: str) -> Optional[Dict]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM characters WHERE name = ?", (name,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_all_character_names(self) -> List[str]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM characters")
        rows = cursor.fetchall()
        conn.close()
        return [row["name"] for row in rows]

    def update_character(self, character_id: int, updates: Dict):
        """Generic update for character fields (whitelisted)."""
        valid = self._validate_updates("characters", updates)
        if not valid:
            logger.warning(f"No valid fields to update for character {character_id}")
            return
        conn = self._get_connection()
        cursor = conn.cursor()
        set_clause = ", ".join([f"{key} = ?" for key in valid.keys()])
        values = list(valid.values()) + [character_id]
        cursor.execute(f"UPDATE characters SET {set_clause} WHERE id = ?", values)
        conn.commit()
        conn.close()

    def get_character_core(self, character_id: int) -> Optional[str]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT character_core FROM characters WHERE id = ?", (character_id,))
        row = cursor.fetchone()
        conn.close()
        return row["character_core"] if row else None

    def update_character_compressed(self, character_id: int, core: str, speech: str,
                                    mannerisms: str, physical: str, goals: str,
                                    scenario_plot: str):
        """Update all compressed character fields (excluding plot_state)."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE characters 
            SET character_core = ?,
                speech_patterns = ?,
                mannerisms = ?,
                physical_description = ?,
                goals = ?,
                scenario_plot = ?
            WHERE id = ?
        """, (core, speech, mannerisms, physical, goals, scenario_plot, character_id))
        conn.commit()
        conn.close()

    def update_plot_state(self, character_id: int, plot_state_json: str):
        """Store generic JSON plot state."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE characters SET plot_state = ? WHERE id = ?", (plot_state_json, character_id))
        conn.commit()
        conn.close()

    def update_narrative_goals(self, character_id: int, current_goal: str = None,
                               hidden_goal: str = None, immediate_beat: str = None,
                               long_arc: str = None, tension: float = None):
        conn = self._get_connection()
        cursor = conn.cursor()
        updates = {}
        if current_goal is not None:
            updates["current_goal"] = current_goal
        if hidden_goal is not None:
            updates["hidden_goal"] = hidden_goal
        if immediate_beat is not None:
            updates["immediate_beat"] = immediate_beat
        if long_arc is not None:
            updates["long_arc"] = long_arc
        if tension is not None:
            updates["tension"] = tension
        if updates:
            valid = self._validate_updates("characters", updates)
            if valid:
                set_clause = ", ".join([f"{key} = ?" for key in valid.keys()])
                values = list(valid.values()) + [character_id]
                cursor.execute(f"UPDATE characters SET {set_clause} WHERE id = ?", values)
                conn.commit()
        conn.close()

    def get_narrative_goals(self, character_id: int) -> Dict:
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
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM emotional_state WHERE character_id = ?", (character_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def update_emotional_state(self, character_id: int, updates: Dict):
        valid = self._validate_updates("emotional_state", updates)
        if not valid:
            return
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM emotional_state WHERE character_id = ?", (character_id,))
        exists = cursor.fetchone()
        if exists:
            set_clause = ", ".join([f"{key} = ?" for key in valid.keys()])
            values = list(valid.values()) + [character_id]
            cursor.execute(f"UPDATE emotional_state SET {set_clause} WHERE character_id = ?", values)
        else:
            columns = ", ".join(["character_id"] + list(valid.keys()))
            placeholders = ", ".join(["?"] + ["?"] * len(valid))
            values = [character_id] + list(valid.values())
            cursor.execute(f"INSERT INTO emotional_state ({columns}) VALUES ({placeholders})", values)
        conn.commit()
        conn.close()

    # ========================================================================
    # MECHANICAL STATS
    # ========================================================================

    def get_mechanical_stats(self, character_id: int) -> Optional[Dict]:
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
        # Handle JSON serialization for conditions
        if 'conditions' in updates and isinstance(updates['conditions'], (list, dict)):
            updates['conditions'] = json.dumps(updates['conditions'])
        valid = self._validate_updates("mechanical_stats", updates)
        if not valid:
            return
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM mechanical_stats WHERE character_id = ?", (character_id,))
        exists = cursor.fetchone()
        if exists:
            set_clause = ", ".join([f"{key} = ?" for key in valid.keys()])
            values = list(valid.values()) + [character_id]
            cursor.execute(f"UPDATE mechanical_stats SET {set_clause} WHERE character_id = ?", values)
        else:
            columns = ", ".join(["character_id"] + list(valid.keys()))
            placeholders = ", ".join(["?"] + ["?"] * len(valid))
            values = [character_id] + list(valid.values())
            cursor.execute(f"INSERT INTO mechanical_stats ({columns}) VALUES ({placeholders})", values)
        conn.commit()
        conn.close()

    # ========================================================================
    # LOCATIONS & AMBIANCE
    # ========================================================================

    def get_location(self, location_id: int) -> Optional[Dict]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM locations WHERE id = ?", (location_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_ambiance(self, location_id: int) -> Optional[Dict]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM ambiance_state WHERE location_id = ?", (location_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def update_ambiance(self, location_id: int, updates: Dict):
        valid = self._validate_updates("ambiance_state", updates)
        if not valid:
            return
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM ambiance_state WHERE location_id = ?", (location_id,))
        exists = cursor.fetchone()
        if exists:
            set_clause = ", ".join([f"{key} = ?" for key in valid.keys()])
            values = list(valid.values()) + [location_id]
            cursor.execute(f"UPDATE ambiance_state SET {set_clause} WHERE location_id = ?", values)
        else:
            columns = ", ".join(["location_id"] + list(valid.keys()))
            placeholders = ", ".join(["?"] + ["?"] * len(valid))
            values = [location_id] + list(valid.values())
            cursor.execute(f"INSERT INTO ambiance_state ({columns}) VALUES ({placeholders})", values)
        conn.commit()
        conn.close()

    def get_all_location_names(self) -> List[str]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM locations")
        rows = cursor.fetchall()
        conn.close()
        return [row["name"] for row in rows]

    def get_present_npcs(self, location_id: int) -> List[Dict]:
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
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE characters SET current_location_id = ? WHERE id = ?", (location_id, character_id))
        conn.commit()
        conn.close()

    # ========================================================================
    # RELATIONSHIPS
    # ========================================================================

    def get_relationships(self, character_id: int) -> List[Dict]:
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
        """Return all active facts with type and source."""
        conn = self._get_connection()
        cursor = conn.cursor()
        if character_id:
            cursor.execute("""
                SELECT id, character_id, fact_text, fact_references AS "references",
                       embedding, importance, confidence, source_type,
                       fact_type, source_character_id,
                       created_turn, last_referenced_turn, expires_at_turn, is_active
                FROM conversational_facts 
                WHERE is_active = 1 AND character_id = ?
                ORDER BY created_turn DESC
            """, (character_id,))
        else:
            cursor.execute("""
                SELECT id, character_id, fact_text, fact_references AS "references",
                       embedding, importance, confidence, source_type,
                       fact_type, source_character_id,
                       created_turn, last_referenced_turn, expires_at_turn, is_active
                FROM conversational_facts 
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
            if 'embedding' in data:
                data['embedding'] = EmbeddingUtils.from_bytes(data['embedding'])
            result.append(data)
        return result
        
    def get_facts_by_day_range(self, character_id: int, start_day: int, end_day: int) -> List[Dict]:
        """
        Get all active facts for a character that occurred between two game days.
        Used for temporal filtering before embedding retrieval.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, character_id, fact_text, fact_references AS "references",
                   embedding, importance, confidence, source_type,
                   fact_type, source_character_id,
                   created_turn, last_referenced_turn, expires_at_turn, game_day, is_active
            FROM conversational_facts
            WHERE is_active = 1
              AND character_id = ?
              AND game_day >= ?
              AND game_day <= ?
            ORDER BY created_turn DESC
        """, (character_id, start_day, end_day))
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
            if 'embedding' in data:
                data['embedding'] = EmbeddingUtils.from_bytes(data['embedding'])
            result.append(data)
        return result

    def insert_conversational_fact(
        self,
        fact_id: str,
        character_id: int,
        fact_text: str,
        references: Union[str, List[str]],
        embedding: List[float] = None,
        importance: float = 0.5,
        confidence: float = 0.9,
        source_type: str = "narrative",
        fact_type: str = "world_fact",          # NEW
        source_character_id: int = None,        # NEW
        created_turn: int = 0,
        last_referenced_turn: int = 0,
        expires_at_turn: int = None
    ):
        """Insert a conversational fact with type and source."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        if isinstance(references, list):
            references_json = json.dumps(references)
        else:
            references_json = references
        
        if embedding is not None:
            embedding_bytes = EmbeddingUtils.to_bytes(embedding)
        else:
            embedding_bytes = None
        
        cursor.execute("""
            INSERT OR REPLACE INTO conversational_facts 
            (id, character_id, fact_text, fact_references, embedding, importance, confidence,
             source_type, fact_type, source_character_id, created_turn, last_referenced_turn, expires_at_turn, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """, (
            fact_id, character_id, fact_text, references_json, embedding_bytes,
            importance, confidence, source_type, fact_type, source_character_id,
            created_turn, last_referenced_turn, expires_at_turn
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
        expires_at_turn: int = None,
        fact_type: str = None,              # NEW
        source_character_id: int = None     # NEW
    ):
        """Update an existing fact. DB column is 'fact_references'."""
        conn = self._get_connection()
        cursor = conn.cursor()
        updates = {}
        
        if new_text is not None:
            updates["fact_text"] = new_text
        if new_references is not None:
            if isinstance(new_references, list):
                updates["fact_references"] = json.dumps(new_references)
            else:
                updates["fact_references"] = new_references
        if importance is not None:
            updates["importance"] = importance
        if confidence is not None:
            updates["confidence"] = confidence
        if last_referenced_turn is not None:
            updates["last_referenced_turn"] = last_referenced_turn
        if expires_at_turn is not None:
            updates["expires_at_turn"] = expires_at_turn
        if fact_type is not None:                       # NEW
            updates["fact_type"] = fact_type
        if source_character_id is not None:             # NEW
            updates["source_character_id"] = source_character_id
        
        if updates:
            set_clause = ", ".join([f"{key} = ?" for key in updates.keys()])
            values = list(updates.values()) + [fact_id]
            cursor.execute(f"UPDATE conversational_facts SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?", values)
            conn.commit()
        conn.close()

    def delete_conversational_fact(self, fact_id: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE conversational_facts SET is_active = 0 WHERE id = ?", (fact_id,))
        conn.commit()
        conn.close()

    def expire_facts_by_turn(self, current_turn: int):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE conversational_facts 
            SET is_active = 0, updated_at = CURRENT_TIMESTAMP
            WHERE expires_at_turn IS NOT NULL AND expires_at_turn <= ?
        """, (current_turn,))
        conn.commit()
        conn.close()
        
    def get_facts_by_type(self, character_id: int, fact_type: str = "world_fact") -> List[Dict]:
        """Get all active facts of a specific type for a character."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, character_id, fact_text, fact_references AS "references",
                   embedding, importance, confidence, source_type,
                   fact_type, source_character_id,
                   created_turn, last_referenced_turn, expires_at_turn, is_active
            FROM conversational_facts 
            WHERE is_active = 1 AND character_id = ? AND fact_type = ?
            ORDER BY created_turn DESC
        """, (character_id, fact_type))
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
            if 'embedding' in data:
                data['embedding'] = EmbeddingUtils.from_bytes(data['embedding'])
            result.append(data)
        return result
        
    def get_belief_facts_by_source(self, source_character_id: int) -> List[Dict]:
        """Get all belief facts expressed by a specific character."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, character_id, fact_text, fact_references AS "references",
                   embedding, importance, confidence, source_type,
                   fact_type, source_character_id,
                   created_turn, last_referenced_turn, expires_at_turn, is_active
            FROM conversational_facts 
            WHERE is_active = 1 AND fact_type = 'belief_fact' AND source_character_id = ?
            ORDER BY created_turn DESC
        """, (source_character_id,))
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
            if 'embedding' in data:
                data['embedding'] = EmbeddingUtils.from_bytes(data['embedding'])
            result.append(data)
        return result
    
    def get_rumor_facts(self, character_id: int = None) -> List[Dict]:
        """Get all active rumor facts (hearsay)."""
        conn = self._get_connection()
        cursor = conn.cursor()
        if character_id:
            cursor.execute("""
                SELECT id, character_id, fact_text, fact_references AS "references",
                       embedding, importance, confidence, source_type,
                       fact_type, source_character_id,
                       created_turn, last_referenced_turn, expires_at_turn, is_active
                FROM conversational_facts 
                WHERE is_active = 1 AND fact_type = 'rumor_fact' AND character_id = ?
                ORDER BY created_turn DESC
            """, (character_id,))
        else:
            cursor.execute("""
                SELECT id, character_id, fact_text, fact_references AS "references",
                       embedding, importance, confidence, source_type,
                       fact_type, source_character_id,
                       created_turn, last_referenced_turn, expires_at_turn, is_active
                FROM conversational_facts 
                WHERE is_active = 1 AND fact_type = 'rumor_fact'
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
            if 'embedding' in data:
                data['embedding'] = EmbeddingUtils.from_bytes(data['embedding'])
            result.append(data)
        return result
    
    # ========================================================================
    # EVENT LOG
    # ========================================================================

    def log_event(self, event_text: str, event_type: str = "narrative", turn: int = 0,
                  importance: float = 0.5, character_id: int = None):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO event_log (event_text, event_type, turn, importance, character_id)
            VALUES (?, ?, ?, ?, ?)
        """, (event_text, event_type, turn, importance, character_id))
        conn.commit()
        conn.close()

    def get_event_log(self, character_id: int = None, limit: int = 100) -> List[Dict]:
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
        valid = self._validate_updates("world_state", updates)
        if not valid:
            return
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM world_state WHERE id = 1")
        if not cursor.fetchone():
            cursor.execute("INSERT INTO world_state (id) VALUES (1)")
        
        set_clause = ", ".join([f"{key} = ?" for key in valid.keys()])
        values = list(valid.values())
        cursor.execute(f"UPDATE world_state SET {set_clause} WHERE id = 1", values)
        conn.commit()
        conn.close()

    # ========================================================================
    # SCENE GRAPH
    # ========================================================================

    def get_scene_graph(self, location_id: int) -> List[Dict]:
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
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM scene_graph WHERE location_id = ? AND object_name = ?", (location_id, object_name))
        row = cursor.fetchone()
        npc_json = json.dumps(npc_present) if npc_present is not None else None
        if row:
            updates = {}
            if object_state is not None:
                updates["object_state"] = object_state
            if npc_json is not None:
                updates["npc_present"] = npc_json
            if visibility is not None:
                updates["visibility"] = visibility
            if updates:
                set_clause = ", ".join([f"{key} = ?" for key in updates.keys()])
                values = list(updates.values()) + [location_id, object_name]
                cursor.execute(f"UPDATE scene_graph SET {set_clause} WHERE location_id = ? AND object_name = ?", values)
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
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM game_state WHERE id = 1")
        row = cursor.fetchone()
        conn.close()
        if row:
            return dict(row)
        return {
            "id": 1,
            "current_location_id": None,
            "current_scene_type": "narrative",
            "combat_active": 0,
            "current_turn": 0
        }

    def update_game_state(self, updates: Dict):
        valid = self._validate_updates("game_state", updates)
        if not valid:
            return
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM game_state WHERE id = 1")
        if not cursor.fetchone():
            cursor.execute("INSERT INTO game_state (id) VALUES (1)")
        set_clause = ", ".join([f"{key} = ?" for key in valid.keys()])
        values = list(valid.values())
        cursor.execute(f"UPDATE game_state SET {set_clause} WHERE id = 1", values)
        conn.commit()
        conn.close()

    def increment_turn(self) -> int:
        """Increment global turn counter and expire facts based on new turn."""
        state = self.get_game_state()
        new_turn = state.get('current_turn', 0) + 1
        self.update_game_state({'current_turn': new_turn})
        self.expire_facts_by_turn(new_turn)
        return new_turn

    # ========================================================================
    # WORKING MEMORY
    # ========================================================================

    def store_recent_prose(self, character_id: int, prose_snippet: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO working_memory (character_id, prose_snippet, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        """, (character_id, prose_snippet))
        conn.commit()
        conn.close()

    def get_recent_prose(self, character_id: int) -> Optional[str]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT prose_snippet FROM working_memory WHERE character_id = ?", (character_id,))
        row = cursor.fetchone()
        conn.close()
        return row["prose_snippet"] if row else None

    # ========================================================================
    # KNOWLEDGE CHUNKS (RAG)
    # ========================================================================

    def store_knowledge_chunk(self, chunk_text: str, embedding: List[float], source_type: str,
                              associated_character_id: int = None):
        conn = self._get_connection()
        cursor = conn.cursor()
        embedding_bytes = EmbeddingUtils.to_bytes(embedding) if embedding is not None else None
        cursor.execute("""
            INSERT INTO knowledge_chunks (chunk_text, embedding, source_type, associated_character_id)
            VALUES (?, ?, ?, ?)
        """, (chunk_text, embedding_bytes, source_type, associated_character_id))
        conn.commit()
        conn.close()

    def get_all_knowledge_chunks(self, character_id: int = None) -> List[Dict]:
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
        result = []
        for row in rows:
            data = dict(row)
            if 'embedding' in data:
                data['embedding'] = EmbeddingUtils.from_bytes(data['embedding'])
            result.append(data)
        return result

    # ========================================================================
    # SCENE HISTORY
    # ========================================================================

    def insert_scene_history(self, character_id: int, summary: str, turn: int = 0):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO scene_history (character_id, emotional_shift_summary, turn)
            VALUES (?, ?, ?)
        """, (character_id, summary, turn))
        conn.commit()
        conn.close()

    def get_scene_history(self, character_id: int, limit: int = 10) -> List[Dict]:
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
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO combat_state (is_active, turn_order, current_turn, round_number)
            VALUES (1, ?, 0, 1)
        """, (json.dumps(turn_order),))
        encounter_id = cursor.lastrowid
        conn.commit()
        conn.close()
        self.update_game_state({"combat_active": 1, "current_scene_type": "combat"})
        return encounter_id

    def end_combat(self):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE combat_state SET is_active = 0 WHERE is_active = 1")
        conn.commit()
        conn.close()
        self.update_game_state({"combat_active": 0, "current_scene_type": "narrative"})

    def update_combat_state(self, updates: Dict):
        valid = self._validate_updates("combat_state", updates)
        if not valid:
            return
        if 'turn_order' in valid and isinstance(valid['turn_order'], list):
            valid['turn_order'] = json.dumps(valid['turn_order'])
        conn = self._get_connection()
        cursor = conn.cursor()
        set_clause = ", ".join([f"{key} = ?" for key in valid.keys()])
        values = list(valid.values())
        cursor.execute(f"UPDATE combat_state SET {set_clause} WHERE is_active = 1", values)
        conn.commit()
        conn.close()
        
# ========================================================================
# D&D STATS (System-Specific)
# ========================================================================

    def get_dnd_stats(self, character_id: int) -> Optional[Dict]:
        """Get D&D 5e stats for a character."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM dnd_stats WHERE character_id = ?", (character_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            data = dict(row)
            # Parse JSON fields
            for field in ['skills', 'armor_proficiencies', 'weapon_proficiencies', 
                          'tool_proficiencies', 'language_proficiencies', 'prepared_spells',
                          'known_spells', 'racial_traits', 'class_features', 'feats',
                          'equipment', 'maneuvers']:
                if field in data and data[field]:
                    try:
                        data[field] = json.loads(data[field])
                    except:
                        data[field] = []
            return data
        return None

    def update_dnd_stats(self, character_id: int, updates: Dict):
        """Update D&D 5e stats for a character."""
        valid = self._validate_updates("dnd_stats", updates)
        if not valid:
            return
        
        # Handle JSON serialization for complex fields
        for field in ['skills', 'armor_proficiencies', 'weapon_proficiencies', 
                      'tool_proficiencies', 'language_proficiencies', 'prepared_spells',
                      'known_spells', 'racial_traits', 'class_features', 'feats',
                      'equipment', 'maneuvers']:
            if field in valid and isinstance(valid[field], (list, dict)):
                valid[field] = json.dumps(valid[field])
        
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM dnd_stats WHERE character_id = ?", (character_id,))
        exists = cursor.fetchone()
        
        if exists:
            set_clause = ", ".join([f"{key} = ?" for key in valid.keys()])
            values = list(valid.values()) + [character_id]
            cursor.execute(f"UPDATE dnd_stats SET {set_clause} WHERE character_id = ?", values)
        else:
            columns = ", ".join(["character_id"] + list(valid.keys()))
            placeholders = ", ".join(["?"] + ["?"] * len(valid))
            values = [character_id] + list(valid.values())
            cursor.execute(f"INSERT INTO dnd_stats ({columns}) VALUES ({placeholders})", values)
        conn.commit()
        conn.close()