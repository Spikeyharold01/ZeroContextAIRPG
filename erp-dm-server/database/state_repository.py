"""Campaign-bound canonical JSON state persistence for new generic API writes."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError

from config import StatePersistenceConfig
from contracts.state import StatePatch, StatePatchConflict, StatePath, apply_state_patch, canonical_json


logger = logging.getLogger(__name__)
_PATH_ADAPTER = TypeAdapter(StatePath)
_MISSING = object()


class StatePersistenceError(RuntimeError):
    """Base error for persistence-boundary failures."""


class CampaignIdentityError(StatePersistenceError):
    pass


class StateIntegrityError(StatePersistenceError):
    pass


class StateAuthorizationError(StatePersistenceError):
    pass


class StateIdempotencyConflict(StatePersistenceError):
    pass


class StateLimitError(StatePersistenceError):
    pass


class StateBusyError(StatePersistenceError):
    """Retryable SQLite writer-contention error."""


class CampaignSessionClosedError(StatePersistenceError):
    """Raised when a repository outlives the campaign session that owns it."""


@dataclass(frozen=True)
class StateWriteResult:
    document_id: str
    revision: int
    content_hash: str
    replayed: bool


@dataclass(frozen=True)
class StateDocumentResult:
    document_id: str
    namespace: str
    subject_type: str
    subject_id: str
    state: dict
    revision: int
    content_hash: str


class TrustedStatePolicy:
    """Application-owned namespace/subject authorization policy.

    Namespace registration is construction-time application configuration; no
    method accepts SQL identifiers or creates database objects.
    """

    def __init__(
        self,
        allowed_targets: dict[str, set[str]],
        subject_authorizer: Callable[[str, str, str], bool] | None = None,
    ):
        self._allowed = {key: frozenset(value) for key, value in allowed_targets.items()}
        self._authorizer = subject_authorizer or (lambda campaign, kind, subject: True)

    def authorize(self, campaign_id: str, namespace: str, subject_type: str, subject_id: str) -> None:
        if subject_type not in self._allowed.get(namespace, ()):
            raise StateAuthorizationError("namespace is not registered for this subject type")
        if not self._authorizer(campaign_id, subject_type, subject_id):
            raise StateAuthorizationError("subject is not authorized for this campaign")

    @property
    def namespaces(self) -> frozenset[str]:
        return frozenset(self._allowed)


def _hash(value: str) -> str:
    return hashlib.sha256(("zero-context-state-v1\0" + value).encode("utf-8")).hexdigest()


def _walk_bounds(value: object) -> tuple[int, int, int]:
    max_depth = keys = array_elements = 0

    def walk(node: object, depth: int) -> None:
        nonlocal max_depth, keys, array_elements
        max_depth = max(max_depth, depth)
        if type(node) is dict:
            keys += len(node)
            for child in node.values():
                walk(child, depth + 1)
        elif type(node) is list:
            array_elements += len(node)
            for child in node:
                walk(child, depth + 1)

    walk(value, 0)
    return max_depth, keys, array_elements


def _value_at(document: dict, path: list[str]) -> object:
    value: object = document
    for segment in path:
        if type(value) is not dict or segment not in value:
            return _MISSING
        value = value[segment]
    return value


class StateRepository:
    """A repository permanently bound to one campaign UUID and database file."""

    def __init__(
        self,
        db_path: str,
        campaign_id: str,
        policy: TrustedStatePolicy,
        settings: StatePersistenceConfig | None = None,
        lifecycle_check: Callable[[], None] | None = None,
    ):
        UUID(campaign_id)
        self.db_path = str(Path(db_path))
        self.campaign_id = campaign_id
        self.policy = policy
        self.settings = settings or StatePersistenceConfig()
        self._lifecycle_check = lifecycle_check
        self._closed = False
        if self.settings.document.safety_ceiling_bytes <= self.settings.document.warning_bytes:
            raise ValueError("document safety ceiling must exceed warning threshold")
        self.json1_available = self._detect_json1()
        self._validate_campaign()

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        """Invalidate this repository; connections are already operation-scoped."""
        self._closed = True
        self._lifecycle_check = None

    def _ensure_open(self) -> None:
        if self._closed:
            raise CampaignSessionClosedError("campaign session repository is closed")
        if self._lifecycle_check is not None:
            self._lifecycle_check()

    def _connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.db_path,
            timeout=self.settings.sqlite.busy_timeout_ms / 1000,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {int(self.settings.sqlite.busy_timeout_ms)}")
        return conn

    def _detect_json1(self) -> bool:
        conn = sqlite3.connect(self.db_path)
        try:
            try:
                return conn.execute("SELECT json_valid('{}')").fetchone()[0] == 1
            except sqlite3.OperationalError:
                return False
        finally:
            conn.close()

    def _validate_campaign(self) -> None:
        conn = self._connection()
        try:
            rows = conn.execute(
                "SELECT id FROM campaigns WHERE lifecycle_status != 'deleted'"
            ).fetchall()
        finally:
            conn.close()
        if len(rows) != 1:
            raise CampaignIdentityError("database must contain exactly one non-deleted campaign")
        if rows[0]["id"] != self.campaign_id:
            raise CampaignIdentityError("repository campaign ID does not match database")

    def _validate_patch_limits(self, patch: StatePatch) -> str:
        encoded = canonical_json(patch.model_dump(mode="json"))
        limits = self.settings.patch
        if len(encoded.encode("utf-8")) > limits.max_bytes:
            raise StateLimitError("patch exceeds configured byte limit")
        if len(patch.operations) > limits.max_operations:
            raise StateLimitError("patch exceeds configured operation limit")
        for operation in patch.operations:
            for field in ("value", "member"):
                if hasattr(operation, field):
                    value = getattr(operation, field)
                    depth, keys, elements = _walk_bounds(value)
                    if depth > limits.max_value_depth or keys > limits.max_total_keys:
                        raise StateLimitError("patch value exceeds configured structural limits")
                    if elements > limits.max_array_elements_per_operation:
                        raise StateLimitError("patch operation exceeds configured array limit")
            if any(len(segment) > limits.max_key_length for segment in operation.path):
                raise StateLimitError("patch path exceeds configured key length")
        return encoded

    def _validate_document(self, document: dict, prior_bytes: int) -> tuple[str, str]:
        if type(document) is not dict:
            raise StateLimitError("state document root must be an object")
        # Reuse the JSON-native/reserved-key checks but apply document-specific
        # configurable structural bounds below.
        try:
            canonical = canonical_json(document)
        except (TypeError, ValueError) as error:
            raise StateLimitError("state document is not valid JSON") from error
        depth, keys, elements = _walk_bounds(document)
        limits = self.settings.document
        size = len(canonical.encode("utf-8"))
        if depth > limits.max_depth or keys > limits.max_total_keys or elements > limits.max_array_elements:
            if size >= prior_bytes:
                raise StateLimitError("state document exceeds configured structural ceiling")
        if size > limits.safety_ceiling_bytes and size >= prior_bytes:
            raise StateLimitError("state document exceeds configured safety ceiling")
        warning_point = min(limits.warning_bytes, int(limits.safety_ceiling_bytes * limits.warning_fraction))
        if size >= warning_point:
            logger.warning(
                "state document %s/%s bytes approaches its configured threshold; split by subject",
                size,
                limits.safety_ceiling_bytes,
            )
        return canonical, _hash(canonical)

    def apply_patch(
        self,
        patch: StatePatch,
        *,
        request_id: str | None = None,
        producer_type: str = "core.persistence",
        producer_id: str | None = None,
        turn_number: int | None = None,
    ) -> StateWriteResult:
        self._ensure_open()
        if not isinstance(patch, StatePatch):
            try:
                patch = StatePatch.model_validate(patch)
            except ValidationError as error:
                raise StatePersistenceError("invalid StatePatch") from error
        target = patch.target
        self.policy.authorize(
            self.campaign_id, target.namespace, target.subject_type, target.subject_id
        )
        patch_json = self._validate_patch_limits(patch)
        request_hash = _hash(patch_json)
        target_fingerprint = _hash(canonical_json(target.model_dump(mode="json")))
        started = time.monotonic()

        conn = self._connection()
        try:
            self._begin_with_retry(conn)
            replay = conn.execute(
                "SELECT target_fingerprint, request_hash, response_json "
                "FROM state_idempotency WHERE campaign_id=? AND idempotency_key=?",
                (self.campaign_id, str(patch.idempotency_key)),
            ).fetchone()
            if replay is not None:
                if replay["target_fingerprint"] != target_fingerprint or replay["request_hash"] != request_hash:
                    raise StateIdempotencyConflict("idempotency key was used for a different request")
                response = json.loads(replay["response_json"])
                conn.commit()
                return StateWriteResult(**response, replayed=True)

            row = conn.execute(
                "SELECT * FROM state_documents WHERE campaign_id=? AND namespace=? "
                "AND subject_type=? AND subject_id=?",
                (self.campaign_id, target.namespace, target.subject_type, target.subject_id),
            ).fetchone()
            if row is None:
                document, revision, document_id = {}, 0, str(uuid4())
                prior_json = "{}"
                prior_hash = _hash(prior_json)
            else:
                if row["lifecycle_status"] != "active":
                    raise StatePersistenceError("deleted state documents cannot be patched")
                try:
                    document = json.loads(row["state_json"])
                except (TypeError, ValueError) as error:
                    raise StateIntegrityError("stored state JSON is malformed") from error
                if type(document) is not dict:
                    raise StateIntegrityError("stored state root is not an object")
                prior_json = canonical_json(document)
                prior_hash = _hash(prior_json)
                if row["state_json"] != prior_json or row["content_hash"] != prior_hash:
                    raise StateIntegrityError("stored state content hash or canonical JSON does not match")
                revision, document_id = row["revision"], row["id"]

            result, next_revision = apply_state_patch(document, revision, patch)
            state_json, result_hash = self._validate_document(result, len(prior_json.encode("utf-8")))
            budget = self.settings.patch.max_apply_milliseconds
            if budget is not None and (time.monotonic() - started) * 1000 > budget:
                raise StateLimitError("patch exceeded configured cooperative apply-time budget")

            if row is None:
                conn.execute(
                    "INSERT INTO state_documents "
                    "(id,campaign_id,namespace,subject_type,subject_id,state_json,revision,content_hash,metadata_json) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (document_id, self.campaign_id, target.namespace, target.subject_type,
                     target.subject_id, state_json, next_revision, result_hash, "{}"),
                )
            else:
                changed = conn.execute(
                    "UPDATE state_documents SET state_json=?,revision=?,content_hash=?,updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=? AND campaign_id=? AND revision=? AND lifecycle_status='active'",
                    (state_json, next_revision, result_hash, document_id, self.campaign_id, revision),
                ).rowcount
                if changed != 1:
                    raise StatePatchConflict("document revision changed concurrently")

            conn.execute(
                "INSERT INTO state_patch_log "
                "(id,campaign_id,state_document_id,idempotency_key,request_id,producer_type,producer_id,"
                "turn_number,base_revision,prior_revision,resulting_revision,patch_json,patch_hash,"
                "prior_content_hash,result_content_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (str(uuid4()), self.campaign_id, document_id, str(patch.idempotency_key), request_id,
                 producer_type, producer_id, turn_number, patch.base_revision, revision, next_revision,
                 patch_json, request_hash, prior_hash, result_hash),
            )
            response = {
                "document_id": document_id,
                "revision": next_revision,
                "content_hash": result_hash,
            }
            conn.execute(
                "INSERT INTO state_idempotency "
                "(campaign_id,idempotency_key,target_fingerprint,request_hash,state_document_id,"
                "resulting_revision,response_json) VALUES (?,?,?,?,?,?,?)",
                (self.campaign_id, str(patch.idempotency_key), target_fingerprint, request_hash,
                 document_id, next_revision, canonical_json(response)),
            )
            self._update_projections(conn, document_id, target.namespace, target.subject_type,
                                     result, next_revision)
            conn.commit()
            return StateWriteResult(**response, replayed=False)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _begin_with_retry(self, conn: sqlite3.Connection) -> None:
        attempts = self.settings.sqlite.retry_count + 1
        for attempt in range(attempts):
            try:
                conn.execute("BEGIN IMMEDIATE")
                return
            except sqlite3.OperationalError as error:
                if "locked" not in str(error).lower() or attempt == attempts - 1:
                    if "locked" in str(error).lower():
                        raise StateBusyError("campaign database is busy; retry later") from error
                    raise
                time.sleep(self.settings.sqlite.retry_backoff_ms / 1000)

    def get_document(
        self, namespace: str, subject_type: str, subject_id: str, *, include_deleted: bool = False
    ) -> StateDocumentResult | None:
        self._ensure_open()
        self.policy.authorize(self.campaign_id, namespace, subject_type, subject_id)
        conn = self._connection()
        try:
            sql = (
                "SELECT * FROM state_documents WHERE campaign_id=? AND namespace=? "
                "AND subject_type=? AND subject_id=?"
            )
            params = [self.campaign_id, namespace, subject_type, subject_id]
            if not include_deleted:
                sql += " AND lifecycle_status='active'"
            row = conn.execute(sql, params).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        state = json.loads(row["state_json"])
        if _hash(canonical_json(state)) != row["content_hash"]:
            raise StateIntegrityError("stored state content hash does not match")
        return StateDocumentResult(row["id"], row["namespace"], row["subject_type"],
                                   row["subject_id"], state, row["revision"], row["content_hash"])

    def get_path(self, namespace: str, subject_type: str, subject_id: str, path: list[str]) -> object:
        self._ensure_open()
        validated = _PATH_ADAPTER.validate_python(path)
        document = self.get_document(namespace, subject_type, subject_id)
        if document is None:
            return _MISSING
        return _value_at(document.state, validated)

    def scan(
        self, namespace: str, subject_type: str, *, limit: int = 100, after_id: str = ""
    ) -> list[StateDocumentResult]:
        self._ensure_open()
        if limit < 1 or limit > 1000:
            raise ValueError("scan limit must be between 1 and 1000")
        if namespace not in self.policy.namespaces:
            raise StateAuthorizationError("namespace is not registered")
        conn = self._connection()
        try:
            rows = conn.execute(
                "SELECT * FROM state_documents WHERE campaign_id=? AND namespace=? "
                "AND subject_type=? AND lifecycle_status='active' AND id>? ORDER BY id LIMIT ?",
                (self.campaign_id, namespace, subject_type, after_id, limit),
            ).fetchall()
        finally:
            conn.close()
        return [StateDocumentResult(row["id"], row["namespace"], row["subject_type"],
                                    row["subject_id"], json.loads(row["state_json"]),
                                    row["revision"], row["content_hash"]) for row in rows]

    def advance_turn(self, expected_turn: int | None = None) -> int:
        self._ensure_open()
        conn = self._connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                "SELECT current_turn FROM campaigns WHERE id=? AND lifecycle_status='active'",
                (self.campaign_id,),
            ).fetchone()[0]
            if expected_turn is not None and current != expected_turn:
                raise StatePatchConflict("campaign turn changed concurrently")
            conn.execute(
                "UPDATE campaigns SET current_turn=current_turn+1,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (self.campaign_id,),
            )
            conn.commit()
            return current + 1
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def register_projection(
        self, projection_id: str, namespace: str, subject_type: str,
        path: list[str], value_type: str
    ) -> None:
        """Trusted setup API; ordinary state patches cannot call or encode this operation."""
        self._ensure_open()
        if namespace not in self.policy.namespaces:
            raise StateAuthorizationError("projection namespace is not registered")
        validated = _PATH_ADAPTER.validate_python(path)
        if value_type not in {"null", "text", "integer", "real", "boolean"}:
            raise ValueError("unsupported projection value type")
        conn = self._connection()
        try:
            conn.execute(
                "INSERT INTO state_projection_definitions "
                "(id,namespace,subject_type,path_json,value_type) VALUES (?,?,?,?,?)",
                (projection_id, namespace, subject_type, canonical_json(validated), value_type),
            )
            conn.commit()
        finally:
            conn.close()

    def rebuild_projections(self, document_id: str) -> None:
        self._ensure_open()
        conn = self._connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM state_documents WHERE id=? AND campaign_id=?",
                (document_id, self.campaign_id),
            ).fetchone()
            if row is None:
                raise KeyError(document_id)
            self._update_projections(conn, document_id, row["namespace"], row["subject_type"],
                                     json.loads(row["state_json"]), row["revision"])
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _update_projections(
        self, conn: sqlite3.Connection, document_id: str, namespace: str,
        subject_type: str, document: dict, revision: int
    ) -> None:
        definitions = conn.execute(
            "SELECT * FROM state_projection_definitions WHERE namespace=? AND subject_type=? "
            "AND lifecycle_status='active'",
            (namespace, subject_type),
        ).fetchall()
        for definition in definitions:
            value = _value_at(document, json.loads(definition["path_json"]))
            conn.execute(
                "DELETE FROM state_projection_values WHERE state_document_id=? AND projection_id=?",
                (document_id, definition["id"]),
            )
            if value is _MISSING:
                continue
            actual_type = (
                "null" if value is None else "boolean" if type(value) is bool else
                "integer" if type(value) is int else "real" if type(value) is float else
                "text" if type(value) is str else "object"
            )
            if actual_type != definition["value_type"]:
                raise StatePersistenceError("projected value does not match trusted definition type")
            conn.execute(
                "INSERT INTO state_projection_values "
                "(campaign_id,state_document_id,projection_id,source_revision,value_type,"
                "text_value,integer_value,real_value,boolean_value,value_hash) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (self.campaign_id, document_id, definition["id"], revision, actual_type,
                 value if actual_type == "text" else None,
                 value if actual_type == "integer" else None,
                 value if actual_type == "real" else None,
                 int(value) if actual_type == "boolean" else None,
                 _hash(canonical_json(value))),
            )
