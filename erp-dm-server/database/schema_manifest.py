"""Canonical SQLite schema inspection and comparison utilities.

The current manifest is derived from ``schema.sql``, the authoritative fresh-
database contract.  The immutable version-1 baseline is stored separately at
``baselines/001_schema.sql`` and corresponds to Git commit fbf3a88.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import sqlite3
from typing import Iterable


DATABASE_DIR = Path(__file__).resolve().parent
CURRENT_SCHEMA_PATH = DATABASE_DIR / "schema.sql"
VERSION_1_BASELINE_PATH = DATABASE_DIR / "baselines" / "001_schema.sql"

# SQLite exposes CHECK clauses only through the CREATE TABLE SQL text.  These
# normalized fragments are the constraints that form part of the contract.
REQUIRED_SQL_FRAGMENTS = {
    "schema_version": ("CHECK (id = 1)",),
    "characters": ("CHECK(type IN ('PC', 'NPC', 'Monster'))",),
    "world_state": ("CHECK (id = 1)",),
    "game_state": ("CHECK (id = 1)",),
}


@dataclass(frozen=True)
class ColumnManifest:
    name: str
    declared_type: str
    not_null: bool
    default: str | None
    primary_key_position: int


@dataclass(frozen=True)
class ForeignKeyManifest:
    source_columns: tuple[str, ...]
    target_table: str
    target_columns: tuple[str, ...]
    on_update: str
    on_delete: str


@dataclass(frozen=True)
class IndexManifest:
    name: str
    unique: bool
    columns: tuple[str, ...]


@dataclass(frozen=True)
class TableManifest:
    name: str
    columns: tuple[ColumnManifest, ...]
    foreign_keys: tuple[ForeignKeyManifest, ...]
    indexes: tuple[IndexManifest, ...]
    create_sql: str


@dataclass(frozen=True)
class SchemaManifest:
    tables: tuple[TableManifest, ...]

    def by_name(self) -> dict[str, TableManifest]:
        return {table.name: table for table in self.tables}


def quote_identifier(identifier: str) -> str:
    """Quote an inspected SQLite identifier."""
    return '"' + identifier.replace('"', '""') + '"'


def _normalize_default(value: object) -> str | None:
    if value is None:
        return None
    return re.sub(r"\s+", " ", str(value).strip())


def _foreign_keys(conn: sqlite3.Connection, table: str) -> tuple[ForeignKeyManifest, ...]:
    grouped: dict[int, list[sqlite3.Row]] = {}
    query = f"PRAGMA foreign_key_list({quote_identifier(table)})"
    for row in conn.execute(query):
        grouped.setdefault(row["id"], []).append(row)

    result = []
    for foreign_key_id in sorted(grouped):
        rows = sorted(grouped[foreign_key_id], key=lambda row: row["seq"])
        result.append(
            ForeignKeyManifest(
                source_columns=tuple(row["from"] for row in rows),
                target_table=rows[0]["table"],
                target_columns=tuple(row["to"] for row in rows),
                on_update=rows[0]["on_update"],
                on_delete=rows[0]["on_delete"],
            )
        )
    return tuple(result)


def _indexes(conn: sqlite3.Connection, table: str) -> tuple[IndexManifest, ...]:
    result = []
    query = f"PRAGMA index_list({quote_identifier(table)})"
    for row in conn.execute(query):
        # Primary-key autoindexes are represented by table_info; retain UNIQUE
        # autoindexes because they express a required table constraint.
        if row["origin"] == "pk":
            continue
        index_name = row["name"]
        columns = tuple(
            info["name"]
            for info in sorted(
                conn.execute(
                    f"PRAGMA index_info({quote_identifier(index_name)})"
                ).fetchall(),
                key=lambda info: info["seqno"],
            )
        )
        result.append(
            IndexManifest(
                name=index_name,
                unique=bool(row["unique"]),
                columns=columns,
            )
        )
    return tuple(sorted(result, key=lambda index: index.name))


def inspect_schema(conn: sqlite3.Connection) -> SchemaManifest:
    """Inspect tables, columns, foreign keys, and indexes through SQLite APIs."""
    previous_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT name, sql
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()
        tables = []
        for row in rows:
            table_name = row["name"]
            columns = tuple(
                ColumnManifest(
                    name=column["name"],
                    declared_type=(column["type"] or "").upper(),
                    not_null=bool(column["notnull"]),
                    default=_normalize_default(column["dflt_value"]),
                    primary_key_position=int(column["pk"]),
                )
                for column in conn.execute(
                    f"PRAGMA table_info({quote_identifier(table_name)})"
                )
            )
            tables.append(
                TableManifest(
                    name=table_name,
                    columns=columns,
                    foreign_keys=_foreign_keys(conn, table_name),
                    indexes=_indexes(conn, table_name),
                    create_sql=row["sql"] or "",
                )
            )
        return SchemaManifest(tuple(tables))
    finally:
        conn.row_factory = previous_factory


def manifest_from_sql(schema_sql: str) -> SchemaManifest:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(schema_sql)
        return inspect_schema(conn)
    finally:
        conn.close()


def load_current_manifest() -> SchemaManifest:
    return manifest_from_sql(CURRENT_SCHEMA_PATH.read_text(encoding="utf-8"))


def load_version_1_manifest() -> SchemaManifest:
    return manifest_from_sql(VERSION_1_BASELINE_PATH.read_text(encoding="utf-8"))


def normalize_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip().upper()


def schema_differences(
    actual: SchemaManifest,
    expected: SchemaManifest,
    required_sql_fragments: dict[str, Iterable[str]] | None = None,
) -> list[str]:
    """Return precise differences from the expected operational contract."""
    differences = []
    actual_tables = actual.by_name()
    expected_tables = expected.by_name()

    for table_name in sorted(expected_tables.keys() - actual_tables.keys()):
        differences.append(f"missing table {table_name}")
    for table_name in sorted(actual_tables.keys() - expected_tables.keys()):
        differences.append(f"unexpected table {table_name}")

    for table_name in sorted(expected_tables.keys() & actual_tables.keys()):
        actual_table = actual_tables[table_name]
        expected_table = expected_tables[table_name]
        if actual_table.columns != expected_table.columns:
            differences.append(f"column contract mismatch for {table_name}")
        if actual_table.foreign_keys != expected_table.foreign_keys:
            differences.append(f"foreign-key contract mismatch for {table_name}")
        if actual_table.indexes != expected_table.indexes:
            differences.append(f"index contract mismatch for {table_name}")

    fragments = required_sql_fragments or {}
    for table_name, required in fragments.items():
        table = actual_tables.get(table_name)
        if table is None:
            continue
        normalized_actual = normalize_sql(table.create_sql)
        for fragment in required:
            if normalize_sql(fragment) not in normalized_actual:
                differences.append(
                    f"required constraint missing from {table_name}: {fragment}"
                )
    return differences
