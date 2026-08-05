"""Extract opinionated legacy state into lossless, read-only compatibility documents."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
import struct
from datetime import datetime, timezone
from uuid import uuid5

from database.compatibility_format import (
    COMPATIBILITY_FORMAT_VERSION,
    DETERMINISTIC_ID_NAMESPACE,
    EXTRACTION_SCHEMA_VERSION,
    EXTRACTOR_REVISION,
    METADATA_OWNER,
)

SCHEMA_VERSION = EXTRACTION_SCHEMA_VERSION
FORMAT_VERSION = COMPATIBILITY_FORMAT_VERSION
ID_NAMESPACE = DETERMINISTIC_ID_NAMESPACE
MAX_VALUE_BYTES = int(os.environ.get("ZERO_CONTEXT_EXTRACTION_MAX_VALUE_BYTES", 256 * 1024 * 1024))
MAX_DOCUMENT_BYTES = int(os.environ.get("ZERO_CONTEXT_EXTRACTION_MAX_DOCUMENT_BYTES", 512 * 1024 * 1024))
BLOB_STREAM_THRESHOLD = int(os.environ.get("ZERO_CONTEXT_EXTRACTION_BLOB_STREAM_THRESHOLD", 1024 * 1024))
_failure_injector = None
_before_foreign_key_validation = lambda conn: None

class ExtractionConflictError(RuntimeError):
    """Extractor ownership conflict with a safe sidecar-ready report."""

    def __init__(self, report):
        super().__init__("extractor-owned compatibility document conflict")
        self.report = report

AREAS = {
    "world-fixed": ("world_state", "legacy.world-state.v1", "legacy-world-state-row",
        ("war_active", "bridge_destroyed", "festival_active", "moon_phase", "weather"), (), ()),
    "world-additional": ("world_state", "legacy.world-additional-state.v1", "legacy-world-state-row",
        ("additional_state",), ("additional_state",), ()),
    "character-narrative": ("characters", "legacy.character-narrative.v1", "legacy-character-row",
        ("full_card_text", "character_core", "speech_patterns", "mannerisms", "physical_description", "goals",
         "prose_fingerprint", "name", "type", "status", "is_active", "current_location_id", "created_at"), (),
        (("current_location_id", "locations", "id"),)),
    "character-plot": ("characters", "legacy.character-plot.v1", "legacy-character-row",
        ("scenario_plot", "current_goal", "hidden_goal", "immediate_beat", "long_arc", "tension"), (), ()),
    "character-plot-state": ("characters", "legacy.character-plot-state.v1", "legacy-character-row",
        ("plot_state",), ("plot_state",), ()),
    "ambiance": ("ambiance_state", "legacy.ambiance.v1", "legacy-ambiance-row",
        ("location_id", "lighting", "weather", "soundscape", "vibe", "smell"), (),
        (("location_id", "locations", "id"),)),
    "emotional": ("emotional_state", "legacy.emotional-state.v1", "legacy-emotional-state-row",
        ("character_id", "trust", "fear", "arousal", "tension", "intimacy", "mood", "emotional_shift"), (),
        (("character_id", "characters", "id"),)),
    "mechanical": ("mechanical_stats", "legacy.mechanical-stats.v1", "legacy-mechanical-stats-row",
        ("character_id", "strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma",
         "hp_current", "hp_max", "armor_class", "proficiency_bonus", "level", "conditions"), ("conditions",),
        (("character_id", "characters", "id"),)),
    "dnd5e": ("dnd_stats", "rules.dnd5e.legacy-v1", "legacy-dnd5e-stats-row", (),
        ("skills", "armor_proficiencies", "weapon_proficiencies", "tool_proficiencies", "language_proficiencies",
         "prepared_spells", "known_spells", "racial_traits", "class_features", "feats", "equipment", "maneuvers"),
        (("character_id", "characters", "id"),)),
    "inventory": ("inventory", "legacy.inventory-mechanics.v1", "legacy-inventory-row",
        ("character_id", "item_name", "item_type", "quantity", "is_equipped", "damage_dice"), (),
        (("character_id", "characters", "id"),)),
    "relationship": ("relationships", "legacy.relationship-state.v1", "legacy-relationship-row",
        ("character_a_id", "character_b_id", "relationship_type", "trust_score"), (),
        (("character_a_id", "characters", "id"), ("character_b_id", "characters", "id"))),
    "scene-graph": ("scene_graph", "legacy.scene-graph.v1", "legacy-scene-graph-row",
        ("location_id", "object_name", "object_state", "npc_present", "visibility"), ("npc_present",),
        (("location_id", "locations", "id"),)),
    "game-state": ("game_state", "legacy.game-state.v1", "legacy-game-state-row",
        ("current_location_id", "current_scene_type", "combat_active", "current_turn", "game_day"), (),
        (("current_location_id", "locations", "id"),)),
    "combat-state": ("combat_state", "legacy.combat-state.v1", "legacy-combat-state-row",
        ("is_active", "turn_order", "current_turn", "round_number"), ("turn_order",), ()),
}

# The union of historical/current columns is deliberately broader than extracted areas.
KNOWN_COLUMNS = {
    "world_state": {"id", "war_active", "bridge_destroyed", "festival_active", "moon_phase", "weather", "additional_state"},
    "characters": {"id", "name", "type", "full_card_text", "character_core", "speech_patterns", "mannerisms",
        "physical_description", "goals", "scenario_plot", "current_goal", "hidden_goal", "immediate_beat", "long_arc",
        "tension", "prose_fingerprint", "status", "is_active", "plot_state", "current_location_id", "created_at"},
    "ambiance_state": {"id", "location_id", "lighting", "weather", "soundscape", "vibe", "smell"},
    "emotional_state": {"character_id", "trust", "fear", "arousal", "tension", "intimacy", "mood", "emotional_shift"},
    "mechanical_stats": {"character_id", "strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma",
        "hp_current", "hp_max", "armor_class", "proficiency_bonus", "level", "conditions"},
    "inventory": {"id", "character_id", "item_name", "item_type", "quantity", "is_equipped", "damage_dice"},
    "relationships": {"id", "character_a_id", "character_b_id", "relationship_type", "trust_score"},
    "scene_graph": {"id", "location_id", "object_name", "object_state", "npc_present", "visibility"},
    "game_state": {"id", "current_location_id", "current_scene_type", "combat_active", "current_turn", "game_day"},
    "combat_state": {"encounter_id", "is_active", "turn_order", "current_turn", "round_number"},
    "dnd_stats": {"character_id", "class", "subclass", "level", "experience_points", "strength", "dexterity",
        "constitution", "intelligence", "wisdom", "charisma", "armor_class", "hp_current", "hp_max", "speed",
        "initiative_bonus", "proficiency_bonus", "strength_save_bonus", "strength_save_proficiency",
        "dexterity_save_bonus", "dexterity_save_proficiency", "constitution_save_bonus", "constitution_save_proficiency",
        "intelligence_save_bonus", "intelligence_save_proficiency", "wisdom_save_bonus", "wisdom_save_proficiency",
        "charisma_save_bonus", "charisma_save_proficiency", "skills", "passive_perception", "darkvision",
        "armor_proficiencies", "weapon_proficiencies", "tool_proficiencies", "language_proficiencies",
        "spellcasting_ability", "spell_save_dc", "spell_attack_bonus", "cantrips_known", "spells_known",
        *(f"spell_slots_level_{n}" for n in range(1, 10)), "prepared_spells", "known_spells", "racial_traits",
        "class_features", "feats", "equipment", "maneuvers"},
}

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS legacy_extraction_runs (
 id TEXT PRIMARY KEY,campaign_id TEXT NOT NULL,extraction_schema_version INTEGER NOT NULL,extractor_revision TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('running','complete','failed','parity_failed')),source_schema_hash TEXT NOT NULL,
 started_at TEXT NOT NULL,completed_at TEXT,source_row_count INTEGER,document_count INTEGER,quarantine_count INTEGER,
 source_root_hash TEXT,document_root_hash TEXT,legacy_before_hash TEXT,legacy_after_hash TEXT,
 parity_status TEXT NOT NULL CHECK(parity_status IN ('pending','exact','failed')),report_json TEXT,failure_json TEXT,
 FOREIGN KEY(campaign_id) REFERENCES campaigns(id));
CREATE TABLE IF NOT EXISTS legacy_extraction_items (
 id TEXT PRIMARY KEY,campaign_id TEXT NOT NULL,extraction_schema_version INTEGER NOT NULL,extractor_revision TEXT NOT NULL,
 area TEXT NOT NULL,source_table TEXT NOT NULL,source_identity_json TEXT NOT NULL,source_identity_hash TEXT NOT NULL,
 source_columns_json TEXT NOT NULL,source_hash TEXT NOT NULL,namespace TEXT NOT NULL,subject_type TEXT NOT NULL,
 subject_id TEXT NOT NULL,state_document_id TEXT,document_content_hash TEXT,parse_status TEXT NOT NULL,
 warning_json TEXT NOT NULL,status TEXT NOT NULL,first_run_id TEXT NOT NULL,last_run_id TEXT NOT NULL,
 extracted_at TEXT NOT NULL,verified_at TEXT NOT NULL,FOREIGN KEY(campaign_id) REFERENCES campaigns(id),
 FOREIGN KEY(state_document_id) REFERENCES state_documents(id),FOREIGN KEY(first_run_id) REFERENCES legacy_extraction_runs(id),
 FOREIGN KEY(last_run_id) REFERENCES legacy_extraction_runs(id),
 UNIQUE(campaign_id,extraction_schema_version,area,source_table,source_identity_hash),
 UNIQUE(campaign_id,namespace,subject_type,subject_id));
CREATE TABLE IF NOT EXISTS legacy_extraction_quarantine (
 id TEXT PRIMARY KEY,campaign_id TEXT NOT NULL,run_id TEXT NOT NULL,extraction_item_id TEXT,source_table TEXT NOT NULL,
 source_identity_json TEXT,source_column TEXT,reason_code TEXT NOT NULL,severity TEXT NOT NULL,raw_storage_class TEXT,
 raw_value_blob BLOB,declared_type TEXT,error_json TEXT NOT NULL,created_at TEXT NOT NULL,
 FOREIGN KEY(campaign_id) REFERENCES campaigns(id),FOREIGN KEY(run_id) REFERENCES legacy_extraction_runs(id),
 FOREIGN KEY(extraction_item_id) REFERENCES legacy_extraction_items(id));
CREATE INDEX IF NOT EXISTS idx_legacy_extraction_runs_campaign ON legacy_extraction_runs(campaign_id,started_at);
CREATE INDEX IF NOT EXISTS idx_legacy_extraction_items_source ON legacy_extraction_items(campaign_id,source_table,source_identity_hash);
CREATE INDEX IF NOT EXISTS idx_legacy_extraction_items_status ON legacy_extraction_items(campaign_id,status);
CREATE INDEX IF NOT EXISTS idx_legacy_extraction_quarantine_source ON legacy_extraction_quarantine(campaign_id,source_table,source_column);
"""

def _q(name):
    return '"' + name.replace('"', '""') + '"'

def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def _hash(data):
    return hashlib.sha256(b"zero-context-legacy-v1\0" + data).hexdigest()

def _frame(parts):
    return b"".join(struct.pack(">Q", len(part)) + part for part in parts)

def _value_envelope(value, storage_class=None, raw_bytes=None):
    kind = storage_class or ("null" if value is None else "blob" if isinstance(value, bytes) else
        "integer" if isinstance(value, int) else "real" if isinstance(value, float) else "text")
    if kind == "null": return {"storage_class": "null"}
    if kind == "integer":
        number = int(value); width = max(1, (number.bit_length() + 8) // 8)
        binary = number.to_bytes(width, "big", signed=True)
        return {"storage_class": "integer", "decimal": str(number), "signed_big_endian_base64": base64.b64encode(binary).decode()}
    if kind == "real":
        binary = struct.pack(">d", float(value))
        return {"storage_class": "real", "ieee754_binary64_hex": binary.hex(), "decimal": repr(float(value))}
    if kind == "blob":
        data = bytes(value)
        _guard(data)
        return {"storage_class": "blob", "base64": base64.b64encode(data).decode(), "byte_length": len(data)}
    data = raw_bytes if raw_bytes is not None else (
        bytes(value) if isinstance(value, bytes) else str(value).encode("utf-8", "surrogatepass")
    )
    _guard(data)
    result = {"storage_class": "text", "base64": base64.b64encode(data).decode(), "byte_length": len(data),
              "encoding": "utf-8", "decoding_status": "valid"}
    try: result["decoded"] = data.decode("utf-8")
    except UnicodeDecodeError: result["decoding_status"] = "invalid"
    return result

def _guard(data):
    if len(data) > MAX_VALUE_BYTES:
        raise RuntimeError(f"legacy value is {len(data)} bytes; extraction safeguard is {MAX_VALUE_BYTES}; no data written")

def _envelope_bytes(value):
    kind = value["storage_class"].encode()
    if kind == b"null": raw = b""
    elif kind == b"integer": raw = base64.b64decode(value["signed_big_endian_base64"])
    elif kind == b"real": raw = bytes.fromhex(value["ieee754_binary64_hex"])
    else: raw = base64.b64decode(value["base64"])
    return _frame((kind, raw))

def _columns(conn, table):
    return [dict(zip(("cid","name","type","notnull","dflt_value","pk","hidden"), row))
            for row in conn.execute(f"PRAGMA table_xinfo({_q(table)})")]

def _tables(conn):
    return {r[0]: r[1] or "" for r in conn.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}

def _metadata_rows(conn, pragma, table):
    return [tuple(row) for row in conn.execute(f"PRAGMA {pragma}({_q(table)})")]

def _row_order(columns, has_rowid, rowid_expression=None, conn=None, table=None, row=None):
    primary = [column for column in sorted(columns, key=lambda item: item["pk"]) if column["pk"]]
    if primary:
        terms = []
        for column in primary:
            quoted = _q(column["name"])
            terms.extend((f"typeof({quoted}) COLLATE BINARY", f"CAST({quoted} AS BLOB) COLLATE BINARY"))
        return ",".join(terms)
    if conn is not None and table is not None:
        for index in conn.execute(f"PRAGMA index_list({_q(table)})"):
            if index[2]:
                names = [x[2] for x in conn.execute(f"PRAGMA index_info({_q(index[1])})") if x[2] is not None]
                candidates = [c for c in columns if c["name"] in names]
                if len(candidates) == len(names):
                    terms = []
                    for column in candidates:
                        quoted = _q(column["name"])
                        terms.extend((f"typeof({quoted}) COLLATE BINARY", f"CAST({quoted} AS BLOB) COLLATE BINARY"))
                    return ",".join(terms)
    if has_rowid and rowid_expression:
        return rowid_expression
    raise RuntimeError("WITHOUT ROWID table has no declared primary key")

def _preflight_table(conn, table, columns):
    for column in columns:
        quoted = _q(column["name"])
        maximum = conn.execute(
            f"SELECT max(length(CAST({quoted} AS BLOB))) FROM {_q(table)}"
        ).fetchone()[0]
        if maximum is not None:
            worst_case = maximum + ((maximum + 2) // 3) * 4
            if maximum > MAX_VALUE_BYTES or worst_case > MAX_DOCUMENT_BYTES:
                raise RuntimeError(
                    f"legacy value in {table}.{column['name']} is {maximum} bytes; "
                    f"estimated encoded envelope is {worst_case} bytes; extraction safeguards are "
                    f"value={MAX_VALUE_BYTES}, document={MAX_DOCUMENT_BYTES}; no data written"
                )

def _read_blob(conn, table, column, rowid, size):
    if rowid is None:
        raise RuntimeError(f"large blob streaming requires rowid for {table}.{column}")
    # Python sqlite3 exposes incremental blob reads, but state_documents still
    # require one exact base64 value. Preflight in _preflight_table enforces a
    # conservative materialization budget before this point; use bytearray so
    # chunked reads do not retain both a chunk list and a second joined copy.
    data = bytearray()
    with conn.blobopen(table, column, rowid, readonly=True) as blob:
        remaining = size
        while remaining:
            chunk = blob.read(min(1024 * 1024, remaining))
            if not chunk:
                raise RuntimeError(f"short blob read for {table}.{column}")
            data.extend(chunk)
            remaining -= len(chunk)
    return bytes(data)

def _iter_rows(conn, table, columns, table_sql):
    has_rowid = "WITHOUT ROWID" not in table_sql.upper()
    names = {column["name"].casefold() for column in columns}
    rowid_expression = next((candidate for candidate in ("rowid", "_rowid_", "oid")
                             if candidate not in names), None) if has_rowid else None
    expressions = [rowid_expression or "NULL"]
    for column in columns:
        quoted = _q(column["name"])
        expressions.extend((
            f"typeof({quoted})",
            f"length(CAST({quoted} AS BLOB))",
            f"CASE WHEN typeof({quoted})='text' THEN CAST({quoted} AS BLOB) "
            f"WHEN typeof({quoted})='blob' AND length({quoted})>{BLOB_STREAM_THRESHOLD} "
            f"AND {1 if rowid_expression else 0}=1 THEN NULL "
            f"ELSE {quoted} END",
        ))
    order = _row_order(columns, has_rowid, rowid_expression, conn, table)
    cursor = conn.execute(f"SELECT {','.join(expressions)} FROM {_q(table)} ORDER BY {order}")
    for raw_row in cursor:
        rowid = raw_row[0]
        values = {}
        offset = 1
        for column in columns:
            storage_class, size, value = raw_row[offset:offset + 3]
            offset += 3
            if storage_class == "blob" and value is None and size:
                value = _read_blob(conn, table, column["name"], rowid, size)
            values[column["name"]] = (storage_class, value, size)
        yield {"rowid": rowid, "values": values}

def _schema_hash(conn):
    rows = conn.execute("SELECT type,name,tbl_name,coalesce(sql,'') FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
    return _hash(_frame(tuple(_json(tuple(r)).encode() for r in rows)))

def _identity(conn, table, columns, row, schema_hash, full_hash):
    pks = sorted((c for c in columns if c["pk"]), key=lambda c: c["pk"])
    warnings = []
    if pks:
        chosen = pks; kind = "primary-key"
    else:
        chosen = []
        for index in conn.execute(f"PRAGMA index_list({_q(table)})"):
            if index[2]:
                names = [x[2] for x in conn.execute(f"PRAGMA index_info({_q(index[1])})")]
                candidates = [c for c in columns if c["name"] in names]
                if len(candidates) == len(names) and all(row["values"][c["name"]][0] != "null" for c in candidates):
                    chosen = candidates; kind = "unique-key"; break
        if not chosen:
            if row["rowid"] is None:
                raise RuntimeError(f"no stable or approved weak identity for {table}")
            warnings.append("weak-rowid-identity")
            identity = {"kind":"weak-rowid", "rowid": str(row["rowid"]), "table_schema_hash": schema_hash,
                        "full_row_source_hash": full_hash}
            return identity, warnings
    values = [{"name": c["name"], "declared_type": c["type"],
               "value": _stored_envelope(row["values"][c["name"]])} for c in chosen]
    return {"kind": kind, "columns": values}, warnings

def _default_origin(column, value):
    default = column["dflt_value"]
    if default is None: return "unknown"
    token = str(default).strip().strip("()")
    if re.fullmatch(r"[-+]?\d+", token) and isinstance(value, int): return "possible" if int(token) == value else "not_possible"
    if re.fullmatch(r"[-+]?(\d+\.\d*|\d*\.\d+)", token) and isinstance(value, (int,float)):
        return "possible" if float(token) == value else "not_possible"
    if len(token) >= 2 and token[0] == token[-1] == "'" and isinstance(value, str):
        return "possible" if token[1:-1].replace("''", "'") == value else "not_possible"
    return "unknown"

def _stored_envelope(stored):
    storage_class, value, _size = stored
    if storage_class == "text":
        return _value_envelope(value, storage_class="text", raw_bytes=bytes(value))
    return _value_envelope(value, storage_class=storage_class)

def _source_columns(metadata, selected, row):
    by_name = {c["name"]: c for c in metadata}
    result = []
    for name in selected:
        c = by_name[name]
        stored = row["values"][name]
        value = stored[1]
        result.append({"ordinal": c["cid"], "name": name, "declared_type": c["type"] or "", "not_null": bool(c["notnull"]),
            "declared_default_sql": c["dflt_value"], "primary_key_position": c["pk"], "hidden": c["hidden"],
            "value": _stored_envelope(stored), "may_have_originated_from_default": _default_origin(c, value)})
    return result

def _source_stream(table, identity, columns):
    parts = [FORMAT_VERSION.encode(), table.encode(), _json(identity).encode()]
    for col in sorted(columns, key=lambda x: x["ordinal"]):
        parts.extend((col["name"].encode(), col["declared_type"].encode(), _envelope_bytes(col["value"])))
    return _frame(tuple(parts))

def _parse_json(columns, json_names):
    parsed, warnings, statuses, diagnostics = {}, [], [], []
    for col in columns:
        if col["name"] not in json_names: continue
        value = col["value"]
        if value["storage_class"] == "null":
            statuses.append("not_applicable")
            diagnostics.append({"column": col["name"], "reason_code": "json-null-not-applicable"})
            continue
        if value["storage_class"] != "text":
            statuses.append("not_applicable")
            diagnostics.append({"column": col["name"], "reason_code": "json-non-text-storage",
                                "storage_class": value["storage_class"]})
            continue
        if value.get("decoding_status") != "valid":
            statuses.append("invalid")
            diagnostics.append({"column": col["name"], "reason_code": "invalid-text-encoding"})
            continue
        try:
            parsed[col["name"]] = json.loads(value["decoded"])
            statuses.append("valid")
            diagnostics.append({"column": col["name"], "reason_code": "json-valid"})
        except json.JSONDecodeError as error:
            statuses.append("invalid")
            diagnostic = {"column": col["name"], "reason_code": "malformed-json",
                          "error_type": type(error).__name__, "offset": error.pos}
            diagnostics.append(diagnostic); warnings.append(f"{col['name']}:malformed-json:{error.pos}")
        except Exception as error:
            statuses.append("invalid")
            diagnostic = {"column": col["name"], "reason_code": "json-parser-failure",
                          "error_type": type(error).__name__}
            diagnostics.append(diagnostic); warnings.append(f"{col['name']}:json-parser-failure:{type(error).__name__}")
    if not statuses: status = "not_applicable"
    elif all(x == "valid" for x in statuses): status = "valid"
    elif "valid" in statuses: status = "partially_valid"
    else: status = "invalid" if "invalid" in statuses else "not_applicable"
    return status, parsed, warnings, diagnostics

def _foreign_key_failures(conn):
    return sorted(
        ({"table": row[0], "rowid": row[1], "parent": row[2], "fkid": row[3]}
         for row in conn.execute("PRAGMA foreign_key_check")),
        key=lambda item: _json(item).encode("utf-8"),
    )

def _references(conn, columns, specs):
    values = {c["name"]: c["value"] for c in columns}; tables = _tables(conn); result = []
    for source, target, target_col in specs:
        if source not in values: continue
        value = values[source]; status = "null" if value["storage_class"] == "null" else "target-table-missing" if target not in tables else "unresolved"
        if status == "unresolved":
            raw = value.get("decimal", value.get("decoded"))
            count = conn.execute(f"SELECT count(*) FROM {_q(target)} WHERE {_q(target_col)}=?", (raw,)).fetchone()[0]
            status = "resolved" if count == 1 else "ambiguous" if count > 1 else "unresolved"
        result.append({"source_column":source,"target_table":target,"target_column":target_col,"raw_value":value,"status":status})
    return result

def _content_hash(state_json):
    return hashlib.sha256(("zero-context-state-v1\0" + state_json).encode()).hexdigest()

def _legacy_fingerprint(conn):
    excluded = {"schema_version", "campaigns", "state_documents", "state_patch_log", "state_idempotency",
        "state_projection_definitions", "state_projection_values", "legacy_extraction_runs", "legacy_extraction_items",
        "legacy_extraction_quarantine"}
    parts = [b"legacy-fingerprint-v2"]
    for table, sql in sorted(_tables(conn).items(), key=lambda item: item[0].encode("utf-8")):
        if table in excluded: continue
        columns = _columns(conn, table)
        row_count = conn.execute(f"SELECT count(*) FROM {_q(table)}").fetchone()[0]
        metadata = {
            "table_xinfo": _metadata_rows(conn, "table_xinfo", table),
            "foreign_key_list": _metadata_rows(conn, "foreign_key_list", table),
            "index_list": _metadata_rows(conn, "index_list", table),
            "indexes": [],
        }
        for index in metadata["index_list"]:
            metadata["indexes"].append({
                "name": index[1],
                "index_xinfo": _metadata_rows(conn, "index_xinfo", index[1]),
                "sql": (lambda row: row[0] if row is not None else "")(
                    conn.execute("SELECT coalesce(sql,'') FROM sqlite_master WHERE type='index' AND name=?", (index[1],)).fetchone()
                ),
            })
        triggers = [tuple(row) for row in conn.execute(
            "SELECT name,coalesce(sql,'') FROM sqlite_master WHERE type='trigger' AND tbl_name=? ORDER BY CAST(name AS BLOB)",
            (table,),
        )]
        parts.append(_frame((table.encode(), sql.encode(), str(row_count).encode(),
                             _json(metadata).encode(), _json(triggers).encode())))
        for row in _iter_rows(conn, table, columns, sql):
            identity, _warnings = _identity(conn, table, columns, row, _hash(sql.encode()), "fingerprint")
            row_parts = [_json(identity).encode()]
            for column in columns:
                row_parts.extend((column["name"].encode(), _envelope_bytes(_stored_envelope(row["values"][column["name"]]))))
            parts.append(_frame(tuple(row_parts)))
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'").fetchone():
        sequence = [tuple(row) for row in conn.execute(
            "SELECT name,seq FROM sqlite_sequence ORDER BY CAST(name AS BLOB)"
        )]
        parts.append(_frame((b"sqlite_sequence", _json(sequence).encode())))
    return _hash(_frame(tuple(parts)))

def migrate(conn: sqlite3.Connection, campaign_id: str | None = None, foreign_keys_before=None) -> dict:
    """Run the initial v8 extraction. The caller owns the encompassing transaction."""
    campaign = conn.execute("SELECT id FROM campaigns WHERE lifecycle_status!='deleted'").fetchall()
    if len(campaign) != 1: raise RuntimeError("v8 extraction requires exactly one live campaign")
    actual_campaign = campaign[0][0]
    if campaign_id is not None and campaign_id != actual_campaign: raise RuntimeError("v8 campaign identity mismatch")
    tables = _tables(conn)
    excluded = {"schema_version", "campaigns", "state_documents", "state_patch_log", "state_idempotency",
                "state_projection_definitions", "state_projection_values", "legacy_extraction_runs",
                "legacy_extraction_items", "legacy_extraction_quarantine"}
    for table in sorted(set(tables) - excluded, key=lambda value: value.encode("utf-8")):
        _preflight_table(conn, table, _columns(conn, table))
    before = _legacy_fingerprint(conn); schema_hash = _schema_hash(conn)
    foreign_keys_before = foreign_keys_before if foreign_keys_before is not None else _foreign_key_failures(conn)
    for statement in TABLE_SQL.split(";"):
        if statement.strip():
            conn.execute(statement)
    return extract(conn, actual_campaign, legacy_before=before, source_schema_hash=schema_hash,
                   foreign_keys_before=foreign_keys_before)

def extract(conn: sqlite3.Connection, campaign_id: str, *, legacy_before=None, source_schema_hash=None,
            foreign_keys_before=None) -> dict:
    """Explicit administrative extraction/refresh; never called by legacy writes."""
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    run_id = str(uuid5(ID_NAMESPACE, f"{campaign_id}\0{EXTRACTOR_REVISION}\0{now}"))
    schema_hash = source_schema_hash or _schema_hash(conn); before = legacy_before or _legacy_fingerprint(conn)
    fk_before = foreign_keys_before if foreign_keys_before is not None else _foreign_key_failures(conn)
    conn.execute("INSERT INTO legacy_extraction_runs(id,campaign_id,extraction_schema_version,extractor_revision,status,source_schema_hash,started_at,parity_status,legacy_before_hash) VALUES(?,?,?,?,?,?,?,?,?)",
        (run_id,campaign_id,SCHEMA_VERSION,EXTRACTOR_REVISION,"running",schema_hash,now,"pending",before))
    tables = _tables(conn); seen = set(); leaves = []; counts = {}; quarantine = 0
    areas = dict(AREAS)
    for table, known in KNOWN_COLUMNS.items():
        if table in tables:
            unknown = tuple(c["name"] for c in _columns(conn, table) if c["name"] not in known)
            if unknown:
                areas[f"unknown-columns:{table}"] = (table, "legacy.unknown-columns.v1", "legacy-source-row", unknown, (), ())
    for area, (table, namespace, subject_type, configured, json_names, reference_specs) in areas.items():
        if table not in tables: continue
        metadata = _columns(conn, table); actual = [c["name"] for c in metadata]
        _preflight_table(conn, table, metadata)
        selected = ([name for name in actual if name in KNOWN_COLUMNS["dnd_stats"]]
                    if area == "dnd5e" else [name for name in configured if name in actual])
        if not selected: continue
        for row in _iter_rows(conn, table, metadata, tables[table]):
            full_cols = _source_columns(metadata, actual, row)
            full_hash = _hash(_source_stream(table, {"preidentity": True}, full_cols))
            identity, identity_warnings = _identity(conn, table, metadata, row, _hash(tables[table].encode()), full_hash)
            identity_json = _json(identity); identity_hash = _hash(identity_json.encode())
            columns = _source_columns(metadata, selected, row)
            source_hash = _hash(_source_stream(table, identity, columns))
            parse_status, parsed, warnings, json_diagnostics = _parse_json(columns, json_names)
            warnings += identity_warnings
            refs = _references(conn, columns, reference_specs)
            warnings += [f"{r['source_column']}:reference-{r['status']}" for r in refs if r["status"] not in ("resolved","null")]
            timestamps = [c for c in columns if c["name"].lower() in ("created_at","updated_at","timestamp")]
            state = {"compatibility_format":FORMAT_VERSION,"extraction":{"schema_version":SCHEMA_VERSION,
                "extractor_revision":EXTRACTOR_REVISION,"extracted_at":now,"source_hash":source_hash,
                "parse_status":parse_status,"warnings":warnings,"json_diagnostics":json_diagnostics},"source":{"table":table,"identity":identity,
                "columns":columns,"timestamps":timestamps},"references":refs,"parsed_views":parsed}
            state_json = _json(state)
            if len(state_json.encode()) > MAX_DOCUMENT_BYTES: raise RuntimeError("compatibility document exceeds extraction safeguard; no data written")
            subject_id = f"{table}/{identity['kind']}/{identity_hash}"
            item_id = str(uuid5(ID_NAMESPACE, f"{campaign_id}\0{SCHEMA_VERSION}\0{EXTRACTOR_REVISION}\0{area}\0{table}\0{identity_hash}"))
            document_id = str(uuid5(ID_NAMESPACE, "document\0" + item_id)); target_hash = _content_hash(state_json)
            prior = conn.execute("SELECT * FROM legacy_extraction_items WHERE id=?",(item_id,)).fetchone()
            document = conn.execute("SELECT * FROM state_documents WHERE id=?",(document_id,)).fetchone()
            status = "extracted"
            if prior:
                if document and (
                    document["content_hash"] != prior["document_content_hash"]
                    or _content_hash(document["state_json"]) != document["content_hash"]
                    or json.loads(document["metadata_json"] or "{}").get("owner") != METADATA_OWNER
                ):
                    actual_hash = _content_hash(document["state_json"])
                    raise ExtractionConflictError({
                        "status": "conflict", "campaign_id": campaign_id, "source_table": table,
                        "source_identity": identity, "source_hash": source_hash,
                        "document_id": document_id, "namespace": namespace,
                        "expected_hash": prior["document_content_hash"], "actual_hash": actual_hash,
                        "guidance": "Restore the extractor-owned document from backup or inspect the manual mutation; do not overwrite it.",
                    })
                if prior["source_hash"] == source_hash and document:
                    status = "unchanged"; target_hash = document["content_hash"]
                elif document:
                    conn.execute("UPDATE state_documents SET state_json=?,revision=revision+1,content_hash=?,metadata_json=?,updated_at=? WHERE id=?",
                        (state_json,target_hash,_json({"owner":METADATA_OWNER}),now,document_id)); status="refreshed"
                else:
                    conn.execute("INSERT INTO state_documents(id,campaign_id,namespace,subject_type,subject_id,state_json,revision,content_hash,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (document_id,campaign_id,namespace,subject_type,subject_id,state_json,1,target_hash,_json({"owner":METADATA_OWNER}),now,now)); status="restored"
                conn.execute("UPDATE legacy_extraction_items SET source_columns_json=?,source_hash=?,document_content_hash=?,parse_status=?,warning_json=?,status=?,last_run_id=?,verified_at=? WHERE id=?",
                    (_json(columns),source_hash,target_hash,parse_status,_json(warnings),status,run_id,now,item_id))
            else:
                if document: raise RuntimeError(f"untracked compatibility target collision: {document_id}")
                conn.execute("INSERT INTO state_documents(id,campaign_id,namespace,subject_type,subject_id,state_json,revision,content_hash,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (document_id,campaign_id,namespace,subject_type,subject_id,state_json,1,target_hash,_json({"owner":METADATA_OWNER}),now,now))
                conn.execute("INSERT INTO legacy_extraction_items VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (item_id,campaign_id,SCHEMA_VERSION,EXTRACTOR_REVISION,area,table,identity_json,identity_hash,_json(columns),source_hash,
                     namespace,subject_type,subject_id,document_id,target_hash,parse_status,_json(warnings),status,run_id,run_id,now,now))
            if parse_status in ("invalid","partially_valid"):
                by_name = {column["name"]: column for column in columns}
                for diagnostic in json_diagnostics:
                    if diagnostic["reason_code"] == "malformed-json":
                        col = by_name[diagnostic["column"]]
                        qid = str(uuid5(ID_NAMESPACE, f"{run_id}\0{item_id}\0{col['name']}\0malformed-json"))
                        raw = _envelope_bytes(col["value"])
                        conn.execute("INSERT OR IGNORE INTO legacy_extraction_quarantine VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (qid,campaign_id,run_id,item_id,table,identity_json,col["name"],"malformed-json","warning",
                             col["value"]["storage_class"],raw,col["declared_type"],_json(diagnostic),now)); quarantine += 1
            reconstructed = json.loads(conn.execute("SELECT state_json FROM state_documents WHERE id=?",(document_id,)).fetchone()[0])
            parity_hash = _hash(_source_stream(table,reconstructed["source"]["identity"],reconstructed["source"]["columns"]))
            if parity_hash != source_hash: raise RuntimeError(f"source-to-document parity failed for {item_id}")
            leaves.append(source_hash); seen.add(item_id); counts[area] = counts.get(area,0)+1
    for item in conn.execute("SELECT id FROM legacy_extraction_items WHERE campaign_id=?",(campaign_id,)):
        if item[0] not in seen: conn.execute("UPDATE legacy_extraction_items SET status='source_missing_on_rerun',last_run_id=?,verified_at=? WHERE id=?",(run_id,now,item[0]))
    after = _legacy_fingerprint(conn)
    if before != after: raise RuntimeError("legacy table/value fingerprint changed during extraction")
    root = _hash(_frame(tuple(sorted(x.encode() for x in leaves))))
    known_tables = set(KNOWN_COLUMNS) | {"dnd_stats"}
    internal = {"schema_version","campaigns","state_documents","state_patch_log","state_idempotency","state_projection_definitions","state_projection_values","legacy_extraction_runs","legacy_extraction_items","legacy_extraction_quarantine"}
    unknown_tables = sorted(set(tables)-known_tables-internal-{"locations","scene_history","conversational_facts","event_log","working_memory","knowledge_chunks"})
    _before_foreign_key_validation(conn)
    fk_after = _foreign_key_failures(conn)
    if fk_after != fk_before:
        raise RuntimeError("foreign-key violations changed during v8 extraction")
    report = {"campaign_id":campaign_id,"schema_version":SCHEMA_VERSION,"areas":counts,"unknown_tables":unknown_tables,
              "source_root_hash":root,"document_root_hash":root,"exact":True,"legacy_before_hash":before,
              "legacy_after_hash":after,"malformed_count":quarantine,"quarantine_count":quarantine,
              "foreign_key_baseline":fk_before,"foreign_key_final":fk_after,
              "pre_existing_foreign_key_violation_count":len(fk_before)}
    if _failure_injector: _failure_injector("legacy_extraction_v8")
    conn.execute("UPDATE legacy_extraction_runs SET status='complete',completed_at=?,source_row_count=?,document_count=?,quarantine_count=?,source_root_hash=?,document_root_hash=?,legacy_after_hash=?,parity_status='exact',report_json=? WHERE id=?",
        (now,len(leaves),len(leaves),quarantine,root,root,after,_json(report),run_id))
    return report
