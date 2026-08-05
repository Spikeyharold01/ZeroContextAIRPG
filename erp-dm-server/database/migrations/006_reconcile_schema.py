"""Atomic reconciliation to the complete operational schema (version 6)."""

from __future__ import annotations

_approved_foreign_key_baseline = None

import json
from pathlib import Path
import sqlite3

from database_schema_manifest_v6 import (
    CURRENT_SCHEMA_PATH,
    REQUIRED_SQL_FRAGMENTS,
    SchemaManifest,
    inspect_schema,
    load_current_manifest as load_latest_manifest,
    normalize_sql,
    quote_identifier,
    schema_differences,
)


MIGRATION_VERSION = 6
VERSION_7_TABLES = frozenset({
    "campaigns",
    "state_documents",
    "state_patch_log",
    "state_idempotency",
    "state_projection_definitions",
    "state_projection_values",
})


def load_current_manifest() -> SchemaManifest:
    """Return the historical v6 contract, excluding additive v7 objects."""
    latest = load_latest_manifest()
    return SchemaManifest(tuple(table for table in latest.tables if table.name not in VERSION_7_TABLES))


def _without_v7(manifest: SchemaManifest) -> SchemaManifest:
    return SchemaManifest(tuple(table for table in manifest.tables if table.name not in VERSION_7_TABLES))
SAFE_ADDITIVE_COLUMNS = {
    ("characters", "prose_fingerprint"),
    ("characters", "status"),
    ("characters", "is_active"),
    ("game_state", "game_day"),
}
JSON_FIELDS = {
    "characters": ("plot_state",),
    "mechanical_stats": ("conditions",),
    "dnd_stats": (
        "skills", "armor_proficiencies", "weapon_proficiencies",
        "tool_proficiencies", "language_proficiencies", "prepared_spells",
        "known_spells", "racial_traits", "class_features", "feats",
        "equipment", "maneuvers",
    ),
    "combat_state": ("turn_order",),
    "conversational_facts": ("fact_references",),
    "world_state": ("additional_state",),
    "scene_graph": ("npc_present",),
}


class ReconciliationError(RuntimeError):
    """The source database cannot be reconciled without explicit user action."""


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _column_names(conn: sqlite3.Connection, table: str) -> tuple[str, ...]:
    return tuple(
        row[1]
        for row in conn.execute(
            f"PRAGMA table_info({quote_identifier(table)})"
        )
    )


def _canonical_table_sql(table: str) -> str:
    canonical = sqlite3.connect(":memory:")
    try:
        canonical.executescript(CURRENT_SCHEMA_PATH.read_text(encoding="utf-8"))
        row = canonical.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        if row is None:
            raise ReconciliationError(f"Canonical schema has no table {table}")
        return row[0]
    finally:
        canonical.close()


def _create_sql_for_name(table: str, replacement: str) -> str:
    sql = _canonical_table_sql(table)
    prefix = f"CREATE TABLE {table}"
    if not sql.startswith(prefix):
        raise ReconciliationError(f"Cannot rewrite canonical CREATE TABLE for {table}")
    return f"CREATE TABLE {quote_identifier(replacement)}" + sql[len(prefix):]


def _unknown_schema_elements(conn: sqlite3.Connection, expected: SchemaManifest) -> list[str]:
    expected_tables = expected.by_name()
    problems = []
    for table in sorted(_table_names(conn) - expected_tables.keys() - VERSION_7_TABLES):
        problems.append(f"unknown table {table}")
    for table in sorted(_table_names(conn) & expected_tables.keys()):
        expected_columns = {column.name for column in expected_tables[table].columns}
        unknown_columns = sorted(set(_column_names(conn, table)) - expected_columns)
        if unknown_columns:
            problems.append(
                f"unknown columns in {table}: {', '.join(unknown_columns)}"
            )
    return problems


def _validate_character_types(conn: sqlite3.Connection) -> None:
    if "characters" not in _table_names(conn) or "type" not in _column_names(conn, "characters"):
        return
    invalid = conn.execute(
        "SELECT id, type FROM characters "
        "WHERE type IS NOT NULL AND type NOT IN ('PC', 'NPC', 'Monster') "
        "ORDER BY id"
    ).fetchall()
    if invalid:
        details = ", ".join(f"id={row[0]} type={row[1]!r}" for row in invalid)
        raise ReconciliationError(f"invalid character types: {details}")


def _validate_json(conn: sqlite3.Connection) -> None:
    tables = _table_names(conn)
    failures = []
    for table, fields in JSON_FIELDS.items():
        if table not in tables:
            continue
        columns = set(_column_names(conn, table))
        key_column = "id" if "id" in columns else (
            "character_id" if "character_id" in columns else "rowid"
        )
        for field in fields:
            if field not in columns:
                continue
            query = (
                f"SELECT {quote_identifier(key_column)}, {quote_identifier(field)} "
                f"FROM {quote_identifier(table)} "
                f"WHERE {quote_identifier(field)} IS NOT NULL"
            )
            for key, value in conn.execute(query):
                try:
                    json.loads(value)
                except (TypeError, ValueError, json.JSONDecodeError):
                    failures.append(f"{table} row {key} field {field}={value!r}")
    if failures:
        raise ReconciliationError("malformed JSON: " + "; ".join(failures))


def _validate_orphans(conn: sqlite3.Connection, expected: SchemaManifest) -> None:
    tables = _table_names(conn)
    failures = []
    for table in expected.tables:
        if table.name not in tables:
            continue
        source_columns = set(_column_names(conn, table.name))
        for foreign_key in table.foreign_keys:
            if len(foreign_key.source_columns) != 1 or len(foreign_key.target_columns) != 1:
                raise ReconciliationError(
                    f"unsupported composite foreign key on {table.name}"
                )
            source = foreign_key.source_columns[0]
            target = foreign_key.target_columns[0]
            if source not in source_columns:
                continue
            if foreign_key.target_table not in tables:
                query = (
                    f"SELECT rowid, {quote_identifier(source)} "
                    f"FROM {quote_identifier(table.name)} "
                    f"WHERE {quote_identifier(source)} IS NOT NULL ORDER BY rowid"
                )
                for rowid, value in conn.execute(query):
                    failures.append(
                        f"{table.name} row {rowid} {source}={value!r} -> "
                        f"missing table {foreign_key.target_table}"
                    )
                continue
            target_columns = set(_column_names(conn, foreign_key.target_table))
            if target not in target_columns:
                continue
            query = f"""
                SELECT child.rowid, child.{quote_identifier(source)}
                FROM {quote_identifier(table.name)} AS child
                LEFT JOIN {quote_identifier(foreign_key.target_table)} AS parent
                  ON child.{quote_identifier(source)} = parent.{quote_identifier(target)}
                WHERE child.{quote_identifier(source)} IS NOT NULL
                  AND parent.{quote_identifier(target)} IS NULL
                ORDER BY child.rowid
            """
            for rowid, value in conn.execute(query):
                failures.append(
                    f"{table.name} row {rowid} {source}={value!r} -> "
                    f"missing {foreign_key.target_table}.{target}"
                )
    if failures:
        raise ReconciliationError("orphaned foreign keys: " + "; ".join(failures))


def validate_source_database(conn: sqlite3.Connection, expected: SchemaManifest) -> None:
    problems = _unknown_schema_elements(conn, expected)
    if problems:
        raise ReconciliationError("incompatible custom schema: " + "; ".join(problems))
    _validate_character_types(conn)
    _validate_json(conn)
    if not _approved_foreign_key_baseline:
        _validate_orphans(conn, expected)


def _requires_rebuild(actual_table, expected_table) -> bool:
    actual_columns = {column.name: column for column in actual_table.columns}
    expected_columns = {column.name: column for column in expected_table.columns}
    missing = set(expected_columns) - set(actual_columns)
    if missing and all((expected_table.name, column) in SAFE_ADDITIVE_COLUMNS for column in missing):
        existing_expected = tuple(
            column for column in expected_table.columns if column.name in actual_columns
        )
        if (
            actual_table.columns == existing_expected
            and actual_table.foreign_keys == expected_table.foreign_keys
        ):
            return False
    return (
        actual_table.columns != expected_table.columns
        or actual_table.foreign_keys != expected_table.foreign_keys
        or any(
            normalize_sql(fragment) not in normalize_sql(actual_table.create_sql)
            for fragment in REQUIRED_SQL_FRAGMENTS.get(expected_table.name, ())
        )
    )


def tables_requiring_rebuild(conn: sqlite3.Connection) -> list[str]:
    actual = inspect_schema(conn).by_name()
    expected = load_current_manifest().by_name()
    return [
        table
        for table in sorted(actual.keys() & expected.keys())
        if _requires_rebuild(actual[table], expected[table])
    ]


def database_has_user_data(conn: sqlite3.Connection) -> bool:
    """Return whether any application table contains rows worth backing up."""
    for table in sorted(_table_names(conn) - {"schema_version"}):
        if conn.execute(
            f"SELECT 1 FROM {quote_identifier(table)} LIMIT 1"
        ).fetchone():
            return True
    return False


def create_verified_backup(db_path: str) -> Path | None:
    """Create and verify a side-by-side backup for an on-disk database."""
    if db_path == ":memory:" or not Path(db_path).is_file():
        return None
    backup_path = Path(f"{db_path}.pre-v6.bak")
    source = sqlite3.connect(db_path)
    destination = sqlite3.connect(backup_path)
    try:
        source.backup(destination)
        result = destination.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise ReconciliationError(
                f"backup verification failed for {backup_path}: {result}"
            )
    finally:
        destination.close()
        source.close()
    return backup_path


def _add_safe_columns(conn: sqlite3.Connection, table: str, missing: set[str]) -> None:
    definitions = {
        ("characters", "prose_fingerprint"): "prose_fingerprint TEXT",
        ("characters", "status"): "status TEXT DEFAULT 'active'",
        ("characters", "is_active"): "is_active BOOLEAN DEFAULT 1",
        ("game_state", "game_day"): "game_day INTEGER DEFAULT 1",
    }
    for column in sorted(missing):
        definition = definitions.get((table, column))
        if definition is None:
            raise ReconciliationError(f"unsafe additive column {table}.{column}")
        conn.execute(
            f"ALTER TABLE {quote_identifier(table)} ADD COLUMN {definition}"
        )


def _rebuild_table(conn: sqlite3.Connection, table: str, expected_table) -> None:
    source_columns = set(_column_names(conn, table))
    target_columns = [column.name for column in expected_table.columns]
    copied_columns = [column for column in target_columns if column in source_columns]
    missing_required = [
        column.name
        for column in expected_table.columns
        if column.name not in source_columns
        and column.not_null
        and column.default is None
        and column.primary_key_position == 0
    ]
    row_count = conn.execute(
        f"SELECT COUNT(*) FROM {quote_identifier(table)}"
    ).fetchone()[0]
    if row_count and missing_required:
        raise ReconciliationError(
            f"cannot rebuild {table}: populated rows lack required columns "
            + ", ".join(missing_required)
        )

    temporary = f"__migration_v6_{table}"
    conn.execute(f"DROP TABLE IF EXISTS {quote_identifier(temporary)}")
    conn.execute(_create_sql_for_name(table, temporary))
    if copied_columns:
        columns_sql = ", ".join(quote_identifier(column) for column in copied_columns)
        conn.execute(
            f"INSERT INTO {quote_identifier(temporary)} ({columns_sql}) "
            f"SELECT {columns_sql} FROM {quote_identifier(table)}"
        )
    conn.execute(f"DROP TABLE {quote_identifier(table)}")
    conn.execute(
        f"ALTER TABLE {quote_identifier(temporary)} RENAME TO {quote_identifier(table)}"
    )
    _after_table_rebuild(table)


def _after_table_rebuild(table: str) -> None:
    """Test seam used to prove that rebuild failures roll back atomically."""


def _create_indexes(conn: sqlite3.Connection, expected: SchemaManifest) -> None:
    canonical = sqlite3.connect(":memory:")
    try:
        canonical.executescript(CURRENT_SCHEMA_PATH.read_text(encoding="utf-8"))
        index_sql = canonical.execute(
            "SELECT name, sql, tbl_name FROM sqlite_master "
            "WHERE type = 'index' AND sql IS NOT NULL ORDER BY name"
        ).fetchall()
    finally:
        canonical.close()
    expected_tables = expected.by_name()
    for name, sql, table_name in index_sql:
        if table_name not in expected_tables:
            continue
        existing = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ?",
            (name,),
        ).fetchone()
        if existing is None:
            conn.execute(sql.replace("CREATE INDEX", "CREATE INDEX IF NOT EXISTS", 1))


def _insert_missing_character_state(conn: sqlite3.Connection) -> None:
    if not {"characters", "emotional_state", "mechanical_stats"}.issubset(_table_names(conn)):
        return
    conn.execute(
        """
        INSERT INTO emotional_state (character_id)
        SELECT id FROM characters
        WHERE id NOT IN (SELECT character_id FROM emotional_state)
        """
    )
    conn.execute(
        """
        INSERT INTO mechanical_stats (character_id)
        SELECT id FROM characters
        WHERE id NOT IN (SELECT character_id FROM mechanical_stats)
        """
    )


def _operational_smoke_check(conn: sqlite3.Connection) -> None:
    """Exercise representative reads/writes and roll them back to a savepoint."""
    conn.execute("SAVEPOINT migration_v6_operational_check")
    try:
        conn.execute(
            "INSERT INTO characters (id, name, type) VALUES (-6000, 'Migration Check', 'NPC')"
        )
        conn.execute("INSERT INTO emotional_state (character_id) VALUES (-6000)")
        conn.execute("INSERT INTO mechanical_stats (character_id) VALUES (-6000)")
        conn.execute("INSERT INTO locations (id, name) VALUES (-6000, 'Migration Check')")
        conn.execute("INSERT INTO ambiance_state (location_id) VALUES (-6000)")
        conn.execute(
            "INSERT INTO relationships (character_a_id, character_b_id) VALUES (-6000, -6000)"
        )
        conn.execute(
            "INSERT INTO conversational_facts "
            "(id, character_id, fact_text, fact_references, embedding, fact_type, game_day) "
            "VALUES ('__migration_check__', -6000, 'check', '[]', X'0000803F', 'world_fact', 1)"
        )
        conn.execute(
            "UPDATE conversational_facts SET last_referenced_turn = 1 "
            "WHERE id = '__migration_check__'"
        )
        conn.execute(
            "INSERT INTO event_log (event_text, character_id) VALUES ('check', -6000)"
        )
        conn.execute("INSERT OR REPLACE INTO world_state (id) VALUES (1)")
        conn.execute(
            "INSERT INTO scene_graph (location_id, object_name, npc_present) "
            "VALUES (-6000, 'check', '[]')"
        )
        conn.execute("INSERT OR REPLACE INTO game_state (id, game_day) VALUES (1, 1)")
        conn.execute(
            "INSERT INTO working_memory (character_id, prose_snippet) VALUES (-6000, 'check')"
        )
        conn.execute(
            "INSERT INTO knowledge_chunks (chunk_text, embedding, associated_character_id) "
            "VALUES ('check', X'0000803F', -6000)"
        )
        conn.execute(
            "INSERT INTO scene_history (character_id, emotional_shift_summary) "
            "VALUES (-6000, 'check')"
        )
        conn.execute("INSERT INTO combat_state (is_active) VALUES (1)")
        conn.execute("INSERT INTO dnd_stats (character_id, class) VALUES (-6000, 'Fighter')")
    finally:
        conn.execute("ROLLBACK TO migration_v6_operational_check")
        conn.execute("RELEASE migration_v6_operational_check")


def reconcile(conn: sqlite3.Connection) -> None:
    """Reconcile the current transaction to the canonical version-6 schema."""
    expected = load_current_manifest()
    validate_source_database(conn, expected)

    actual_by_name = _without_v7(inspect_schema(conn)).by_name()
    expected_by_name = expected.by_name()
    for table in expected.tables:
        if table.name == "schema_version":
            continue
        if table.name not in actual_by_name:
            conn.execute(_canonical_table_sql(table.name))
            continue
        actual_table = actual_by_name[table.name]
        actual_columns = {column.name for column in actual_table.columns}
        expected_columns = {column.name for column in table.columns}
        missing = expected_columns - actual_columns
        if not _requires_rebuild(actual_table, table):
            if missing:
                _add_safe_columns(conn, table.name, missing)
        else:
            _rebuild_table(conn, table.name, table)

    _insert_missing_character_state(conn)
    _create_indexes(conn, expected)

    differences = schema_differences(
        _without_v7(inspect_schema(conn)), expected, REQUIRED_SQL_FRAGMENTS
    )
    if differences:
        raise ReconciliationError(
            "schema reconciliation did not reach canonical parity: "
            + "; ".join(differences)
        )

    foreign_key_failures = conn.execute("PRAGMA foreign_key_check").fetchall()
    approved = _approved_foreign_key_baseline
    if foreign_key_failures and (approved is None or [tuple(row) for row in foreign_key_failures] != approved):
        raise ReconciliationError(
            "PRAGMA foreign_key_check failed: " + repr(foreign_key_failures)
        )
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise ReconciliationError(f"PRAGMA integrity_check failed: {integrity}")
    _validate_json(conn)
    _operational_smoke_check(conn)
