import os
import sqlite3
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from campaign import (
    CampaignInitializationError,
    create_campaign,
    open_campaign,
    repair_campaign,
    repair_missing_configuration,
)
from config import EngineConfig
from database.db_manager import DatabaseManager


def _make_v6_database(db_path: Path):
    manager = DatabaseManager(str(db_path))
    conn = manager._get_connection()
    for table in (
        "state_projection_values", "state_projection_definitions", "state_idempotency",
        "state_patch_log", "state_documents", "campaigns",
    ):
        conn.execute(f"DROP TABLE {table}")
    conn.execute("UPDATE schema_version SET version=6 WHERE id=1")
    conn.execute("INSERT INTO characters (name,type) VALUES ('Legacy','PC')")
    conn.commit()
    conn.close()


def _write_config(config_path: Path, db_path="data/campaign.db", campaign_id=None):
    config = EngineConfig()
    config.db.path = db_path
    config.db.campaign_id = campaign_id
    config.db.archive_path = "history/archives"
    config.save(config_path)
    return config


def test_new_campaign_uses_one_uuid_and_campaign_relative_paths(tmp_path, monkeypatch):
    campaign_dir = tmp_path / "selected" / "campaign"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    session = create_campaign(campaign_directory=campaign_dir)

    assert UUID(session.campaign_id)
    assert session.configuration_path == campaign_dir / "engine.toml"
    assert session.database_path == campaign_dir / "data/game.db"
    assert session.archive_path == campaign_dir / "archives"
    reloaded = EngineConfig.load(session.configuration_path, required=True, apply_environment=False)
    assert reloaded.db.campaign_id == session.campaign_id
    conn = sqlite3.connect(session.database_path)
    assert conn.execute("SELECT id FROM campaigns").fetchone()[0] == session.campaign_id
    conn.close()


def test_open_v6_without_id_synchronizes_config_and_keeps_backup(tmp_path):
    config_path = tmp_path / "legacy" / "engine.toml"
    _write_config(config_path)
    db_path = config_path.parent / "data/campaign.db"
    _make_v6_database(db_path)

    session = open_campaign(configuration_path=config_path)

    saved = EngineConfig.load(config_path, required=True, apply_environment=False)
    assert saved.db.campaign_id == session.manager.campaign_id
    assert Path(f"{db_path}.pre-v7.bak").is_file()
    reopened = open_campaign(configuration_path=config_path)
    assert reopened.campaign_id == session.campaign_id


def test_v6_existing_config_id_is_used_by_migration(tmp_path):
    config_path = tmp_path / "legacy-id" / "engine.toml"
    expected_id = str(uuid4())
    _write_config(config_path, campaign_id=expected_id)
    db_path = config_path.parent / "data/campaign.db"
    _make_v6_database(db_path)

    session = open_campaign(configuration_path=config_path)

    assert session.campaign_id == expected_id
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT id FROM campaigns").fetchone()[0] == expected_id
    conn.close()


def test_v7_missing_id_requires_explicit_repair(tmp_path):
    session = create_campaign(campaign_directory=tmp_path / "repair")
    config = EngineConfig.load(session.configuration_path, required=True, apply_environment=False)
    config.db.campaign_id = None
    config.save(session.configuration_path)

    with pytest.raises(CampaignInitializationError) as captured:
        open_campaign(configuration_path=session.configuration_path)
    assert "missing its campaign ID" in captured.value.reason
    assert "repair_campaign" in str(captured.value)

    repaired = repair_campaign(configuration_path=session.configuration_path)
    assert repaired.campaign_id == session.campaign_id


def test_mismatch_fails_before_database_mutation_with_structured_paths(tmp_path):
    session = create_campaign(campaign_directory=tmp_path / "mismatch")
    config = EngineConfig.load(session.configuration_path, required=True, apply_environment=False)
    wrong_id = str(uuid4())
    config.db.campaign_id = wrong_id
    config.save(session.configuration_path)
    before = session.database_path.read_bytes()

    with pytest.raises(CampaignInitializationError) as captured:
        open_campaign(configuration_path=session.configuration_path)

    error = captured.value
    assert error.details.configuration_path == str(session.configuration_path)
    assert error.details.database_path == str(session.database_path)
    assert error.details.configured_campaign_id == wrong_id
    assert error.details.database_campaign_id == session.campaign_id
    assert error.details.schema_version == 7
    assert error.details.migration_occurred is False
    assert "belong together" in str(error)
    assert session.database_path.read_bytes() == before


def test_missing_configuration_never_opens_or_creates_default_database(tmp_path):
    campaign_dir = tmp_path / "missing"
    with pytest.raises(CampaignInitializationError) as captured:
        open_campaign(campaign_directory=campaign_dir)
    assert "configuration is missing" in captured.value.reason
    assert not (campaign_dir / "data/game.db").exists()

    created = create_campaign(campaign_directory=campaign_dir)
    assert created.database_path.is_file()


def test_copied_campaign_preserves_identity_and_wrong_pair_fails(tmp_path):
    import shutil

    original = create_campaign(campaign_directory=tmp_path / "original")
    copied_dir = tmp_path / "copied"
    shutil.copytree(original.configuration_path.parent, copied_dir)
    copied = open_campaign(campaign_directory=copied_dir)
    assert copied.campaign_id == original.campaign_id

    other = create_campaign(campaign_directory=tmp_path / "other")
    wrong_config = EngineConfig.load(copied.configuration_path, required=True, apply_environment=False)
    wrong_config.db.path = os.path.relpath(other.database_path, copied.configuration_path.parent)
    wrong_config.save(copied.configuration_path)
    with pytest.raises(CampaignInitializationError, match="do not match"):
        open_campaign(configuration_path=copied.configuration_path)


def test_config_none_is_omitted_and_successful_save_round_trips(tmp_path):
    config_path = tmp_path / "config" / "engine.toml"
    config = EngineConfig()
    assert config.db.campaign_id is None
    config.save(config_path)
    assert "campaign_id" not in config_path.read_text(encoding="utf-8")
    assert EngineConfig.load(
        config_path, required=True, apply_environment=False
    ).db.campaign_id is None


def test_atomic_save_failure_propagates_and_preserves_previous_file(tmp_path, monkeypatch):
    config_path = tmp_path / "atomic" / "engine.toml"
    config = _write_config(config_path)
    before = config_path.read_bytes()
    config.db.path = "changed.db"

    def fail_replace(_source, _destination):
        raise OSError("injected replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(RuntimeError, match="injected replace failure"):
        config.save(config_path)
    assert config_path.read_bytes() == before
    assert not list(config_path.parent.glob("*.tmp"))


def test_failed_post_migration_config_sync_is_actionable_and_id_is_stable(
    tmp_path, monkeypatch
):
    config_path = tmp_path / "sync-failure" / "engine.toml"
    _write_config(config_path)
    db_path = config_path.parent / "data/campaign.db"
    _make_v6_database(db_path)
    original_save = EngineConfig.save

    def fail_save(_self, _path=None):
        raise RuntimeError("injected configuration failure")

    monkeypatch.setattr(EngineConfig, "save", fail_save)
    with pytest.raises(CampaignInitializationError) as captured:
        open_campaign(configuration_path=config_path)
    error = captured.value
    assert error.details.migration_occurred is True
    assert error.details.database_campaign_id is not None
    assert error.details.verified_backup_path == f"{db_path}.pre-v7.bak"
    assert "not corrupt" in str(error)
    persisted_id = error.details.database_campaign_id

    with pytest.raises(CampaignInitializationError) as retry:
        open_campaign(configuration_path=config_path)
    assert retry.value.details.database_campaign_id == persisted_id

    monkeypatch.setattr(EngineConfig, "save", original_save)
    repaired = repair_campaign(configuration_path=config_path)
    assert repaired.campaign_id == persisted_id


def test_production_database_manager_construction_is_campaign_service_owned():
    root = Path(__file__).parents[1]
    offenders = []
    for path in root.rglob("*.py"):
        if "test" in path.name or path.name == "db_manager.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "DatabaseManager(" in text and path.name != "campaign.py":
            offenders.append(str(path.relative_to(root)))
    assert offenders == []


def test_missing_config_repair_recreates_rules_off_package_without_database_changes(
    tmp_path, monkeypatch
):
    original = create_campaign(campaign_directory=tmp_path / "original")
    original.configuration_path.unlink()
    replacement = tmp_path / "renamed" / "engine.toml"
    before = original.database_path.read_bytes()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    repaired = repair_missing_configuration(
        database_path=original.database_path,
        configuration_path=replacement,
    )

    saved = EngineConfig.load(replacement, required=True, apply_environment=False)
    assert saved.db.campaign_id == original.campaign_id == repaired.campaign_id
    assert repaired.database_path == original.database_path
    assert saved.rules_engine.enabled is False
    assert saved.rules_engine.engine_type == "off"
    assert original.database_path.read_bytes() == before
    conn = sqlite3.connect(original.database_path)
    assert conn.execute("SELECT COUNT(*) FROM mechanical_stats").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM dnd_stats").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM combat_state").fetchone()[0] == 0
    conn.close()
    assert open_campaign(configuration_path=replacement).campaign_id == original.campaign_id
    assert not (elsewhere / "data/game.db").exists()


def test_missing_config_repair_rejects_missing_and_invalid_database(tmp_path):
    destination = tmp_path / "repair" / "engine.toml"
    missing = tmp_path / "missing.db"
    with pytest.raises(CampaignInitializationError) as absent:
        repair_missing_configuration(database_path=missing, configuration_path=destination)
    assert absent.value.details.database_path == str(missing)
    assert not missing.exists()
    assert not destination.exists()

    invalid = tmp_path / "invalid.db"
    invalid.write_text("not sqlite", encoding="utf-8")
    with pytest.raises(CampaignInitializationError, match="inspection failed") as malformed:
        repair_missing_configuration(database_path=invalid, configuration_path=destination)
    assert isinstance(malformed.value.__cause__, sqlite3.DatabaseError)
    assert not destination.exists()


def test_missing_config_repair_rejects_version_6_and_directs_to_open(tmp_path):
    db_path = tmp_path / "legacy.db"
    _make_v6_database(db_path)
    before = db_path.read_bytes()
    with pytest.raises(CampaignInitializationError) as captured:
        repair_missing_configuration(
            database_path=db_path,
            configuration_path=tmp_path / "legacy" / "engine.toml",
        )
    assert "version-7" in captured.value.reason
    assert "open_campaign" in str(captured.value)
    assert db_path.read_bytes() == before


@pytest.mark.parametrize("live_rows", [0, 2])
def test_missing_config_repair_rejects_invalid_campaign_row_count(tmp_path, live_rows):
    session = create_campaign(campaign_directory=tmp_path / f"rows-{live_rows}")
    session.configuration_path.unlink()
    conn = sqlite3.connect(session.database_path)
    if live_rows == 0:
        conn.execute(
            "UPDATE campaigns SET lifecycle_status='deleted',deleted_at=CURRENT_TIMESTAMP"
        )
    else:
        conn.execute("DROP INDEX idx_campaigns_one_live")
        conn.execute(
            "INSERT INTO campaigns(id,display_name) VALUES (?,?)", (str(uuid4()), "Other")
        )
    conn.commit()
    conn.close()

    with pytest.raises(CampaignInitializationError) as captured:
        repair_missing_configuration(
            database_path=session.database_path,
            configuration_path=tmp_path / f"replacement-{live_rows}" / "engine.toml",
        )
    assert "exactly one" in captured.value.reason
    assert captured.value.details.database_path == str(session.database_path)


def test_missing_config_repair_rejects_invalid_database_campaign_uuid(tmp_path):
    session = create_campaign(campaign_directory=tmp_path / "invalid-id")
    session.configuration_path.unlink()
    conn = sqlite3.connect(session.database_path)
    conn.execute("UPDATE campaigns SET id='not-a-uuid'")
    conn.commit()
    conn.close()
    with pytest.raises(CampaignInitializationError, match="not a valid UUID"):
        repair_missing_configuration(
            database_path=session.database_path,
            configuration_path=tmp_path / "replacement-id" / "engine.toml",
        )


def test_missing_config_repair_refuses_existing_conflicting_configuration(tmp_path):
    session = create_campaign(campaign_directory=tmp_path / "database-owner")
    session.configuration_path.unlink()
    destination = tmp_path / "occupied" / "engine.toml"
    _write_config(destination, campaign_id=str(uuid4()))
    before = destination.read_bytes()
    with pytest.raises(CampaignInitializationError) as captured:
        repair_missing_configuration(
            database_path=session.database_path,
            configuration_path=destination,
        )
    assert "already exists" in captured.value.reason
    assert captured.value.details.configured_campaign_id is not None
    assert destination.read_bytes() == before


def test_missing_config_repair_save_failure_preserves_database(tmp_path, monkeypatch):
    session = create_campaign(campaign_directory=tmp_path / "save-failure")
    session.configuration_path.unlink()
    destination = tmp_path / "replacement-failure" / "engine.toml"
    before = session.database_path.read_bytes()

    def fail_save(_self, _path=None):
        raise RuntimeError("injected repair save failure")

    monkeypatch.setattr(EngineConfig, "save", fail_save)
    with pytest.raises(CampaignInitializationError) as captured:
        repair_missing_configuration(
            database_path=session.database_path,
            configuration_path=destination,
        )
    assert "could not be saved" in captured.value.reason
    assert captured.value.details.database_campaign_id == session.campaign_id
    assert captured.value.details.integrity_status == "ok"
    assert session.database_path.read_bytes() == before
    assert not destination.exists()
