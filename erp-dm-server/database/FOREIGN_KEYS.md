# SQLite foreign-key policy

`DatabaseManager._get_connection()` enables `PRAGMA foreign_keys = ON` for
every connection. The version-6 reconciliation temporarily disables
enforcement only while rebuilding tables; it validates references before the
rebuild and runs `PRAGMA foreign_key_check` before recording version 6.

The schema does not declare `ON DELETE` or `ON UPDATE` clauses. SQLite therefore
reports `NO ACTION` for both actions. This intentionally rejects physical
parent deletion or key changes while dependent rows exist; no relationship
uses `CASCADE` or `SET NULL`.

## Relationship policy

| Child relationship | SQLite action | Application policy |
| --- | --- | --- |
| `characters.current_location_id -> locations.id` | `NO ACTION` | Location deletion is rejected while assigned characters exist. Reassign characters first. |
| `ambiance_state.location_id -> locations.id` | `NO ACTION` | Location deletion is rejected while ambiance exists. |
| `emotional_state.character_id -> characters.id` | `NO ACTION` | Character lifecycle is application-managed soft deletion. |
| `mechanical_stats.character_id -> characters.id` | `NO ACTION` | Character lifecycle is application-managed soft deletion. |
| `dnd_stats.character_id -> characters.id` | `NO ACTION` | Character lifecycle is application-managed soft deletion. |
| `inventory.character_id -> characters.id` | `NO ACTION` | Character lifecycle is application-managed soft deletion. |
| `relationships.character_a_id -> characters.id` | `NO ACTION` | Character lifecycle is application-managed soft deletion. |
| `relationships.character_b_id -> characters.id` | `NO ACTION` | Character lifecycle is application-managed soft deletion. |
| `scene_history.character_id -> characters.id` | `NO ACTION` | Character lifecycle is application-managed soft deletion. |
| `conversational_facts.character_id -> characters.id` | `NO ACTION` | Character lifecycle is application-managed soft deletion; facts have their own `is_active` flag. |
| `conversational_facts.source_character_id -> characters.id` | `NO ACTION` | Belief provenance must continue to reference an existing character. |
| `event_log.character_id -> characters.id` | `NO ACTION` | Historical events retain their character reference. |
| `working_memory.character_id -> characters.id` | `NO ACTION` | Character lifecycle is application-managed soft deletion. |
| `knowledge_chunks.associated_character_id -> characters.id` | `NO ACTION` | Character lifecycle is application-managed soft deletion. |
| `scene_graph.location_id -> locations.id` | `NO ACTION` | Location deletion is rejected while scene state exists. |
| `game_state.current_location_id -> locations.id` | `NO ACTION` | Move or clear game state before physically deleting a location. |

## Character deletion

`characters.status` and `characters.is_active` define the current character
lifecycle policy. Deactivating a character updates those fields and preserves
emotional state, mechanics, inventory, relationships, facts, events, working
memory, and other history. The manager does not physically delete characters.
Physical deletion, if introduced later, requires an explicit retention policy
and schema migration rather than changing foreign-key actions for convenience.
