# database/db_manager.py
import sqlite3
import json
from typing import Optional, Dict, List, Any

class DatabaseManager:
    def __init__(self, db_path: str = "data/game.db"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Initialize the database with schema.sql if it doesn't exist."""
        import os
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        conn = sqlite3.connect(self.db_path)
        with open("database/schema.sql", "r") as f:
            conn.executescript(f.read())
        conn.commit()
        conn.close()
    
    def _get_connection(self):
        """Get a database connection with JSON serialization support."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Allows accessing columns by name
        return conn
    
    # ========== CHARACTER OPERATIONS ==========
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
    
    def get_character_card(self, character_id: int) -> Optional[str]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT full_card_text FROM characters WHERE id = ?", (character_id,))
        row = cursor.fetchone()
        conn.close()
        return row["full_card_text"] if row else None
    
    def create_character(self, name: str, full_card_text: str, character_type: str = "NPC") -> int:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO characters (name, type, full_card_text) VALUES (?, ?, ?)",
            (name, character_type, full_card_text)
        )
        character_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return character_id
    
    # ========== EMOTIONAL STATE OPERATIONS ==========
    def get_emotional_state(self, character_id: int) -> Optional[Dict]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM emotional_state WHERE character_id = ?", (character_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    
    def update_emotional_state(self, character_id: int, updates: Dict):
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Build dynamic UPDATE query
        set_clause = ", ".join([f"{key} = ?" for key in updates.keys()])
        values = list(updates.values()) + [character_id]
        
        cursor.execute(f"""
            UPDATE emotional_state 
            SET {set_clause}, emotional_shift = ?
            WHERE character_id = ?
        """, values + [updates.get("emotional_shift", None)])
        
        if cursor.rowcount == 0:
            # Insert if not exists
            columns = ", ".join(updates.keys())
            placeholders = ", ".join(["?"] * len(updates))
            cursor.execute(f"""
                INSERT INTO emotional_state (character_id, {columns}) 
                VALUES (?, {placeholders})
            """, [character_id] + list(updates.values()))
        
        conn.commit()
        conn.close()
    
    # ========== MECHANICAL STATS OPERATIONS ==========
    def get_mechanical_stats(self, character_id: int) -> Optional[Dict]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM mechanical_stats WHERE character_id = ?", (character_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    
    def update_mechanical_stats(self, character_id: int, updates: Dict):
        conn = self._get_connection()
        cursor = conn.cursor()
        
        set_clause = ", ".join([f"{key} = ?" for key in updates.keys()])
        values = list(updates.values()) + [character_id]
        
        cursor.execute(f"""
            UPDATE mechanical_stats 
            SET {set_clause}
            WHERE character_id = ?
        """, values)
        
        if cursor.rowcount == 0:
            columns = ", ".join(updates.keys())
            placeholders = ", ".join(["?"] * len(updates))
            cursor.execute(f"""
                INSERT INTO mechanical_stats (character_id, {columns}) 
                VALUES (?, {placeholders})
            """, [character_id] + list(updates.values()))
        
        conn.commit()
        conn.close()
    
    # ========== LOCATION OPERATIONS ==========
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
    
    # ========== NPC / PRESENCE OPERATIONS ==========
    def get_present_npcs(self, location_id: int) -> List[Dict]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT c.* FROM characters c
            WHERE c.current_location_id = ? AND c.type = 'NPC'
        """, (location_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    def update_character_location(self, character_id: int, location_id: int):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE characters SET current_location_id = ? WHERE id = ?",
            (location_id, character_id)
        )
        conn.commit()
        conn.close()
    
    # ========== RELATIONSHIP OPERATIONS ==========
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
    
    # ========== UTILITY ==========
    def get_all_character_names(self) -> List[str]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM characters")
        rows = cursor.fetchall()
        conn.close()
        return [row["name"] for row in rows]
    
    def get_all_location_names(self) -> List[str]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM locations")
        rows = cursor.fetchall()
        conn.close()
        return [row["name"] for row in rows]