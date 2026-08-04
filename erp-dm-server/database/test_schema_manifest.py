from pathlib import Path
import sqlite3
import sys


sys.path.insert(0, str(Path(__file__).parent))

from schema_manifest import (
    REQUIRED_SQL_FRAGMENTS,
    VERSION_1_BASELINE_PATH,
    inspect_schema,
    load_current_manifest,
    load_version_1_manifest,
    schema_differences,
)


def test_version_1_baseline_is_the_immutable_fbf3a88_snapshot():
    baseline = VERSION_1_BASELINE_PATH.read_text(encoding="utf-8")

    assert baseline.startswith("-- ============================================================")
    assert "CREATE TABLE schema_version" not in baseline
    assert "prose_fingerprint TEXT" in baseline
    assert "game_day INTEGER DEFAULT 1" in baseline
    assert "status TEXT DEFAULT 'active'" not in baseline
    version_1 = load_version_1_manifest()
    character_columns = {
        column.name for column in version_1.by_name()["characters"].columns
    }
    assert "is_active" not in character_columns
    assert len(version_1.tables) == 17


def test_current_schema_manifest_is_self_consistent():
    expected = load_current_manifest()

    assert schema_differences(expected, expected, REQUIRED_SQL_FRAGMENTS) == []
    assert "schema_version" in expected.by_name()
    assert len(expected.by_name()["characters"].columns) == 21


def test_schema_comparison_reports_columns_foreign_keys_indexes_and_constraints():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE parent (id INTEGER PRIMARY KEY);
        CREATE TABLE child (
            id INTEGER PRIMARY KEY,
            parent_id INTEGER,
            FOREIGN KEY (parent_id) REFERENCES parent(id)
        );
        CREATE INDEX child_parent ON child(parent_id);
        """
    )
    expected = inspect_schema(conn)
    conn.execute("DROP INDEX child_parent")
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("ALTER TABLE child RENAME TO old_child")
    conn.execute("CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id TEXT)")
    conn.execute("INSERT INTO child SELECT id, parent_id FROM old_child")
    conn.execute("DROP TABLE old_child")
    actual = inspect_schema(conn)
    conn.close()

    differences = schema_differences(actual, expected)
    assert "column contract mismatch for child" in differences
    assert "foreign-key contract mismatch for child" in differences
    assert "index contract mismatch for child" in differences


def test_version_1_difference_from_current_is_explicit():
    baseline = load_version_1_manifest()
    current = load_current_manifest()
    differences = schema_differences(baseline, current, REQUIRED_SQL_FRAGMENTS)

    assert differences == [
        "missing table campaigns",
        "missing table schema_version",
        "missing table state_documents",
        "missing table state_idempotency",
        "missing table state_patch_log",
        "missing table state_projection_definitions",
        "missing table state_projection_values",
        "column contract mismatch for characters",
    ]
