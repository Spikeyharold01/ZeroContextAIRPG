"""Trusted one-directory/one-configuration/one-database campaign lifecycle."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

from config import EngineConfig
from database.db_manager import DatabaseManager
from database.state_repository import (
    CampaignSessionClosedError,
    StateRepository,
    TrustedStatePolicy,
)


@dataclass(frozen=True)
class CampaignIdentityDetails:
    configuration_path: str
    database_path: str
    configured_campaign_id: str | None
    database_campaign_id: str | None
    schema_version: int | None
    migration_occurred: bool
    verified_backup_path: str | None
    recommended_actions: tuple[str, ...]
    integrity_status: str | None = None


class CampaignInitializationError(RuntimeError):
    """Safe, actionable failure while opening a selected campaign package."""

    def __init__(self, reason: str, details: CampaignIdentityDetails):
        self.reason = reason
        self.details = details
        actions = " ".join(details.recommended_actions)
        super().__init__(
            f"{reason}. Configuration: {details.configuration_path}. "
            f"Database: {details.database_path}. Configured campaign ID: "
            f"{details.configured_campaign_id or '<missing>'}. Database campaign ID: "
            f"{details.database_campaign_id or '<missing>'}. Schema version: "
            f"{details.schema_version if details.schema_version is not None else '<unknown>'}. "
            f"Migration occurred: {details.migration_occurred}. Verified backup: "
            f"{details.verified_backup_path or '<none>'}. Integrity: "
            f"{details.integrity_status or '<not checked>'}. Recovery: {actions}"
        )


@dataclass
class _CampaignLifecycle:
    active: bool = True

    def ensure_active(self) -> None:
        if not self.active:
            raise CampaignSessionClosedError("campaign session is closed")


@dataclass
class CampaignSession:
    configuration_path: Path
    database_path: Path
    archive_path: Path
    settings: EngineConfig
    manager: DatabaseManager | None
    campaign_id: str
    _lifecycle: _CampaignLifecycle = field(default_factory=_CampaignLifecycle, repr=False)
    _repositories: set[StateRepository] = field(default_factory=set, repr=False)

    @property
    def active(self) -> bool:
        return self._lifecycle.active

    @property
    def closed(self) -> bool:
        return not self.active

    def ensure_active(self) -> None:
        self._lifecycle.ensure_active()

    def create_state_repository(
        self, policy: TrustedStatePolicy, *, persistence_settings=None
    ) -> StateRepository:
        """Create a repository invalidated when this campaign session closes."""
        self.ensure_active()
        repository = StateRepository(
            str(self.database_path), self.campaign_id, policy,
            persistence_settings or self.settings.state_persistence,
            lifecycle_check=self._lifecycle.ensure_active,
        )
        self._repositories.add(repository)
        return repository

    def close(self) -> None:
        """Idempotently invalidate derived repositories and release owned references."""
        if self.closed:
            return
        self._lifecycle.active = False
        for repository in tuple(self._repositories):
            repository.close()
        self._repositories.clear()
        # DatabaseManager owns no persistent connection, but dropping the
        # reference prevents state access through a closed session.
        self.manager = None


@dataclass(frozen=True)
class _DatabaseIdentity:
    exists: bool
    schema_version: int | None
    campaign_id: str | None
    live_campaign_count: int | None = None
    integrity_status: str | None = None


def _selected_configuration(
    *, campaign_directory: str | Path | None = None,
    configuration_path: str | Path | None = None,
    campaign_package: str | Path | None = None,
) -> Path:
    selectors = [value for value in (campaign_directory, configuration_path, campaign_package)
                 if value is not None]
    if len(selectors) != 1:
        raise ValueError("select exactly one campaign directory, configuration path, or package")
    if campaign_directory is not None:
        return Path(campaign_directory).resolve() / "engine.toml"
    if configuration_path is not None:
        return Path(configuration_path).resolve()
    selected = Path(campaign_package).resolve()
    return selected if selected.suffix.lower() == ".toml" else selected / "engine.toml"


def _relative_to_configuration(config_path: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (config_path.parent / path).resolve()


def _inspect_database(db_path: Path, *, verify_integrity: bool = False) -> _DatabaseIdentity:
    if not db_path.is_file():
        return _DatabaseIdentity(False, None, None)
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        version = None
        if "schema_version" in tables:
            row = conn.execute("SELECT version FROM schema_version WHERE id=1").fetchone()
            version = row[0] if row else None
        campaign_id = None
        live_campaign_count = None
        if "campaigns" in tables:
            rows = conn.execute(
                "SELECT id FROM campaigns WHERE lifecycle_status != 'deleted'"
            ).fetchall()
            live_campaign_count = len(rows)
            if len(rows) == 1:
                campaign_id = rows[0][0]
        integrity_status = None
        if verify_integrity:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
            integrity_status = (
                "ok" if integrity == "ok" and not foreign_keys
                else f"integrity_check={integrity}; foreign_key_failures={len(foreign_keys)}"
            )
        return _DatabaseIdentity(
            True, version, campaign_id, live_campaign_count, integrity_status
        )
    finally:
        conn.close()


def _backup_path(db_path: Path) -> Path | None:
    candidates = [Path(f"{db_path}.pre-v7.bak"), Path(f"{db_path}.pre-v6.bak")]
    return next((path for path in candidates if path.is_file()), None)


def _details(
    config_path: Path, db_path: Path, configured_id: str | None,
    database: _DatabaseIdentity, *, migrated: bool = False,
    actions: tuple[str, ...]
) -> CampaignIdentityDetails:
    backup = _backup_path(db_path)
    return CampaignIdentityDetails(
        str(config_path), str(db_path), configured_id, database.campaign_id,
        database.schema_version, migrated, str(backup) if backup else None, actions,
        database.integrity_status
    )


def _inspect_campaign_database(
    config_path: Path,
    db_path: Path,
    configured_id: str | None,
    *,
    verify_integrity: bool = False,
    migrated: bool = False,
) -> _DatabaseIdentity:
    """Normalize low-level inspection failures at the campaign-service boundary."""
    try:
        return _inspect_database(db_path, verify_integrity=verify_integrity)
    except CampaignInitializationError:
        raise
    except Exception as error:
        database = _DatabaseIdentity(
            db_path.is_file(), None, None, integrity_status="inspection failed"
        )
        raise CampaignInitializationError(
            "campaign database inspection failed",
            _details(config_path, db_path, configured_id, database, migrated=migrated, actions=(
                "Verify that the selected configuration and database belong together.",
                "Restore the intended valid campaign database or a verified backup.",
                "Do not change the database identity merely to bypass this failure.",
            )),
        ) from error


def _load_campaign_configuration(config_path: Path, db_path: Path) -> EngineConfig:
    """Load an explicitly selected campaign config with structured failures."""
    try:
        return EngineConfig.load(config_path, required=True, apply_environment=False)
    except Exception as error:
        raise CampaignInitializationError(
            "campaign configuration could not be loaded",
            _details(config_path, db_path, None, _DatabaseIdentity(db_path.is_file(), None, None),
                     actions=(
                "Verify that the selected engine.toml is readable and belongs to this campaign.",
                "Restore a valid configuration or use the explicit missing-configuration repair workflow.",
            )),
        ) from error


def _validate_uuid(value: str, label: str) -> None:
    try:
        UUID(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} is not a valid UUID") from error


def create_campaign(
    *, campaign_directory: str | Path | None = None,
    configuration_path: str | Path | None = None,
    campaign_package: str | Path | None = None,
    settings: EngineConfig | None = None,
) -> CampaignSession:
    """Explicitly create and verify one new campaign package."""
    config_path = _selected_configuration(
        campaign_directory=campaign_directory, configuration_path=configuration_path,
        campaign_package=campaign_package
    )
    config = settings or EngineConfig()
    db_path = _relative_to_configuration(config_path, config.db.path)
    if config_path.exists() or db_path.exists():
        database = _inspect_campaign_database(
            config_path, db_path, config.db.campaign_id
        )
        raise CampaignInitializationError(
            "new campaign creation refuses to overwrite an existing package",
            _details(config_path, db_path, config.db.campaign_id, database, actions=(
                "Choose an empty campaign directory or explicitly open the existing campaign.",
            )),
        )
    campaign_id = str(uuid4())
    config.db.campaign_id = campaign_id
    try:
        config.save(config_path)
        manager = DatabaseManager(str(db_path), campaign_id=campaign_id)
        reloaded = EngineConfig.load(config_path, required=True, apply_environment=False)
        if reloaded.db.campaign_id != manager.campaign_id:
            raise RuntimeError("saved configuration identity does not match initialized database")
    except Exception as error:
        database = _inspect_campaign_database(config_path, db_path, campaign_id)
        raise CampaignInitializationError(
            f"new campaign package initialization failed: {error}",
            _details(config_path, db_path, campaign_id, database, actions=(
                "Do not treat this package as initialized.",
                "Keep any database and configuration for diagnosis, then retry in an empty directory.",
            )),
        ) from error
    return _session(config_path, db_path, reloaded, manager)


def open_campaign(
    *, campaign_directory: str | Path | None = None,
    configuration_path: str | Path | None = None,
    campaign_package: str | Path | None = None,
) -> CampaignSession:
    """Open or migrate an explicitly selected existing campaign package."""
    config_path = _selected_configuration(
        campaign_directory=campaign_directory, configuration_path=configuration_path,
        campaign_package=campaign_package
    )
    if not config_path.is_file():
        placeholder_db = (config_path.parent / "data/game.db").resolve()
        raise CampaignInitializationError(
            "campaign configuration is missing",
            _details(config_path, placeholder_db, None,
                     _DatabaseIdentity(placeholder_db.is_file(), None, None), actions=(
                "Verify that the selected directory is the intended campaign package.",
                "Restore engine.toml or call repair_missing_configuration with the intended database and configuration paths; no default database was opened.",
            )),
        )
    config = _load_campaign_configuration(
        config_path, (config_path.parent / "data/game.db").resolve()
    )
    db_path = _relative_to_configuration(config_path, config.db.path)
    database_before = _inspect_campaign_database(
        config_path, db_path, config.db.campaign_id
    )
    if not database_before.exists:
        raise CampaignInitializationError(
            "configured campaign database is missing",
            _details(config_path, db_path, config.db.campaign_id, database_before, actions=(
                "Verify that engine.toml points to the intended campaign database.",
                "Use create_campaign only for a genuinely new campaign.",
            )),
        )
    if database_before.live_campaign_count is not None and database_before.live_campaign_count != 1:
        raise CampaignInitializationError(
            "campaign database must contain exactly one non-deleted campaign row",
            _details(config_path, db_path, config.db.campaign_id, database_before, actions=(
                "Verify that the selected configuration and database belong together.",
                "Restore a valid campaign database or backup; do not invent or replace its identity.",
            )),
        )
    if database_before.campaign_id is not None:
        try:
            _validate_uuid(database_before.campaign_id, "database campaign ID")
        except ValueError as error:
            raise CampaignInitializationError(
                str(error), _details(config_path, db_path, config.db.campaign_id,
                                     database_before, actions=(
                    "Restore the intended valid campaign database or verified backup.",
                    "Do not replace the database ID merely to silence this error.",
                ))
            ) from error
    if config.db.campaign_id is not None:
        try:
            _validate_uuid(config.db.campaign_id, "configured campaign ID")
        except ValueError as error:
            raise CampaignInitializationError(
                str(error), _details(config_path, db_path, config.db.campaign_id,
                                     database_before, actions=(
                    "Correct the selected configuration with the intended campaign UUID.",
                    "Do not change the database identity merely to silence this error.",
                ))
            ) from error
    if database_before.campaign_id is not None and config.db.campaign_id is not None:
        if database_before.campaign_id != config.db.campaign_id:
            raise CampaignInitializationError(
                "campaign configuration and database identities do not match",
                _details(config_path, db_path, config.db.campaign_id, database_before, actions=(
                    "Verify that the selected configuration and database belong together.",
                    "Select the correct package or restore its matching configuration.",
                    "Do not replace the database ID merely to silence this error.",
                )),
            )
    if database_before.schema_version == DatabaseManager.LATEST_SCHEMA_VERSION:
        if config.db.campaign_id is None:
            raise CampaignInitializationError(
                "version-9 campaign configuration is missing its campaign ID",
                _details(config_path, db_path, None, database_before, actions=(
                    "Verify that this configuration and database are the intended pair.",
                    "Run repair_campaign for this explicitly selected package to copy the database ID.",
                )),
            )
        try:
            manager = DatabaseManager(str(db_path), campaign_id=config.db.campaign_id)
        except Exception as error:
            raise CampaignInitializationError(
                "campaign database identity validation failed",
                _details(config_path, db_path, config.db.campaign_id, database_before, actions=(
                    "Verify that the selected configuration and database belong together.",
                    "Restore the matching package or verified backup; do not replace its database ID.",
                )),
            ) from error
        return _session(config_path, db_path, config, manager)

    configured_id = config.db.campaign_id
    try:
        manager = DatabaseManager(str(db_path), campaign_id=configured_id)
    except Exception as error:
        after = _inspect_campaign_database(
            config_path, db_path, configured_id,
            migrated=database_before.schema_version != DatabaseManager.LATEST_SCHEMA_VERSION,
        )
        raise CampaignInitializationError(
            f"campaign database migration failed: {error}",
            _details(config_path, db_path, configured_id, after,
                     migrated=after.schema_version != database_before.schema_version, actions=(
                "Keep the verified backup and inspect the reported database error.",
                "Do not modify either campaign identity while diagnosing migration failure.",
            )),
        ) from error
    database_after = _inspect_campaign_database(
        config_path, db_path, manager.campaign_id, migrated=True
    )
    config.db.campaign_id = manager.campaign_id
    try:
        config.save(config_path)
        reloaded = EngineConfig.load(config_path, required=True, apply_environment=False)
        if reloaded.db.campaign_id != manager.campaign_id:
            raise RuntimeError("reloaded configuration ID does not match migrated database")
    except Exception as error:
        raise CampaignInitializationError(
            f"database migrated but campaign configuration synchronization failed: {error}",
            _details(config_path, db_path, config.db.campaign_id, database_after,
                     migrated=True, actions=(
                "The database is not corrupt; keep the verified pre-migration backup.",
                f"After verifying this is the intended package, write campaign_id = {manager.campaign_id} to the selected engine.toml.",
                "Alternatively run repair_campaign for this explicitly selected package; retrying must reuse the database ID.",
            )),
        ) from error
    return _session(config_path, db_path, reloaded, manager)


def repair_campaign(
    *, campaign_directory: str | Path | None = None,
    configuration_path: str | Path | None = None,
    campaign_package: str | Path | None = None,
) -> CampaignSession:
    """Explicitly copy a current database identity into its selected config."""
    config_path = _selected_configuration(
        campaign_directory=campaign_directory, configuration_path=configuration_path,
        campaign_package=campaign_package
    )
    if not config_path.is_file():
        placeholder_db = (config_path.parent / "data/game.db").resolve()
        raise CampaignInitializationError(
            "repair requires an existing campaign configuration",
            _details(config_path, placeholder_db, None,
                     _DatabaseIdentity(placeholder_db.is_file(), None, None), actions=(
                "Restore engine.toml or use repair_missing_configuration with explicit database and destination paths.",
            )),
        )
    config = _load_campaign_configuration(
        config_path, (config_path.parent / "data/game.db").resolve()
    )
    db_path = _relative_to_configuration(config_path, config.db.path)
    database = _inspect_campaign_database(config_path, db_path, config.db.campaign_id)
    if database.schema_version != DatabaseManager.LATEST_SCHEMA_VERSION or database.campaign_id is None:
        raise CampaignInitializationError(
            "repair requires a version-9 database with one valid campaign identity",
            _details(config_path, db_path, config.db.campaign_id, database, actions=(
                "Verify that the selected configuration and database belong together.",
                "Open the legacy package normally to migrate it before repair.",
            )),
        )
    if config.db.campaign_id not in (None, database.campaign_id):
        raise CampaignInitializationError(
            "repair refuses to overwrite a conflicting configured identity",
            _details(config_path, db_path, config.db.campaign_id, database, actions=(
                "Verify that the selected configuration and database belong together.",
                "Select the matching package; do not replace the database ID.",
            )),
        )
    config.db.campaign_id = database.campaign_id
    try:
        config.save(config_path)
        return open_campaign(configuration_path=config_path)
    except CampaignInitializationError:
        raise
    except Exception as error:
        raise CampaignInitializationError(
            "campaign configuration repair failed",
            _details(config_path, db_path, config.db.campaign_id, database, actions=(
                "Correct configuration storage permissions and retry the explicit repair.",
                "Keep the database identity unchanged.",
            )),
        ) from error


def repair_missing_configuration(
    *,
    database_path: str | Path,
    campaign_directory: str | Path | None = None,
    configuration_path: str | Path | None = None,
    campaign_package: str | Path | None = None,
    base_settings: EngineConfig | None = None,
) -> CampaignSession:
    """Recreate a missing config for one explicitly selected current database."""
    config_path = _selected_configuration(
        campaign_directory=campaign_directory, configuration_path=configuration_path,
        campaign_package=campaign_package
    )
    db_path = Path(database_path).expanduser().resolve()
    if not db_path.is_file():
        raise CampaignInitializationError(
            "selected campaign database does not exist",
            _details(config_path, db_path, None, _DatabaseIdentity(False, None, None), actions=(
                "Select the intended existing SQLite campaign database explicitly.",
                "No default database was searched for, opened, or created.",
            )),
        )
    try:
        database = _inspect_campaign_database(
            config_path, db_path, None, verify_integrity=True
        )
    except CampaignInitializationError:
        raise
    if database.schema_version != DatabaseManager.LATEST_SCHEMA_VERSION:
        raise CampaignInitializationError(
            "missing-configuration repair requires a version-9 campaign database",
            _details(config_path, db_path, None, database, actions=(
                "Restore the campaign configuration and use open_campaign for migration.",
                "Do not use configuration repair to bypass the version-6 migration workflow.",
            )),
        )
    if database.live_campaign_count != 1:
        raise CampaignInitializationError(
            "campaign database must contain exactly one non-deleted campaign row",
            _details(config_path, db_path, None, database, actions=(
                "Restore a valid version-9 campaign database or verified backup.",
                "Do not invent or replace the database identity.",
            )),
        )
    try:
        _validate_uuid(database.campaign_id, "database campaign ID")
    except ValueError as error:
        raise CampaignInitializationError(
            str(error), _details(config_path, db_path, None, database, actions=(
                "Restore the intended valid campaign database or verified backup.",
                "Do not replace the database ID merely to silence this error.",
            ))
        ) from error
    if database.integrity_status != "ok":
        raise CampaignInitializationError(
            "campaign database failed integrity validation",
            _details(config_path, db_path, None, database, actions=(
                "Restore a verified backup or repair database integrity before recreating configuration.",
                "Do not write a replacement configuration for an invalid database.",
            )),
        )
    if config_path.exists():
        try:
            existing = _load_campaign_configuration(config_path, db_path)
        except Exception as error:
            raise CampaignInitializationError(
                f"replacement configuration path contains an unreadable file: {error}",
                _details(config_path, db_path, None, database, actions=(
                    "Select a missing configuration destination or restore the intended configuration.",
                    "Do not overwrite an unrelated campaign configuration.",
                )),
            ) from error
        raise CampaignInitializationError(
            "replacement configuration path already exists",
            _details(config_path, db_path, existing.db.campaign_id, database, actions=(
                "Use open_campaign when the existing configuration belongs to this database.",
                "Use repair_campaign only when that configuration is missing its ID.",
                "Select a different missing destination; do not overwrite an unrelated configuration.",
            )),
        )

    settings = base_settings or EngineConfig()
    if base_settings is None:
        settings.rules_engine.enabled = False
        settings.rules_engine.engine_type = "off"
    settings.db.campaign_id = database.campaign_id
    settings.db.path = os.path.relpath(db_path, config_path.parent)
    if Path(settings.db.archive_path).is_absolute():
        settings.db.archive_path = "archives"
    try:
        settings.save(config_path)
        reloaded = EngineConfig.load(config_path, required=True, apply_environment=False)
        if reloaded.db.campaign_id != database.campaign_id:
            raise RuntimeError("reloaded replacement configuration ID does not match database")
        session = open_campaign(configuration_path=config_path)
    except Exception as error:
        raise CampaignInitializationError(
            f"replacement campaign configuration could not be saved and verified: {error}",
            _details(config_path, db_path, settings.db.campaign_id, database, actions=(
                "The database was not changed; correct configuration storage permissions and retry.",
                "Keep the selected database ID unchanged and verify this remains the intended package.",
            )),
        ) from error
    return session


def change_campaign(
    current_session: CampaignSession,
    *,
    campaign_directory: str | Path | None = None,
    configuration_path: str | Path | None = None,
    campaign_package: str | Path | None = None,
) -> CampaignSession:
    """Sequentially close one session and explicitly open another package."""
    current_session.close()
    return open_campaign(
        campaign_directory=campaign_directory,
        configuration_path=configuration_path,
        campaign_package=campaign_package,
    )


def _session(
    config_path: Path, db_path: Path, settings: EngineConfig, manager: DatabaseManager
) -> CampaignSession:
    session = CampaignSession(
        config_path, db_path, _relative_to_configuration(config_path, settings.db.archive_path),
        settings, manager, manager.campaign_id
    )
    manager.bind_lifecycle(session.ensure_active)
    return session
