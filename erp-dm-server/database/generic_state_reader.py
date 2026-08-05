"""Read-only, campaign-bound access to v8 legacy compatibility documents."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import struct
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Any, Literal
from urllib.parse import quote
from uuid import uuid5

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator


from database.compatibility_format import (
    COMPATIBILITY_FORMAT_VERSION,
    COMPATIBILITY_NAMESPACES,
    DETERMINISTIC_ID_NAMESPACE,
    EXTRACTION_SCHEMA_VERSION,
    EXTRACTOR_REVISION,
    METADATA_OWNER,
)


class CompatibilityReadError(RuntimeError):
    """Base failure at the read-only compatibility boundary."""


class CompatibilityIntegrityError(CompatibilityReadError):
    """A stored compatibility representation failed hash verification."""


class ImmutableMapping(Mapping[str, Any]):
    """Small recursively immutable mapping used at the public read boundary."""

    __slots__ = ("_data",)

    def __init__(self, values: Mapping[str, Any]):
        object.__setattr__(self, "_data", MappingProxyType(
            {str(key): _freeze_json(value) for key, value in values.items()}
        ))

    def __setattr__(self, name: str, value: Any) -> None:
        raise TypeError("ImmutableMapping does not support mutation")

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


def _freeze_json(value: Any) -> Any:
    if isinstance(value, ImmutableMapping):
        return value
    if isinstance(value, Mapping):
        return ImmutableMapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(child) for child in value)
    return value


def _mutable_copy(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return {name: _mutable_copy(getattr(value, name)) for name in type(value).model_fields}
    if isinstance(value, Mapping):
        return {key: _mutable_copy(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_mutable_copy(child) for child in value]
    return value


FrozenJson = Annotated[Any, BeforeValidator(_freeze_json)]


class TypedSQLiteValue(BaseModel):
    """Lossless SQLite storage value plus a convenient Python view."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    storage_class: Literal["null", "integer", "real", "text", "blob"]
    decimal: str | None = None
    signed_big_endian_base64: str | None = None
    ieee754_binary64_hex: str | None = None
    base64: str | None = None
    byte_length: int | None = Field(default=None, ge=0)
    encoding: str | None = None
    decoding_status: Literal["valid", "invalid"] | None = None
    decoded: str | None = None

    @model_validator(mode="after")
    def validate_representation(self):
        required = {
            "integer": self.signed_big_endian_base64,
            "real": self.ieee754_binary64_hex,
            "text": self.base64,
            "blob": self.base64,
        }
        if self.storage_class != "null" and not required[self.storage_class]:
            raise ValueError(f"{self.storage_class} value is missing its lossless representation")
        return self

    @property
    def raw_bytes(self) -> bytes:
        if self.storage_class == "null":
            return b""
        if self.storage_class == "integer":
            return base64.b64decode(self.signed_big_endian_base64, validate=True)
        if self.storage_class == "real":
            return bytes.fromhex(self.ieee754_binary64_hex or "")
        return base64.b64decode(self.base64, validate=True)

    @property
    def value(self) -> None | int | float | str | bytes:
        """Return a convenient value without discarding the lossless envelope."""
        if self.storage_class == "null":
            return None
        if self.storage_class == "integer":
            return int.from_bytes(self.raw_bytes, "big", signed=True)
        if self.storage_class == "real":
            return struct.unpack(">d", self.raw_bytes)[0]
        if self.storage_class == "text":
            return self.raw_bytes.decode(self.encoding or "utf-8") if self.decoding_status == "valid" else self.raw_bytes
        return self.raw_bytes


class SourceColumn(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    ordinal: int
    name: str
    declared_type: str
    not_null: bool
    declared_default_sql: str | None
    primary_key_position: int
    hidden: int
    value: TypedSQLiteValue
    may_have_originated_from_default: Literal["possible", "not_possible", "unknown"]


class SourceIdentity(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    kind: Literal["primary-key", "unique-key", "weak-rowid"]
    columns: tuple[FrozenJson, ...] | None = None
    rowid: str | None = None
    table_schema_hash: str | None = None
    full_row_source_hash: str | None = None


class ExtractionDescriptor(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    schema_version: Literal[8]
    extractor_revision: str
    extracted_at: str
    source_hash: str
    parse_status: Literal["valid", "invalid", "partially_valid", "not_applicable"]
    warnings: tuple[str, ...]
    json_diagnostics: tuple[FrozenJson, ...] = ()


class SourceDescriptor(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    table: str
    identity: SourceIdentity
    columns: tuple[SourceColumn, ...]
    timestamps: tuple[SourceColumn, ...]

    def column(self, name: str) -> SourceColumn | None:
        return next((column for column in self.columns if column.name == name), None)

    @property
    def values(self) -> ImmutableMapping:
        return ImmutableMapping({column.name: column.value.value for column in self.columns})

    def mutable_values(self) -> dict[str, None | int | float | str | bytes]:
        """Return an explicitly mutable convenience copy."""
        return dict(self.values)


class CompatibilityDocument(BaseModel):
    """Strongly validated compatibility envelope and persistence identity."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    document_id: str
    campaign_id: str
    namespace: str
    subject_type: str
    subject_id: str
    revision: int = Field(ge=1)
    content_hash: str
    compatibility_format: Literal["legacy-sqlite-row.v1"]
    extraction: ExtractionDescriptor
    source: SourceDescriptor
    references: tuple[FrozenJson, ...]
    parsed_views: FrozenJson

    def mutable_copy(self) -> dict[str, Any]:
        """Return a recursively mutable copy without weakening the stored model."""
        return _mutable_copy(self)


class ExtractionRun(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    id: str
    campaign_id: str
    status: str
    parity_status: str
    report: FrozenJson | None = None


class ExtractionItem(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    id: str
    campaign_id: str
    area: str
    source_table: str
    source_hash: str
    namespace: str
    subject_type: str
    subject_id: str
    state_document_id: str | None
    document_content_hash: str | None
    status: str


class IntegrityVerification(BaseModel):
    model_config = ConfigDict(frozen=True)
    document_id: str
    findings: tuple["IntegrityFinding", ...]

    @property
    def valid(self) -> bool:
        return not self.findings

    @property
    def finding_codes(self) -> tuple[str, ...]:
        return tuple(finding.code for finding in self.findings)


class IntegrityFinding(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    code: str
    message: str
    expected: str | None = None
    actual: str | None = None


def _frame(parts: tuple[bytes, ...]) -> bytes:
    return b"".join(struct.pack(">Q", len(part)) + part for part in parts)


def _domain_hash(data: bytes) -> str:
    return hashlib.sha256(b"zero-context-legacy-v1\0" + data).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _source_hash(raw_document: dict[str, Any]) -> str:
    source = raw_document["source"]
    parts = [raw_document["compatibility_format"].encode(), source["table"].encode(),
             _canonical_json(source["identity"]).encode()]
    for column in sorted(source["columns"], key=lambda item: item["ordinal"]):
        value = TypedSQLiteValue.model_validate(column["value"])
        parts.extend((column["name"].encode(), column["declared_type"].encode(),
                      _frame((value.storage_class.encode(), value.raw_bytes))))
    return _domain_hash(_frame(tuple(parts)))


class GenericStateReader:
    """Read-only reader permanently bound to one campaign database and UUID."""

    def __init__(self, db_path: str | Path, campaign_id: str):
        self.db_path = Path(db_path).resolve()
        self.campaign_id = campaign_id
        with self._connection() as conn:
            row = conn.execute("SELECT id FROM campaigns WHERE id=? AND lifecycle_status!='deleted'", (campaign_id,)).fetchone()
            if row is None:
                raise CompatibilityReadError("campaign is not active in the selected database")

    def _connection(self) -> sqlite3.Connection:
        uri = "file:" + quote(str(self.db_path), safe="/") + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn

    def get(self, namespace: str, subject_type: str, subject_id: str, *, verify: bool = True) -> CompatibilityDocument | None:
        """Look up one document by its complete campaign-scoped identity."""
        if namespace not in COMPATIBILITY_NAMESPACES:
            raise CompatibilityReadError("namespace is not a registered compatibility namespace")
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM state_documents WHERE campaign_id=? AND namespace=? AND subject_type=? "
                               "AND subject_id=? AND lifecycle_status='active'",
                               (self.campaign_id, namespace, subject_type, subject_id)).fetchone()
            if row is None:
                return None
            if verify and not self._verify_row(conn, row).valid:
                raise CompatibilityIntegrityError(f"compatibility document failed integrity verification: {row['id']}")
            return self._document(row)

    def get_unique(self, namespace: str, subject_id: str, *, verify: bool = True) -> CompatibilityDocument | None:
        """Compatibility lookup that fails rather than guessing across subject types."""
        if namespace not in COMPATIBILITY_NAMESPACES:
            raise CompatibilityReadError("namespace is not a registered compatibility namespace")
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM state_documents WHERE campaign_id=? AND namespace=? AND subject_id=? "
                "AND lifecycle_status='active' ORDER BY subject_type,id",
                (self.campaign_id, namespace, subject_id),
            ).fetchall()
            if not rows:
                return None
            if len(rows) != 1:
                raise CompatibilityReadError("compatibility subject lookup is ambiguous; provide subject_type")
            row = rows[0]
            if verify and not self._verify_row(conn, row).valid:
                raise CompatibilityIntegrityError(f"compatibility document failed integrity verification: {row['id']}")
            return self._document(row)

    def enumerate(self, *, namespace: str | None = None, subject_id: str | None = None,
                  verify: bool = True) -> tuple[CompatibilityDocument, ...]:
        if namespace is not None and namespace not in COMPATIBILITY_NAMESPACES:
            raise CompatibilityReadError("namespace is not a registered compatibility namespace")
        sql = "SELECT * FROM state_documents WHERE campaign_id=? AND lifecycle_status='active'"
        parameters: list[Any] = [self.campaign_id]
        if namespace is None:
            sql += " AND namespace IN (%s)" % ",".join("?" for _ in COMPATIBILITY_NAMESPACES)
            parameters.extend(sorted(COMPATIBILITY_NAMESPACES))
        else:
            sql += " AND namespace=?"; parameters.append(namespace)
        if subject_id is not None:
            sql += " AND subject_id=?"; parameters.append(subject_id)
        sql += " ORDER BY namespace,subject_type,subject_id,id"
        with self._connection() as conn:
            rows = conn.execute(sql, parameters).fetchall()
            if verify:
                failures = [row["id"] for row in rows if not self._verify_row(conn, row).valid]
                if failures:
                    raise CompatibilityIntegrityError("compatibility documents failed integrity verification: " + ", ".join(failures))
            return tuple(self._document(row) for row in rows)

    def by_campaign(self, campaign_id: str) -> tuple[CompatibilityDocument, ...]:
        if campaign_id != self.campaign_id:
            raise CompatibilityReadError("reader is bound to a different campaign")
        return self.enumerate()

    def verify(self, document_id: str) -> IntegrityVerification:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM state_documents WHERE id=? AND campaign_id=?", (document_id, self.campaign_id)).fetchone()
            if row is None or row["namespace"] not in COMPATIBILITY_NAMESPACES:
                raise KeyError(document_id)
            return self._verify_row(conn, row)

    def extraction_runs(self) -> tuple[ExtractionRun, ...]:
        with self._connection() as conn:
            rows = conn.execute("SELECT * FROM legacy_extraction_runs WHERE campaign_id=? ORDER BY started_at,id", (self.campaign_id,)).fetchall()
            return tuple(ExtractionRun(**dict(row), report=_freeze_json(json.loads(row["report_json"]))
                                       if row["report_json"] else None) for row in rows)

    def extraction_items(self, *, document_id: str | None = None) -> tuple[ExtractionItem, ...]:
        sql = "SELECT * FROM legacy_extraction_items WHERE campaign_id=?"; params: list[Any] = [self.campaign_id]
        if document_id is not None: sql += " AND state_document_id=?"; params.append(document_id)
        sql += " ORDER BY area,source_table,source_identity_hash"
        with self._connection() as conn:
            return tuple(ExtractionItem.model_validate(dict(row)) for row in conn.execute(sql, params))

    def quarantine(self) -> tuple[ImmutableMapping, ...]:
        with self._connection() as conn:
            return tuple(ImmutableMapping(dict(row)) for row in conn.execute(
                "SELECT * FROM legacy_extraction_quarantine WHERE campaign_id=? ORDER BY created_at,id",
                (self.campaign_id,),
            ))

    def _document(self, row: sqlite3.Row) -> CompatibilityDocument:
        raw = json.loads(row["state_json"])
        raw["extraction"]["warnings"] = tuple(raw["extraction"]["warnings"])
        raw["extraction"]["json_diagnostics"] = tuple(
            _freeze_json(value) for value in raw["extraction"].get("json_diagnostics", ())
        )
        if raw["source"]["identity"].get("columns") is not None:
            raw["source"]["identity"]["columns"] = tuple(
                _freeze_json(value) for value in raw["source"]["identity"]["columns"]
            )
        raw["source"]["columns"] = tuple(raw["source"]["columns"])
        raw["source"]["timestamps"] = tuple(raw["source"]["timestamps"])
        raw["references"] = tuple(_freeze_json(value) for value in raw["references"])
        raw["parsed_views"] = _freeze_json(raw["parsed_views"])
        return CompatibilityDocument(document_id=row["id"], campaign_id=row["campaign_id"], namespace=row["namespace"],
            subject_type=row["subject_type"], subject_id=row["subject_id"], revision=row["revision"],
            content_hash=row["content_hash"], **raw)

    def _verify_row(self, conn: sqlite3.Connection, row: sqlite3.Row) -> IntegrityVerification:
        actual_content = hashlib.sha256(("zero-context-state-v1\0" + row["state_json"]).encode()).hexdigest()
        findings = []

        def require(code, condition, message, expected=None, actual=None):
            if not condition:
                findings.append(IntegrityFinding(code=code, message=message,
                    expected=None if expected is None else str(expected),
                    actual=None if actual is None else str(actual)))

        try:
            raw = json.loads(row["state_json"])
        except (json.JSONDecodeError, TypeError) as error:
            require("state-json-invalid", False, "state document JSON cannot be decoded", "valid JSON", type(error).__name__)
            raw = None
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
            if type(metadata) is not dict:
                raise TypeError("metadata root is not an object")
        except (json.JSONDecodeError, TypeError) as error:
            require("metadata-json-invalid", False, "document metadata cannot be decoded", "JSON object", type(error).__name__)
            metadata = {}
        try:
            actual_source = _source_hash(raw) if raw is not None else None
        except (KeyError, TypeError, ValueError) as error:
            require("source-reconstruction-failed", False, "source hash cannot be reconstructed", None, type(error).__name__)
            actual_source = None
        items = conn.execute(
            "SELECT i.*,r.campaign_id AS run_campaign_id,r.extraction_schema_version AS run_schema_version,"
            "r.extractor_revision AS run_extractor_revision,r.status AS run_status,r.parity_status AS run_parity_status "
            "FROM legacy_extraction_items i LEFT JOIN legacy_extraction_runs r ON r.id=i.last_run_id "
            "WHERE i.campaign_id=? AND i.state_document_id=?",
            (self.campaign_id, row["id"]),
        ).fetchall()
        require("content-hash-mismatch", actual_content == row["content_hash"], "document content hash differs",
                row["content_hash"], actual_content)
        raw_extraction = raw.get("extraction", {}) if type(raw) is dict else {}
        require("source-hash-mismatch", actual_source is not None and actual_source == raw_extraction.get("source_hash"),
                "document source hash differs", raw_extraction.get("source_hash"), actual_source)
        require("trusted-owner-mismatch", metadata.get("owner") == METADATA_OWNER,
                "document owner differs from trusted extractor owner", METADATA_OWNER, metadata.get("owner"))
        require("trusted-format-mismatch", (raw or {}).get("compatibility_format") == COMPATIBILITY_FORMAT_VERSION,
                "document compatibility format differs from trusted format", COMPATIBILITY_FORMAT_VERSION,
                (raw or {}).get("compatibility_format"))
        require("trusted-schema-version-mismatch", raw_extraction.get("schema_version") == EXTRACTION_SCHEMA_VERSION,
                "document schema version differs from trusted extraction schema", EXTRACTION_SCHEMA_VERSION,
                raw_extraction.get("schema_version"))
        require("trusted-extractor-revision-mismatch", raw_extraction.get("extractor_revision") == EXTRACTOR_REVISION,
                "document extractor revision differs from trusted extractor revision", EXTRACTOR_REVISION,
                raw_extraction.get("extractor_revision"))
        require("campaign-mismatch", row["campaign_id"] == self.campaign_id,
                "document campaign differs from reader", self.campaign_id, row["campaign_id"])
        if len(items) != 1:
            require("extraction-item-count", False, "document must link to exactly one extraction item", 1, len(items))
            return IntegrityVerification(document_id=row["id"], findings=tuple(findings))
        item = items[0]
        source_identity = None
        try:
            source_identity = json.loads(item["source_identity_json"])
            expected_identity_hash = _domain_hash(_canonical_json(source_identity).encode())
        except (json.JSONDecodeError, TypeError) as error:
            require("source-identity-json-invalid", False, "source identity JSON cannot be decoded", "valid JSON", type(error).__name__)
            expected_identity_hash = None
        require("source-identity-hash-mismatch", expected_identity_hash is not None and item["source_identity_hash"] == expected_identity_hash,
                "source identity hash differs from trusted canonical identity hash", item["source_identity_hash"], expected_identity_hash)
        expected_item_id = str(uuid5(DETERMINISTIC_ID_NAMESPACE,
            f"{item['campaign_id']}\0{EXTRACTION_SCHEMA_VERSION}\0{EXTRACTOR_REVISION}\0{item['area']}\0{item['source_table']}\0{item['source_identity_hash']}"))
        expected_document_id = str(uuid5(DETERMINISTIC_ID_NAMESPACE, "document\0" + expected_item_id))
        expected_subject_id = f"{item['source_table']}/{(source_identity or {}).get('kind')}/{item['source_identity_hash']}"
        require("extraction-item-id-mismatch", item["id"] == expected_item_id,
                "extraction item ID differs from trusted deterministic ID", expected_item_id, item["id"])
        require("state-document-id-mismatch", row["id"] == expected_document_id,
                "state document ID differs from trusted deterministic ID", expected_document_id, row["id"])
        require("deterministic-subject-id-mismatch", row["subject_id"] == expected_subject_id and item["subject_id"] == expected_subject_id,
                "subject ID differs from trusted deterministic source identity", expected_subject_id, f"document={row['subject_id']}; item={item['subject_id']}")
        require("item-schema-version-untrusted", item["extraction_schema_version"] == EXTRACTION_SCHEMA_VERSION,
                "extraction item schema version differs from trusted schema", EXTRACTION_SCHEMA_VERSION, item["extraction_schema_version"])
        require("item-extractor-revision-untrusted", item["extractor_revision"] == EXTRACTOR_REVISION,
                "extraction item revision differs from trusted extractor revision", EXTRACTOR_REVISION, item["extractor_revision"])
        require("owner-mismatch", metadata.get("owner") == METADATA_OWNER,
                "extractor owner differs", METADATA_OWNER, metadata.get("owner"))
        for field in ("campaign_id", "namespace", "subject_type", "subject_id"):
            require(f"{field.replace('_','-')}-mismatch", row[field] == item[field],
                    f"document {field} differs from extraction item", item[field], row[field])
        require("schema-version-mismatch", raw_extraction.get("schema_version") == item["extraction_schema_version"],
                "extraction schema version differs", item["extraction_schema_version"], raw_extraction.get("schema_version"))
        require("extractor-revision-mismatch", raw_extraction.get("extractor_revision") == item["extractor_revision"],
                "extractor revision differs", item["extractor_revision"], raw_extraction.get("extractor_revision"))
        require("tracking-source-hash-mismatch", actual_source is not None and item["source_hash"] == actual_source,
                "tracking source hash differs", item["source_hash"], actual_source)
        require("tracking-content-hash-mismatch", item["document_content_hash"] == row["content_hash"],
                "tracking content hash differs", item["document_content_hash"], row["content_hash"])
        require("run-missing", item["run_status"] is not None, "linked extraction run is missing")
        require("run-campaign-mismatch", item["run_campaign_id"] == item["campaign_id"],
                "run campaign differs", item["campaign_id"], item["run_campaign_id"])
        require("run-schema-version-mismatch", item["run_schema_version"] == item["extraction_schema_version"],
                "run schema version differs", item["extraction_schema_version"], item["run_schema_version"])
        require("run-schema-version-untrusted", item["run_schema_version"] == EXTRACTION_SCHEMA_VERSION,
                "run schema version differs from trusted schema", EXTRACTION_SCHEMA_VERSION, item["run_schema_version"])
        require("run-extractor-revision-mismatch", item["run_extractor_revision"] == item["extractor_revision"],
                "run extractor revision differs", item["extractor_revision"], item["run_extractor_revision"])
        require("run-extractor-revision-untrusted", item["run_extractor_revision"] == EXTRACTOR_REVISION,
                "run extractor revision differs from trusted extractor revision", EXTRACTOR_REVISION, item["run_extractor_revision"])
        require("run-incomplete", item["run_status"] == "complete", "linked extraction run is not complete",
                "complete", item["run_status"])
        require("run-parity-not-exact", item["run_parity_status"] == "exact", "linked run parity is not exact",
                "exact", item["run_parity_status"])
        run = conn.execute("SELECT * FROM legacy_extraction_runs WHERE id=? AND campaign_id=?",
                           (item["last_run_id"], self.campaign_id)).fetchone()
        if run is not None:
            try:
                report = json.loads(run["report_json"] or "{}")
                if type(report) is not dict:
                    raise TypeError("report root is not an object")
            except (json.JSONDecodeError, TypeError) as error:
                require("run-report-invalid", False, "run report JSON cannot be decoded", "JSON object", type(error).__name__)
                report = {}
            require("run-report-exact-false", report.get("exact") is True, "run report is not exact", True, report.get("exact"))
            require("run-report-source-root-mismatch", report.get("source_root_hash") == run["source_root_hash"],
                    "run source root hash differs from report", run["source_root_hash"], report.get("source_root_hash"))
            require("run-report-document-root-mismatch", report.get("document_root_hash") == run["document_root_hash"],
                    "run document root hash differs from report", run["document_root_hash"], report.get("document_root_hash"))
            require("run-root-parity-mismatch", run["source_root_hash"] == run["document_root_hash"],
                    "exact parity run has mismatched roots", run["source_root_hash"], run["document_root_hash"])
            document_count = conn.execute("SELECT count(*) FROM legacy_extraction_items WHERE campaign_id=? AND last_run_id=? AND state_document_id IS NOT NULL",
                                          (self.campaign_id, item["last_run_id"])).fetchone()[0]
            require("run-document-count-mismatch", run["document_count"] == document_count,
                    "run document count differs from extraction items", run["document_count"], document_count)
            source_count = conn.execute("SELECT count(*) FROM legacy_extraction_items WHERE campaign_id=? AND last_run_id=?",
                                        (self.campaign_id, item["last_run_id"])).fetchone()[0]
            quarantine_count = conn.execute("SELECT count(*) FROM legacy_extraction_quarantine WHERE campaign_id=? AND run_id=?",
                                            (self.campaign_id, item["last_run_id"])).fetchone()[0]
            require("run-source-row-count-mismatch", run["source_row_count"] == source_count,
                    "run source row count differs from extraction items", run["source_row_count"], source_count)
            require("run-quarantine-count-mismatch", run["quarantine_count"] == quarantine_count,
                    "run quarantine count differs from quarantine rows", run["quarantine_count"], quarantine_count)
        return IntegrityVerification(document_id=row["id"], findings=tuple(findings))

    def _area(self, namespace: str) -> tuple[CompatibilityDocument, ...]:
        return self.enumerate(namespace=namespace)

    def world_state(self): return self._area("legacy.world-state.v1")
    def world_additional_state(self): return self._area("legacy.world-additional-state.v1")
    def character_narrative(self): return self._area("legacy.character-narrative.v1")
    def character_plot(self): return self._area("legacy.character-plot.v1")
    def character_plot_state(self): return self._area("legacy.character-plot-state.v1")
    def ambiance(self): return self._area("legacy.ambiance.v1")
    def emotional_state(self): return self._area("legacy.emotional-state.v1")
    def mechanical_stats(self): return self._area("legacy.mechanical-stats.v1")
    def dnd_provenance(self): return self._area("rules.dnd5e.legacy-v1")
    def inventory(self): return self._area("legacy.inventory-mechanics.v1")
    def relationships(self): return self._area("legacy.relationship-state.v1")
    def scene_graph(self): return self._area("legacy.scene-graph.v1")
    def game_state(self): return self._area("legacy.game-state.v1")
    def combat_state(self): return self._area("legacy.combat-state.v1")
    def unknown_columns(self): return self._area("legacy.unknown-columns.v1")
