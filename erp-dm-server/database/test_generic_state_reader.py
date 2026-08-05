import json
import hashlib
import sqlite3

import pytest

from database.db_manager import DatabaseManager
from database.generic_state_reader import (
    COMPATIBILITY_NAMESPACES,
    CompatibilityReadError,
    ImmutableMapping,
    GenericStateReader,
)


def populated_campaign(tmp_path):
    path = tmp_path / "reader.db"
    manager = DatabaseManager(str(path))
    conn = manager._get_connection()
    conn.execute("ALTER TABLE characters ADD COLUMN [custom lore] BLOB")
    conn.execute("INSERT INTO locations(id,name) VALUES(2,'Dock')")
    conn.execute("""INSERT INTO characters(
        id,name,type,full_card_text,character_core,scenario_plot,current_goal,plot_state,
        current_location_id,[custom lore]) VALUES(1,'Mara','NPC','card','core','plot','goal',?,2,?)""",
        ('{"broken":', sqlite3.Binary(b"\x00lore")))
    conn.execute("INSERT INTO world_state(id,weather,additional_state) VALUES(1,'rain','{\"moon\":null}')")
    conn.execute("INSERT INTO ambiance_state(id,location_id,lighting) VALUES(1,2,'dim')")
    conn.execute("INSERT INTO emotional_state(character_id,trust,mood) VALUES(1,63,'guarded')")
    conn.execute("INSERT INTO mechanical_stats(character_id,hp_current,conditions) VALUES(1,7,'[]')")
    conn.execute("INSERT INTO dnd_stats(character_id,class,equipment) VALUES(1,'Wizard','[]')")
    conn.execute("INSERT INTO inventory(id,character_id,item_name,quantity) VALUES(1,1,'Key',2)")
    conn.execute("INSERT INTO relationships(id,character_a_id,character_b_id,relationship_type) VALUES(1,1,1,'owes')")
    conn.execute("INSERT INTO scene_graph(id,location_id,object_name,npc_present) VALUES(1,2,'Door','[1,999]')")
    conn.execute("INSERT INTO game_state(id,current_location_id,current_scene_type) VALUES(1,2,'arrival')")
    conn.execute("INSERT INTO combat_state(encounter_id,is_active,turn_order) VALUES(1,0,'[]')")
    conn.commit(); conn.close()
    manager.refresh_legacy_extraction()
    return manager, GenericStateReader(path, manager.campaign_id)


def test_reader_enumerates_every_v8_namespace_and_metadata(tmp_path):
    manager, reader = populated_campaign(tmp_path)
    documents = reader.enumerate()
    assert {document.namespace for document in documents} == COMPATIBILITY_NAMESPACES
    assert all(reader.verify(document.document_id).valid for document in documents)
    assert len(reader.by_campaign(manager.campaign_id)) == len(documents)
    assert reader.extraction_runs()[-1].parity_status == "exact"
    assert len(reader.extraction_items()) == len(documents)
    assert any(row["reason_code"] == "malformed-json" for row in reader.quarantine())
    with pytest.raises(CompatibilityReadError):
        reader.by_campaign("not-this-campaign")


def test_area_methods_return_typed_lossless_values_and_passive_dnd(tmp_path):
    manager, reader = populated_campaign(tmp_path)
    methods = (reader.world_state, reader.world_additional_state, reader.character_narrative,
        reader.character_plot, reader.character_plot_state, reader.ambiance, reader.emotional_state,
        reader.mechanical_stats, reader.dnd_provenance, reader.inventory, reader.relationships,
        reader.scene_graph, reader.game_state, reader.combat_state, reader.unknown_columns)
    assert all(len(method()) == 1 for method in methods)
    mechanical = reader.mechanical_stats()[0]
    hp = mechanical.source.column("hp_current")
    assert hp.value.storage_class == "integer"
    assert hp.value.value == 7
    unknown = reader.unknown_columns()[0].source.column("custom lore").value
    assert unknown.storage_class == "blob"
    assert unknown.value == b"\x00lore"
    assert reader.dnd_provenance()[0].source.values["class"] == "Wizard"
    conn = manager._get_connection()
    assert conn.execute("SELECT rules_profile_id FROM campaigns").fetchone()[0] is None
    conn.close()


def test_malformed_json_is_preserved_and_parsed_views_are_diagnostic(tmp_path):
    _, reader = populated_campaign(tmp_path)
    malformed = reader.character_plot_state()[0]
    assert malformed.extraction.parse_status == "invalid"
    assert malformed.source.column("plot_state").value.value == '{"broken":'
    assert malformed.parsed_views == {}
    valid = reader.world_additional_state()[0]
    assert valid.extraction.parse_status == "valid"
    assert valid.source.column("additional_state").value.value == '{"moon":null}'
    assert valid.parsed_views == {"additional_state": {"moon": None}}


def test_reads_never_mutate_documents_patch_log_or_legacy_authority(tmp_path):
    manager, reader = populated_campaign(tmp_path)
    conn = manager._get_connection()
    before_documents = conn.execute("SELECT id,state_json,revision,content_hash,updated_at FROM state_documents ORDER BY id").fetchall()
    before_patches = conn.execute("SELECT count(*) FROM state_patch_log").fetchone()[0]
    conn.execute("UPDATE world_state SET weather='snow' WHERE id=1")
    conn.commit(); conn.close()

    assert reader.world_state()[0].source.values["weather"] == "rain"
    reader.enumerate(); reader.extraction_runs(); reader.extraction_items(); reader.quarantine()

    conn = manager._get_connection()
    assert conn.execute("SELECT weather FROM world_state WHERE id=1").fetchone()[0] == "snow"
    assert conn.execute("SELECT id,state_json,revision,content_hash,updated_at FROM state_documents ORDER BY id").fetchall() == before_documents
    assert conn.execute("SELECT count(*) FROM state_patch_log").fetchone()[0] == before_patches
    conn.close()


def test_reader_works_on_fresh_v8_database(tmp_path):
    manager = DatabaseManager(str(tmp_path / "fresh.db"))
    reader = GenericStateReader(manager.db_path, manager.campaign_id)
    assert reader.enumerate() == ()
    assert reader.world_state() == ()
    assert reader.extraction_runs()[0].report["exact"] is True


def test_exact_lookup_and_ambiguous_compatibility_lookup(tmp_path):
    manager, reader = populated_campaign(tmp_path)
    original = reader.world_state()[0]
    assert reader.get(original.namespace, original.subject_type, original.subject_id).document_id == original.document_id
    conn = manager._get_connection()
    row = conn.execute("SELECT * FROM state_documents WHERE id=?", (original.document_id,)).fetchone()
    conn.execute("""INSERT INTO state_documents(
        id,campaign_id,namespace,subject_type,subject_id,state_json,revision,content_hash,metadata_json
    ) VALUES(?,?,?,?,?,?,?,?,?)""", ("ambiguous-copy", row["campaign_id"], row["namespace"],
        "legacy-alternate-world-row", row["subject_id"], row["state_json"], row["revision"],
        row["content_hash"], row["metadata_json"]))
    conn.commit(); conn.close()
    assert reader.get(original.namespace, original.subject_type, original.subject_id, verify=False).document_id == original.document_id
    assert reader.get(original.namespace, "legacy-alternate-world-row", original.subject_id, verify=False).document_id == "ambiguous-copy"
    with pytest.raises(CompatibilityReadError, match="ambiguous"):
        reader.get_unique(original.namespace, original.subject_id, verify=False)


def test_reader_collections_are_immutable_and_mutable_copy_is_isolated(tmp_path):
    _, reader = populated_campaign(tmp_path)
    document = reader.world_additional_state()[0]
    assert isinstance(document.parsed_views, ImmutableMapping)
    assert isinstance(document.extraction.warnings, tuple)
    with pytest.raises(TypeError):
        document.parsed_views["changed"] = True
    with pytest.raises(TypeError):
        document.parsed_views["additional_state"]["moon"] = "full"
    with pytest.raises(AttributeError):
        document.extraction.warnings.append("changed")
    mutable = document.mutable_copy()
    mutable["parsed_views"]["additional_state"]["moon"] = "full"
    assert document.parsed_views["additional_state"]["moon"] is None
    values = document.source.mutable_values()
    values["additional_state"] = "changed"
    assert document.source.values["additional_state"] == '{"moon":null}'


def _world_document(manager, reader):
    document = reader.world_state()[0]
    return document, document.document_id


def test_integrity_requires_owner_and_subject_identity_agreement(tmp_path):
    manager, reader = populated_campaign(tmp_path)
    document, document_id = _world_document(manager, reader)
    assert reader.verify(document_id).valid
    conn = manager._get_connection()
    conn.execute("UPDATE state_documents SET metadata_json='{}' WHERE id=?", (document_id,)); conn.commit(); conn.close()
    assert "owner-mismatch" in reader.verify(document_id).finding_codes

    manager, reader = populated_campaign(tmp_path / "subject")
    document, document_id = _world_document(manager, reader)
    conn = manager._get_connection()
    conn.execute("UPDATE state_documents SET subject_type='legacy-tampered-row' WHERE id=?", (document_id,)); conn.commit(); conn.close()
    verification = reader.verify(document_id)
    assert "subject-type-mismatch" in verification.finding_codes and not verification.valid


def test_integrity_requires_extractor_revision_and_completed_exact_run(tmp_path):
    manager, reader = populated_campaign(tmp_path)
    document, document_id = _world_document(manager, reader)
    conn = manager._get_connection()
    raw = json.loads(conn.execute("SELECT state_json FROM state_documents WHERE id=?", (document_id,)).fetchone()[0])
    raw["extraction"]["extractor_revision"] = "tampered-extractor"
    encoded = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    content_hash = hashlib.sha256(("zero-context-state-v1\0" + encoded).encode()).hexdigest()
    conn.execute("UPDATE state_documents SET state_json=?,content_hash=? WHERE id=?", (encoded, content_hash, document_id))
    conn.execute("UPDATE legacy_extraction_items SET document_content_hash=? WHERE state_document_id=?", (content_hash, document_id))
    conn.commit(); conn.close()
    assert "extractor-revision-mismatch" in reader.verify(document_id).finding_codes

    manager, reader = populated_campaign(tmp_path / "failed")
    document, document_id = _world_document(manager, reader)
    conn = manager._get_connection()
    run_id = conn.execute("SELECT last_run_id FROM legacy_extraction_items WHERE state_document_id=?", (document_id,)).fetchone()[0]
    conn.execute("UPDATE legacy_extraction_runs SET status='failed' WHERE id=?", (run_id,)); conn.commit(); conn.close()
    assert "run-incomplete" in reader.verify(document_id).finding_codes
    conn = manager._get_connection()
    conn.execute("UPDATE legacy_extraction_runs SET status='running' WHERE id=?", (run_id,)); conn.commit(); conn.close()
    assert "run-incomplete" in reader.verify(document_id).finding_codes

    manager, reader = populated_campaign(tmp_path / "parity")
    document, document_id = _world_document(manager, reader)
    conn = manager._get_connection()
    run_id = conn.execute("SELECT last_run_id FROM legacy_extraction_items WHERE state_document_id=?", (document_id,)).fetchone()[0]
    conn.execute("UPDATE legacy_extraction_runs SET status='parity_failed',parity_status='failed' WHERE id=?", (run_id,))
    conn.commit(); conn.close()
    findings = reader.verify(document_id).finding_codes
    assert "run-incomplete" in findings and "run-parity-not-exact" in findings

def test_coordinated_tampering_cannot_redefine_trusted_extractor_constants(tmp_path):
    manager, reader = populated_campaign(tmp_path)
    document = reader.world_state()[0]
    conn = manager._get_connection()
    row = conn.execute("SELECT state_json,metadata_json,content_hash FROM state_documents WHERE id=?", (document.document_id,)).fetchone()
    raw = json.loads(row["state_json"])
    raw["extraction"]["schema_version"] = 800
    raw["extraction"]["extractor_revision"] = "attacker.v1"
    encoded = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    content_hash = hashlib.sha256(("zero-context-state-v1\0" + encoded).encode()).hexdigest()
    run_id = conn.execute("SELECT last_run_id FROM legacy_extraction_items WHERE state_document_id=?", (document.document_id,)).fetchone()[0]
    conn.execute("UPDATE state_documents SET state_json=?,content_hash=?,metadata_json=? WHERE id=?",
                 (encoded, content_hash, json.dumps({"owner":"attacker.v1"}), document.document_id))
    conn.execute("UPDATE legacy_extraction_items SET extraction_schema_version=800,extractor_revision='attacker.v1',document_content_hash=? WHERE state_document_id=?",
                 (content_hash, document.document_id))
    conn.execute("UPDATE legacy_extraction_runs SET extraction_schema_version=800,extractor_revision='attacker.v1' WHERE id=?", (run_id,))
    conn.commit(); conn.close()
    verification = reader.verify(document.document_id)
    assert not verification.valid
    assert {"trusted-owner-mismatch", "trusted-schema-version-mismatch", "trusted-extractor-revision-mismatch", "item-schema-version-untrusted", "run-schema-version-untrusted"} & set(verification.finding_codes)


def test_integrity_recomputes_deterministic_item_document_subject_and_identity_hashes(tmp_path):
    manager, reader = populated_campaign(tmp_path)
    document = reader.world_state()[0]
    conn = manager._get_connection()
    item = conn.execute("SELECT * FROM legacy_extraction_items WHERE state_document_id=?", (document.document_id,)).fetchone()
    run_id = item["last_run_id"]
    conn.execute("UPDATE legacy_extraction_items SET id=? WHERE id=?", ("not-deterministic-item", item["id"]))
    conn.commit(); conn.close()
    findings = set(reader.verify(document.document_id).finding_codes)
    assert "extraction-item-id-mismatch" in findings

    manager, reader = populated_campaign(tmp_path / "doc")
    document = reader.world_state()[0]
    conn = manager._get_connection()
    conn.execute("PRAGMA foreign_keys=OFF")
    item = conn.execute("SELECT * FROM legacy_extraction_items WHERE state_document_id=?", (document.document_id,)).fetchone()
    conn.execute("UPDATE state_documents SET id=? WHERE id=?", ("not-deterministic-document", document.document_id))
    conn.execute("UPDATE legacy_extraction_items SET state_document_id=? WHERE id=?", ("not-deterministic-document", item["id"]))
    conn.commit(); conn.close()
    findings = set(reader.verify("not-deterministic-document").finding_codes)
    assert "state-document-id-mismatch" in findings

    manager, reader = populated_campaign(tmp_path / "subject")
    document = reader.world_state()[0]
    conn = manager._get_connection()
    conn.execute("UPDATE state_documents SET subject_id='not-deterministic-subject' WHERE id=?", (document.document_id,))
    conn.execute("UPDATE legacy_extraction_items SET subject_id='not-deterministic-subject' WHERE state_document_id=?", (document.document_id,))
    conn.commit(); conn.close()
    findings = set(reader.verify(document.document_id).finding_codes)
    assert "deterministic-subject-id-mismatch" in findings

    manager, reader = populated_campaign(tmp_path / "identity")
    document = reader.world_state()[0]
    conn = manager._get_connection()
    conn.execute("UPDATE legacy_extraction_items SET source_identity_hash='wrong' WHERE state_document_id=?", (document.document_id,))
    conn.commit(); conn.close()
    findings = set(reader.verify(document.document_id).finding_codes)
    assert "source-identity-hash-mismatch" in findings


def test_run_level_integrity_reports_each_normalized_report_corruption(tmp_path):
    corruptions = [
        ("exact", "UPDATE legacy_extraction_runs SET report_json=json_set(report_json,'$.exact',json('false')) WHERE id=?", "run-report-exact-false"),
        ("malformed", "UPDATE legacy_extraction_runs SET report_json='{' WHERE id=?", "run-report-invalid"),
        ("source_root_report", "UPDATE legacy_extraction_runs SET report_json=json_set(report_json,'$.source_root_hash','wrong') WHERE id=?", "run-report-source-root-mismatch"),
        ("document_root_report", "UPDATE legacy_extraction_runs SET report_json=json_set(report_json,'$.document_root_hash','wrong') WHERE id=?", "run-report-document-root-mismatch"),
        ("root_column", "UPDATE legacy_extraction_runs SET document_root_hash='wrong' WHERE id=?", "run-root-parity-mismatch"),
        ("source_count", "UPDATE legacy_extraction_runs SET source_row_count=999 WHERE id=?", "run-source-row-count-mismatch"),
        ("document_count", "UPDATE legacy_extraction_runs SET document_count=999 WHERE id=?", "run-document-count-mismatch"),
        ("quarantine_count", "UPDATE legacy_extraction_runs SET quarantine_count=999 WHERE id=?", "run-quarantine-count-mismatch"),
        ("running", "UPDATE legacy_extraction_runs SET status='running' WHERE id=?", "run-incomplete"),
        ("failed", "UPDATE legacy_extraction_runs SET status='failed' WHERE id=?", "run-incomplete"),
        ("parity", "UPDATE legacy_extraction_runs SET parity_status='failed' WHERE id=?", "run-parity-not-exact"),
        ("schema", "UPDATE legacy_extraction_runs SET extraction_schema_version=800 WHERE id=?", "run-schema-version-untrusted"),
        ("extractor", "UPDATE legacy_extraction_runs SET extractor_revision='attacker.v1' WHERE id=?", "run-extractor-revision-untrusted"),
    ]
    for name, sql, code in corruptions:
        manager, reader = populated_campaign(tmp_path / name)
        document = reader.world_state()[0]
        conn = manager._get_connection()
        run_id = conn.execute("SELECT last_run_id FROM legacy_extraction_items WHERE state_document_id=?", (document.document_id,)).fetchone()[0]
        conn.execute(sql, (run_id,)); conn.commit(); conn.close()
        assert code in reader.verify(document.document_id).finding_codes
    manager, reader = populated_campaign(tmp_path / "valid-run")
    assert reader.verify(reader.world_state()[0].document_id).valid
