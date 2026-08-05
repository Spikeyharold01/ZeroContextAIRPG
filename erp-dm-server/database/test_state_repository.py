import json
import sqlite3
import sys
import threading
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from config import StatePersistenceConfig
from contracts.state import StatePatch, StatePatchConflict
from database.db_manager import DatabaseManager
from database.state_repository import (
    CampaignIdentityError,
    StateBusyError,
    StateIdempotencyConflict,
    StateIntegrityError,
    StateLimitError,
    StatePersistenceError,
    StateRepository,
    TrustedStatePolicy,
)


TARGETS = {
    "story.world": {"core.campaign"},
    "story.plot": {"core.plot-thread"},
    "story.scene": {"core.scene"},
    "story.entity": {"core.entity"},
}


def make_version_6_database(db_path):
    manager = DatabaseManager(str(db_path))
    conn = manager._get_connection()
    for table in (
        "state_projection_values", "state_projection_definitions", "state_idempotency",
        "legacy_extraction_quarantine", "legacy_extraction_items", "legacy_extraction_runs",
        "state_patch_log", "state_documents", "campaigns",
    ):
        conn.execute(f"DROP TABLE {table}")
    conn.execute("UPDATE schema_version SET version=6 WHERE id=1")
    conn.execute("INSERT INTO characters (name,type) VALUES ('Preserved','PC')")
    conn.commit()
    conn.close()


@pytest.fixture
def state_store(tmp_path):
    db_path = tmp_path / "campaign.db"
    manager = DatabaseManager(str(db_path))
    repository = StateRepository(
        str(db_path), manager.campaign_id, TrustedStatePolicy(TARGETS)
    )
    return manager, repository


def patch(namespace="story.world", subject_type="core.campaign", subject_id="world",
          operations=None, base_revision=None, key=None):
    return StatePatch.model_validate({
        "target": {"namespace": namespace, "subject_type": subject_type, "subject_id": subject_id},
        "base_revision": base_revision,
        "operations": operations or [{"op": "set", "path": ["value"], "value": 1}],
        "idempotency_key": key or uuid4(),
    })


def test_v7_campaign_is_stable_and_configuration_mismatch_is_rejected(tmp_path):
    db_path = tmp_path / "stable.db"
    first = DatabaseManager(str(db_path))
    campaign_id = first.campaign_configuration_value()
    second = DatabaseManager(str(db_path), campaign_id=campaign_id)
    assert second.campaign_id == campaign_id
    with pytest.raises(RuntimeError, match="does not match"):
        DatabaseManager(str(db_path), campaign_id=str(uuid4()))
    conn = second._get_connection()
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 8
    assert conn.execute(
        "SELECT COUNT(*) FROM campaigns WHERE lifecycle_status != 'deleted'"
    ).fetchone()[0] == 1
    conn.close()


def test_v6_additive_migration_backs_up_and_preserves_data(tmp_path):
    db_path = tmp_path / "v6.db"
    make_version_6_database(db_path)
    manager = DatabaseManager(str(db_path))
    assert Path(f"{db_path}.pre-v7.bak").is_file()
    assert manager.get_character_by_name("Preserved")["type"] == "PC"
    conn = manager._get_connection()
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()


def test_v7_failure_rolls_back_tables_data_and_version(tmp_path, monkeypatch):
    db_path = tmp_path / "v7-failure.db"
    make_version_6_database(db_path)

    def fail(_stage):
        raise RuntimeError("injected v7 failure")

    monkeypatch.setattr(DatabaseManager, "_MIGRATION_FAILURE_INJECTOR", fail)
    with pytest.raises(RuntimeError, match="injected v7 failure"):
        DatabaseManager(str(db_path))
    monkeypatch.setattr(DatabaseManager, "_MIGRATION_FAILURE_INJECTOR", None)
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 6
    assert conn.execute("SELECT name FROM characters WHERE name='Preserved'").fetchone()[0] == "Preserved"
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='campaigns'"
    ).fetchone() is None
    conn.close()
    assert Path(f"{db_path}.pre-v7.bak").is_file()


def test_zero_or_multiple_live_campaigns_are_rejected(tmp_path):
    db_path = tmp_path / "invalid.db"
    manager = DatabaseManager(str(db_path))
    conn = manager._get_connection()
    conn.execute("UPDATE campaigns SET lifecycle_status='deleted',deleted_at=CURRENT_TIMESTAMP")
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="exactly one"):
        DatabaseManager(str(db_path))

    conn = sqlite3.connect(db_path)
    conn.execute("DROP INDEX idx_campaigns_one_live")
    conn.execute(
        "INSERT INTO campaigns (id,display_name) VALUES (?,?)", (str(uuid4()), "Second")
    )
    conn.execute(
        "INSERT INTO campaigns (id,display_name) VALUES (?,?)", (str(uuid4()), "Third")
    )
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="exactly one"):
        DatabaseManager(str(db_path))


def test_all_patch_operations_order_null_and_expected(state_store):
    _, repo = state_store
    first = repo.apply_patch(patch(operations=[
        {"op": "set", "path": ["nested", "value"], "value": None},
        {"op": "set", "path": ["object"], "value": {"a": 1}},
        {"op": "merge_object", "path": ["object"], "value": {"b": {"x": 2}}},
        {"op": "set", "path": ["members"], "value": []},
        {"op": "add_set_member", "path": ["members"], "member": {"b": 2, "a": 1}},
        {"op": "add_set_member", "path": ["members"], "member": {"a": 1, "b": 2}},
    ], base_revision=0))
    assert first.revision == 1
    second = repo.apply_patch(patch(operations=[
        {"op": "set", "path": ["nested", "value"], "value": "changed",
         "expected": {"value": None}},
        {"op": "remove_set_member", "path": ["members"], "member": {"a": 1, "b": 2}},
        {"op": "remove", "path": ["object", "a"]},
    ], base_revision=1))
    assert second.revision == 2
    assert repo.get_document("story.world", "core.campaign", "world").state == {
        "members": [], "nested": {"value": "changed"}, "object": {"b": {"x": 2}}
    }


def test_missing_remove_scalar_crossing_and_atomic_rollback(state_store):
    _, repo = state_store
    repo.apply_patch(patch(operations=[{"op": "set", "path": ["scalar"], "value": 1}]))
    with pytest.raises(StatePatchConflict):
        repo.apply_patch(patch(operations=[
            {"op": "set", "path": ["temporary"], "value": True},
            {"op": "remove", "path": ["missing"]},
        ]))
    with pytest.raises(StatePatchConflict):
        repo.apply_patch(patch(operations=[{"op": "set", "path": ["scalar", "child"], "value": 2}]))
    assert repo.get_document("story.world", "core.campaign", "world").state == {"scalar": 1}


def test_idempotency_replay_and_mismatch(state_store):
    manager, repo = state_store
    key = uuid4()
    request = patch(key=key)
    first = repo.apply_patch(request)
    replay = repo.apply_patch(request)
    assert replay.replayed and replay.revision == first.revision == 1
    with pytest.raises(StateIdempotencyConflict):
        repo.apply_patch(patch(key=key, operations=[{"op": "set", "path": ["value"], "value": 2}]))
    conn = manager._get_connection()
    assert conn.execute("SELECT COUNT(*) FROM state_patch_log").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM state_idempotency").fetchone()[0] == 1
    conn.close()


def test_content_hash_and_deleted_documents_are_rejected(state_store):
    manager, repo = state_store
    result = repo.apply_patch(patch())
    conn = manager._get_connection()
    conn.execute("UPDATE state_documents SET content_hash='tampered' WHERE id=?", (result.document_id,))
    conn.commit()
    conn.close()
    with pytest.raises(StateIntegrityError):
        repo.apply_patch(patch())

    conn = manager._get_connection()
    conn.execute(
        "UPDATE state_documents SET content_hash=?,lifecycle_status='deleted',deleted_at=CURRENT_TIMESTAMP WHERE id=?",
        (result.content_hash, result.document_id),
    )
    conn.commit()
    conn.close()
    with pytest.raises(StatePersistenceError, match="deleted"):
        repo.apply_patch(patch())


def test_projection_updates_and_rebuilds_without_document_change(state_store):
    manager, repo = state_store
    repo.register_projection("mood", "story.entity", "core.entity", ["mood"], "text")
    result = repo.apply_patch(patch("story.entity", "core.entity", "ada", [
        {"op": "set", "path": ["mood"], "value": "curious"}
    ]))
    conn = manager._get_connection()
    row = conn.execute("SELECT * FROM state_projection_values").fetchone()
    assert row["text_value"] == "curious" and row["source_revision"] == 1
    before = conn.execute("SELECT state_json,revision FROM state_documents").fetchone()
    conn.execute("DELETE FROM state_projection_values")
    conn.commit()
    conn.close()
    repo.rebuild_projections(result.document_id)
    conn = manager._get_connection()
    assert conn.execute("SELECT text_value FROM state_projection_values").fetchone()[0] == "curious"
    assert conn.execute("SELECT state_json,revision FROM state_documents").fetchone() == before
    conn.close()


def test_projection_type_failure_rolls_back_everything(state_store):
    manager, repo = state_store
    repo.register_projection("mood", "story.entity", "core.entity", ["mood"], "integer")
    with pytest.raises(StatePersistenceError, match="definition type"):
        repo.apply_patch(patch("story.entity", "core.entity", "ada", [
            {"op": "set", "path": ["mood"], "value": "curious"}
        ]))
    conn = manager._get_connection()
    for table in ("state_documents", "state_patch_log", "state_idempotency", "state_projection_values"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    conn.close()


def test_configurable_warning_ceiling_and_shrinking_oversized(caplog, tmp_path):
    db_path = tmp_path / "bounds.db"
    manager = DatabaseManager(str(db_path))
    settings = StatePersistenceConfig()
    settings.document.warning_bytes = 40
    settings.document.safety_ceiling_bytes = 100
    repo = StateRepository(str(db_path), manager.campaign_id, TrustedStatePolicy(TARGETS), settings)
    with caplog.at_level("WARNING"):
        repo.apply_patch(patch(operations=[{"op": "set", "path": ["text"], "value": "x" * 45}]))
    assert "split by subject" in caplog.text
    with pytest.raises(StateLimitError, match="safety ceiling"):
        repo.apply_patch(patch(operations=[{"op": "set", "path": ["text"], "value": "x" * 200}]))
    assert repo.get_document("story.world", "core.campaign", "world").revision == 1

    # Lowering a limit never truncates the existing document and shrinking remains possible.
    settings.document.warning_bytes = 10
    settings.document.safety_ceiling_bytes = 30
    repo.apply_patch(patch(operations=[{"op": "set", "path": ["text"], "value": "short"}]))
    assert repo.get_path("story.world", "core.campaign", "world", ["text"]) == "short"


def test_stale_revision_and_busy_timeout_are_clear(state_store):
    manager, repo = state_store
    repo.apply_patch(patch(base_revision=0))
    with pytest.raises(StatePatchConflict, match="base revision"):
        repo.apply_patch(patch(base_revision=0))

    settings = StatePersistenceConfig()
    settings.sqlite.busy_timeout_ms = 1
    settings.sqlite.retry_count = 1
    settings.sqlite.retry_backoff_ms = 1
    busy_repo = StateRepository(manager.db_path, manager.campaign_id, TrustedStatePolicy(TARGETS), settings)
    blocker = manager._get_connection()
    blocker.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(StateBusyError, match="retry later"):
            busy_repo.apply_patch(patch(subject_id="busy"))
    finally:
        blocker.rollback()
        blocker.close()


def test_separate_connection_writers_have_no_lost_update(state_store):
    manager, repo = state_store
    repo.apply_patch(patch(operations=[
        {"op": "set", "path": ["left"], "value": 0},
        {"op": "set", "path": ["right"], "value": 0},
    ]))
    barrier = threading.Barrier(2)
    errors = []

    def writer(path_name):
        try:
            local = StateRepository(manager.db_path, manager.campaign_id, TrustedStatePolicy(TARGETS))
            barrier.wait()
            local.apply_patch(patch(operations=[{"op": "set", "path": [path_name], "value": 1}]))
        except Exception as error:  # pragma: no cover - asserted below
            errors.append(error)

    threads = [threading.Thread(target=writer, args=(name,)) for name in ("left", "right")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    document = repo.get_document("story.world", "core.campaign", "world")
    assert document.state == {"left": 1, "right": 1}
    assert document.revision == 3


@pytest.mark.parametrize("namespace,subject_type,subject_id,value", [
    ("story.world", "core.campaign", "freeform", {"floating_city": "awake"}),
    ("story.plot", "core.plot-thread", "signal", {"mystery": "unresolved"}),
    ("story.scene", "core.scene", "haunted-hall", {"echoes": ["footsteps"]}),
    ("story.entity", "core.entity", "modern-lead", {"occupation": "journalist"}),
    ("story.entity", "core.entity", "experiment-7", {"form": {"mutable": True}}),
])
def test_rules_free_genre_neutral_state(namespace, subject_type, subject_id, value, state_store):
    manager, repo = state_store
    repo.apply_patch(patch(namespace, subject_type, subject_id, [
        {"op": "set", "path": ["state"], "value": value}
    ]))
    assert repo.get_path(namespace, subject_type, subject_id, ["state"]) == value
    assert repo.advance_turn() == 1
    conn = manager._get_connection()
    assert conn.execute("SELECT rules_profile_id FROM campaigns").fetchone()[0] is None
    assert conn.execute("SELECT COUNT(*) FROM mechanical_stats").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM dnd_stats").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM combat_state").fetchone()[0] == 0
    conn.close()
