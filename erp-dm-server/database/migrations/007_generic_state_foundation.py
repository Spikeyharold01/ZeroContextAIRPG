"""Add the campaign-bound generic state persistence foundation."""

from __future__ import annotations

import sqlite3
from uuid import UUID, uuid4


MIGRATION_TABLES = (
    "campaigns",
    "state_documents",
    "state_patch_log",
    "state_idempotency",
    "state_projection_definitions",
    "state_projection_values",
)
_before_validation = lambda: None


def migrate(
    conn: sqlite3.Connection, schema_sql: str, campaign_id: str | None = None
) -> str:
    """Create only v7 tables/indexes and return the stable campaign UUID."""
    source = sqlite3.connect(":memory:")
    try:
        source.executescript(schema_sql)
        for table in MIGRATION_TABLES:
            sql = source.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()[0]
            conn.execute(sql.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1))
        indexes = source.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL "
            "AND tbl_name IN (%s) ORDER BY name" % ",".join("?" * len(MIGRATION_TABLES)),
            MIGRATION_TABLES,
        ).fetchall()
        for (sql,) in indexes:
            conn.execute(sql.replace("CREATE ", "CREATE ", 1).replace(
                " INDEX ", " INDEX IF NOT EXISTS ", 1
            ))
    finally:
        source.close()

    existing = conn.execute("SELECT id FROM campaigns").fetchall()
    if existing:
        if len(existing) != 1:
            raise RuntimeError("v7 migration requires at most one campaign row")
        return existing[0][0]
    campaign_id = campaign_id or str(uuid4())
    UUID(campaign_id)
    turn_row = conn.execute(
        "SELECT current_turn FROM game_state WHERE id=1"
    ).fetchone() if _table_exists(conn, "game_state") else None
    current_turn = int(turn_row[0]) if turn_row is not None else 0
    conn.execute(
        "INSERT INTO campaigns (id, display_name, current_turn) VALUES (?, ?, ?)",
        (campaign_id, "Migrated Campaign", current_turn),
    )
    _before_validation()
    return campaign_id


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None
