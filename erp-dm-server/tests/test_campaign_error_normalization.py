import sqlite3
import sys
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

import campaign as campaign_module
from campaign import (
    CampaignInitializationError,
    create_campaign,
    open_campaign,
    repair_campaign,
    repair_missing_configuration,
)
from config import EngineConfig


def _config(path: Path, database: str, campaign_id=None):
    settings = EngineConfig()
    settings.db.path = database
    settings.db.campaign_id = campaign_id
    settings.save(path)


def test_create_normalizes_existing_database_inspection_failure(tmp_path):
    database = tmp_path / "broken.db"
    database.write_text("not sqlite", encoding="utf-8")
    settings = EngineConfig()
    settings.db.path = "broken.db"

    with pytest.raises(CampaignInitializationError) as captured:
        create_campaign(campaign_directory=tmp_path, settings=settings)

    error = captured.value
    assert error.reason == "campaign database inspection failed"
    assert error.details.database_path == str(database)
    assert error.details.integrity_status == "inspection failed"
    assert isinstance(error.__cause__, sqlite3.DatabaseError)


def test_open_normalizes_unreadable_configuration(tmp_path):
    config_path = tmp_path / "engine.toml"
    config_path.write_text("[invalid", encoding="utf-8")

    with pytest.raises(CampaignInitializationError) as captured:
        open_campaign(configuration_path=config_path)

    assert captured.value.reason == "campaign configuration could not be loaded"
    assert captured.value.details.configuration_path == str(config_path)
    assert captured.value.__cause__ is not None


def test_repair_normalizes_missing_configuration_and_database_inspection(tmp_path):
    missing_config = tmp_path / "missing.toml"
    with pytest.raises(CampaignInitializationError) as missing:
        repair_campaign(configuration_path=missing_config)
    assert missing.value.reason == "repair requires an existing campaign configuration"

    broken_database = tmp_path / "broken.db"
    broken_database.write_text("not sqlite", encoding="utf-8")
    config_path = tmp_path / "engine.toml"
    _config(config_path, "broken.db")
    with pytest.raises(CampaignInitializationError) as broken:
        repair_campaign(configuration_path=config_path)
    assert broken.value.reason == "campaign database inspection failed"
    assert isinstance(broken.value.__cause__, sqlite3.DatabaseError)


def test_missing_config_repair_normalizes_integrity_failure(tmp_path, monkeypatch):
    database = tmp_path / "campaign.db"
    database.touch()
    identity = campaign_module._DatabaseIdentity(
        True, 8, str(uuid4()), live_campaign_count=1,
        integrity_status="integrity_check=failed; foreign_key_failures=0",
    )
    monkeypatch.setattr(campaign_module, "_inspect_database", lambda *_args, **_kwargs: identity)

    with pytest.raises(CampaignInitializationError) as captured:
        repair_missing_configuration(
            database_path=database,
            configuration_path=tmp_path / "replacement" / "engine.toml",
        )

    error = captured.value
    assert error.reason == "campaign database failed integrity validation"
    assert error.details.integrity_status.startswith("integrity_check=failed")
    assert error.details.database_campaign_id == identity.campaign_id


def test_open_identity_failures_are_structured(tmp_path):
    session = create_campaign(campaign_directory=tmp_path / "campaign")
    config = EngineConfig.load(
        session.configuration_path, required=True, apply_environment=False
    )

    conn = sqlite3.connect(session.database_path)
    conn.execute("UPDATE campaigns SET id='malformed'")
    conn.commit()
    conn.close()
    with pytest.raises(CampaignInitializationError) as malformed:
        open_campaign(configuration_path=session.configuration_path)
    assert "not a valid UUID" in malformed.value.reason
    assert malformed.value.details.database_campaign_id == "malformed"

    conn = sqlite3.connect(session.database_path)
    conn.execute("UPDATE campaigns SET id=?", (str(uuid4()),))
    conn.commit()
    conn.close()
    config.db.campaign_id = session.campaign_id
    config.save(session.configuration_path)
    with pytest.raises(CampaignInitializationError) as mismatch:
        open_campaign(configuration_path=session.configuration_path)
    assert "do not match" in mismatch.value.reason
    assert mismatch.value.details.configured_campaign_id == session.campaign_id


@pytest.mark.parametrize("live_rows", [0, 2])
def test_open_row_count_failures_are_structured(tmp_path, live_rows):
    session = create_campaign(campaign_directory=tmp_path / f"rows-{live_rows}")
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
        open_campaign(configuration_path=session.configuration_path)
    assert "exactly one" in captured.value.reason
    assert captured.value.details.database_path == str(session.database_path)


def test_missing_database_and_invalid_schema_are_structured(tmp_path):
    config_path = tmp_path / "missing-db" / "engine.toml"
    _config(config_path, "absent.db", str(uuid4()))
    with pytest.raises(CampaignInitializationError) as missing:
        open_campaign(configuration_path=config_path)
    assert missing.value.reason == "configured campaign database is missing"

    invalid_schema = tmp_path / "old.db"
    conn = sqlite3.connect(invalid_schema)
    conn.execute("CREATE TABLE schema_version(id INTEGER PRIMARY KEY, version INTEGER)")
    conn.execute("INSERT INTO schema_version VALUES(1, 6)")
    conn.commit()
    conn.close()
    with pytest.raises(CampaignInitializationError) as schema:
        repair_missing_configuration(
            database_path=invalid_schema,
            configuration_path=tmp_path / "old" / "engine.toml",
        )
    assert "version-8" in schema.value.reason
