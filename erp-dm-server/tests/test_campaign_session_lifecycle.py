import sqlite3
import sys
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from campaign import (
    CampaignSessionClosedError,
    change_campaign,
    create_campaign,
    open_campaign,
)
from config import EngineConfig, settings as global_settings
from contracts.state import StatePatch
from database.state_repository import TrustedStatePolicy


POLICY = {"story.world": {"core.campaign"}}


def _patch(subject_id: str, value: str) -> StatePatch:
    return StatePatch.model_validate({
        "target": {
            "namespace": "story.world",
            "subject_type": "core.campaign",
            "subject_id": subject_id,
        },
        "operations": [{"op": "set", "path": ["value"], "value": value}],
        "idempotency_key": uuid4(),
    })


def test_session_is_active_then_close_is_idempotent_and_blocks_new_repositories(tmp_path):
    session = create_campaign(campaign_directory=tmp_path / "campaign")
    manager = session.manager
    assert session.active is True
    assert session.closed is False
    assert session.manager is not None

    session.close()
    session.close()

    assert session.active is False
    assert session.closed is True
    assert session.manager is None
    with pytest.raises(CampaignSessionClosedError, match="closed"):
        session.create_state_repository(TrustedStatePolicy(POLICY))
    with pytest.raises(CampaignSessionClosedError, match="closed"):
        manager.get_world_state()


def test_repository_created_by_session_is_invalid_after_close(tmp_path):
    session = create_campaign(campaign_directory=tmp_path / "campaign")
    repository = session.create_state_repository(TrustedStatePolicy(POLICY))
    repository.apply_patch(_patch("world", "before-close"))

    session.close()

    assert repository.closed is True
    with pytest.raises(CampaignSessionClosedError, match="closed"):
        repository.get_document("story.world", "core.campaign", "world")
    with pytest.raises(CampaignSessionClosedError, match="closed"):
        repository.apply_patch(_patch("world", "after-close"))


def test_sequential_campaign_change_isolates_settings_paths_state_and_globals(tmp_path):
    defaults_id = global_settings.db.campaign_id
    defaults_path = global_settings.db.path

    settings_a = EngineConfig()
    settings_a.db.path = "data/a.db"
    settings_a.server.port = 5101
    created_a = create_campaign(campaign_directory=tmp_path / "a", settings=settings_a)
    created_a.close()

    settings_b = EngineConfig()
    settings_b.db.path = "storage/b.db"
    settings_b.server.port = 5102
    created_b = create_campaign(campaign_directory=tmp_path / "b", settings=settings_b)
    created_b.close()

    session_a = open_campaign(configuration_path=created_a.configuration_path)
    repository_a = session_a.create_state_repository(TrustedStatePolicy(POLICY))
    repository_a.apply_patch(_patch("world-a", "campaign-a"))
    database_a_before_close = session_a.database_path.read_bytes()
    database_b_before_open = created_b.database_path.read_bytes()

    session_b = change_campaign(
        session_a, configuration_path=created_b.configuration_path
    )

    assert session_a.closed is True
    assert session_a.manager is None
    with pytest.raises(CampaignSessionClosedError):
        repository_a.apply_patch(_patch("world-a", "stale-write"))
    assert session_a.database_path.read_bytes() == database_a_before_close
    assert session_b.active is True
    assert session_b.campaign_id != session_a.campaign_id
    assert session_b.configuration_path == created_b.configuration_path
    assert session_b.database_path == created_b.database_path
    assert session_b.settings.server.port == 5102
    assert session_b.database_path.read_bytes() == database_b_before_open

    repository_b = session_b.create_state_repository(TrustedStatePolicy(POLICY))
    assert repository_b.get_document(
        "story.world", "core.campaign", "world-a"
    ) is None
    repository_b.apply_patch(_patch("world-b", "campaign-b"))

    conn_a = sqlite3.connect(created_a.database_path)
    conn_b = sqlite3.connect(created_b.database_path)
    try:
        assert conn_a.execute(
            "SELECT COUNT(*) FROM state_documents WHERE subject_id='world-a'"
        ).fetchone()[0] == 1
        assert conn_a.execute(
            "SELECT COUNT(*) FROM state_documents WHERE subject_id='world-b'"
        ).fetchone()[0] == 0
        assert conn_b.execute(
            "SELECT COUNT(*) FROM state_documents WHERE subject_id='world-a'"
        ).fetchone()[0] == 0
        assert conn_b.execute(
            "SELECT COUNT(*) FROM state_documents WHERE subject_id='world-b'"
        ).fetchone()[0] == 1
    finally:
        conn_a.close()
        conn_b.close()

    assert global_settings.db.campaign_id == defaults_id
    assert global_settings.db.path == defaults_path
    session_b.close()
