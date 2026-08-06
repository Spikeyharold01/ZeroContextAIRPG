"""Bounded, deterministic, authority-aware campaign context retrieval."""

import hashlib
import json
import math
import sqlite3
from difflib import SequenceMatcher

from database.db_manager import cosine_similarity
from proxy_server.errors import TurnError
from proxy_server.models import AuthorityLevel, ContextCandidate, TurnContext
from .alias_matcher import match_aliases, normalize
from .dialogue_focus import derive_addressee_hint


APPROVED_NAMESPACES = ("narrative.aliases", "narrative.dialogue", "narrative.memory",
                       "narrative.time", "narrative.world", "narrative.location", "narrative.scene")
MAX_STATE_ROWS = 100
MAX_ALIAS_SOURCE_ROWS = 80
MAX_FACT_ROWS = 50
MAX_EVENT_ROWS = 20


def _fuzzy_lexical_vector(text: str, dimensions: int = 64) -> list[float]:
    """Deterministic character-trigram vector for fuzzy lexical similarity."""
    value = f"  {normalize(text)}  "
    vector = [0.0] * dimensions
    for index in range(max(1, len(value) - 2)):
        gram = value[index:index + 3].encode("utf-8")
        bucket = int.from_bytes(hashlib.sha256(gram).digest()[:4], "big") % dimensions
        vector[bucket] += 1.0
    magnitude = math.sqrt(sum(item * item for item in vector)) or 1.0
    return [item / magnitude for item in vector]


def _required_state(conn, campaign_id: str, namespace: str, subject_type: str,
                    subject_id: str) -> tuple[dict | None, int, str | None]:
    """Read one required document directly, independently of optional limits."""
    row = conn.execute(
        "SELECT id,state_json,revision FROM state_documents WHERE campaign_id=? AND namespace=? "
        "AND subject_type=? AND subject_id=? AND lifecycle_status='active' ORDER BY id LIMIT 1",
        (campaign_id, namespace, subject_type, subject_id),
    ).fetchone()
    if row is None:
        return None, 0, None
    return json.loads(row["state_json"]), row["revision"], row["id"]


def _load_entities(conn, ids: list[str], limit: int) -> list[dict[str, str]]:
    numeric = sorted({int(value) for value in ids if str(value).isdigit()})[:limit]
    if not numeric:
        return []
    placeholders = ",".join("?" for _ in numeric)
    rows = conn.execute(
        f"SELECT id,name,type FROM characters WHERE id IN ({placeholders}) AND is_active=1 "
        "AND status='active' ORDER BY id LIMIT ?", (*numeric, limit)
    ).fetchall()
    return [{"id": str(row["id"]), "name": row["name"], "type": row["type"]} for row in rows]


def retrieve_context(db_path: str, campaign_id: str, raw_message: str, request_id: str,
                     *, history_limit: int = 12, candidate_limit: int = 20) -> TurnContext:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        campaign = conn.execute(
            "SELECT id,display_name,active_scene_id,current_turn FROM campaigns "
            "WHERE id=? AND lifecycle_status='active'", (campaign_id,)
        ).fetchone()
        if campaign is None:
            raise TurnError("no_active_campaign", "No active campaign is available.", request_id, False, 409)
        players = conn.execute(
            "SELECT id,name,current_location_id FROM characters WHERE type='PC' AND is_active=1 "
            "AND status='active' ORDER BY id LIMIT 2"
        ).fetchall()
        if not players:
            raise TurnError("no_active_player_entity", "No active player entity is available.", request_id, False, 409)
        if len(players) != 1:
            raise TurnError("ambiguous_player_entity", "More than one active player entity is available.", request_id, False, 409)
        player = players[0]
        player_id = str(player["id"])
        location = conn.execute(
            "SELECT id,name,region,description FROM locations WHERE id=? ORDER BY id LIMIT 1",
            (player["current_location_id"],)
        ).fetchone()
        scene_id = campaign["active_scene_id"]
        dialogue_subject = scene_id or campaign_id
        dialogue, dialogue_rev, _dialogue_doc_id = _required_state(
            conn, campaign_id, "narrative.dialogue", "narrative.scene", dialogue_subject)
        memory, memory_rev, _memory_doc_id = _required_state(
            conn, campaign_id, "narrative.memory", "narrative.entity", player_id)
        story_time, time_rev, _time_doc_id = _required_state(
            conn, campaign_id, "narrative.time", "narrative.campaign", campaign_id)
        scene_state, scene_rev, _scene_doc_id = _required_state(
            conn, campaign_id, "narrative.scene", "narrative.scene", dialogue_subject)
        # Required state above never competes with this independently bounded optional scan.
        state_rows = conn.execute(
            "SELECT namespace,subject_type,subject_id,state_json,revision,id FROM state_documents "
            "WHERE campaign_id=? AND lifecycle_status='active' AND namespace IN (?,?,?,?,?,?,?) "
            "AND namespace NOT IN ('narrative.dialogue','narrative.memory','narrative.time') "
            "AND NOT (namespace='narrative.scene' AND subject_type='narrative.scene' AND subject_id=?) "
            "ORDER BY namespace,subject_type,subject_id,id LIMIT ?",
            (campaign_id, *APPROVED_NAMESPACES, dialogue_subject, MAX_STATE_ROWS),
        ).fetchall()
        participant_ids: list[str] = []
        participant_basis = "none"
        if scene_state and isinstance(scene_state.get("participant_ids"), list):
            participant_ids = [str(value) for value in scene_state["participant_ids"]]
            participant_basis = "authoritative_generic_scene"
        elif location:
            graph = conn.execute(
                "SELECT npc_present FROM scene_graph WHERE location_id=? AND npc_present IS NOT NULL "
                "ORDER BY id DESC LIMIT 1", (location["id"],)
            ).fetchone()
            if graph:
                parsed = json.loads(graph["npc_present"])
                if isinstance(parsed, list):
                    participant_ids = [player_id, *(str(value) for value in parsed)]
                    participant_basis = "explicit_legacy_scene"
        if not participant_ids and dialogue and isinstance(dialogue.get("scene_participant_ids"), list):
            participant_ids = [str(value) for value in dialogue["scene_participant_ids"]]
            participant_basis = "dialogue_focus"
        participants = _load_entities(conn, participant_ids, candidate_limit)
        inferred_rows = conn.execute(
            "SELECT id,name,type FROM characters WHERE is_active=1 AND status='active' "
            "AND current_location_id IS ? ORDER BY id LIMIT ?", (player["current_location_id"], candidate_limit)
        ).fetchall() if player["current_location_id"] is not None else []
        inferred = [{"id": str(row["id"]), "name": row["name"], "type": row["type"],
                     "basis": "co_location_hint"} for row in inferred_rows
                    if str(row["id"]) not in {item["id"] for item in participants}]

        canonical_rows = conn.execute(
            "SELECT id,name,current_location_id,type FROM characters WHERE is_active=1 AND status='active' "
            "ORDER BY CASE WHEN current_location_id IS ? THEN 0 ELSE 1 END,name,id LIMIT ?",
            (player["current_location_id"], MAX_ALIAS_SOURCE_ROWS),
        ).fetchall()
        aliases = [{"alias": row["name"], "subject_type": "narrative.entity", "subject_id": str(row["id"]),
                    "canonical": True, "in_scene": str(row["id"]) in {p["id"] for p in participants},
                    "at_location": row["current_location_id"] == player["current_location_id"]}
                   for row in canonical_rows]
        location_rows = conn.execute(
            "SELECT id,name FROM locations ORDER BY CASE WHEN id=? THEN 0 ELSE 1 END,name,id LIMIT ?",
            (player["current_location_id"], MAX_ALIAS_SOURCE_ROWS),
        ).fetchall()
        aliases.extend({"alias": row["name"], "subject_type": "narrative.location",
                        "subject_id": str(row["id"]), "canonical": True,
                        "at_location": row["id"] == player["current_location_id"]} for row in location_rows)
        for row in state_rows:
            if row["namespace"] == "narrative.aliases":
                for alias in json.loads(row["state_json"]).get("aliases", [])[:candidate_limit]:
                    if isinstance(alias, str):
                        aliases.append({"alias": alias, "subject_type": row["subject_type"],
                                        "subject_id": row["subject_id"], "canonical": False})
        matches = match_aliases(raw_message, aliases, limit=candidate_limit)
        matched_aliases = sorted({item.alias for item in matches}, key=lambda value: (-len(value), value))

        fact_rows = conn.execute(
            "SELECT id,character_id,fact_text,importance,confidence,source_type,created_turn,last_referenced_turn "
            "FROM conversational_facts WHERE is_active=1 AND (expires_at_turn IS NULL OR expires_at_turn>?) "
            "ORDER BY importance DESC,last_referenced_turn DESC,id LIMIT ?",
            (campaign["current_turn"], MAX_FACT_ROWS),
        ).fetchall()
        query_vector = _fuzzy_lexical_vector(raw_message)
        candidates: list[ContextCandidate] = []
        retrieval_order = 0
        for row in fact_rows:
            retrieval_order += 1
            fuzzy_lexical = (cosine_similarity(query_vector, _fuzzy_lexical_vector(row["fact_text"]))
                             + SequenceMatcher(None, normalize(raw_message), normalize(row["fact_text"])).ratio()) / 2
            lexical = next((alias for alias in matched_aliases if alias in normalize(row["fact_text"])), None)
            candidates.append(ContextCandidate(
                "fact", f"{campaign_id}:fact:{row['id']}", AuthorityLevel.ACTIVE_FACT,
                "narrative.entity" if row["character_id"] else None,
                str(row["character_id"]) if row["character_id"] else None,
                {"id": row["id"], "text": row["fact_text"], "confidence": row["confidence"], "source": row["source_type"],
                 "fuzzy_lexical_score": fuzzy_lexical, "retrieval_mode": "relational_lexical"},
                "lexical_fact" if lexical else "fuzzy_lexical_fact", direct_entity_relevance=1.0 if row["character_id"] and str(row["character_id"]) in {m.subject_id for m in matches} else 0.0,
                exact_alias_match=1.0 if lexical else 0.0, fuzzy_lexical_score=fuzzy_lexical,
                lexical_match_type="exact_alias" if lexical else None, importance=float(row["importance"] or 0),
                relevant_conversation_turn=int(row["last_referenced_turn"] or row["created_turn"] or 0),
                retrieval_order=retrieval_order))
        candidates = sorted((item for item in candidates
                             if item.exact_alias_match or item.fuzzy_lexical_score >= 0.40),
                            key=lambda item: item.rank_key())[:candidate_limit]
        facts = [item.content for item in candidates if item.source_type == "fact"]

        event_rows = conn.execute(
            "SELECT id,event_text,event_type,turn,importance,character_id FROM event_log "
            "ORDER BY turn DESC,importance DESC,id DESC LIMIT ?", (MAX_EVENT_ROWS,)
        ).fetchall()
        events = []
        for row in event_rows:
            retrieval_order += 1
            event = dict(row); events.append(event)
            candidates.append(ContextCandidate("event", f"{campaign_id}:event:{row['id']}",
                AuthorityLevel.EVENT_HISTORY, "narrative.entity" if row["character_id"] else None,
                str(row["character_id"]) if row["character_id"] else None, event, "event",
                direct_entity_relevance=1.0 if row["character_id"] and str(row["character_id"]) in {m.subject_id for m in matches} else 0.0,
                importance=float(row["importance"] or 0), relevant_conversation_turn=int(row["turn"] or 0),
                retrieval_order=retrieval_order))
        history_rows = conn.execute(
            "SELECT m.id,m.role,m.content,c.conversation_turn_after FROM conversation_turn_messages m "
            "JOIN conversation_turn_commits c ON c.id=m.conversation_turn_commit_id AND c.campaign_id=m.campaign_id "
            "WHERE m.campaign_id=? ORDER BY c.conversation_turn_after DESC,m.message_index DESC,m.id DESC LIMIT ?",
            (campaign_id, history_limit),
        ).fetchall()
        history = [{"role": row["role"], "content": row["content"]} for row in reversed(history_rows)]
        for row in history_rows:
            retrieval_order += 1
            candidates.append(ContextCandidate("chat", f"{campaign_id}:message:{row['id']}",
                AuthorityLevel.CHAT_HISTORY, None, None, {"role": row["role"], "content": row["content"]},
                "chat_history", relevant_conversation_turn=row["conversation_turn_after"], retrieval_order=retrieval_order))
        for row in state_rows:
            if row["namespace"] in {"narrative.aliases", "narrative.dialogue", "narrative.memory", "narrative.time"}:
                continue
            retrieval_order += 1
            candidates.append(ContextCandidate("generic_state", f"{campaign_id}:state:{row['id']}",
                AuthorityLevel.GENERIC_AUTHORITATIVE, row["subject_type"], row["subject_id"],
                {"namespace": row["namespace"], "state": json.loads(row["state_json"])},
                "direct_generic_state" if (row["subject_id"] in {player_id, dialogue_subject,
                    str(location["id"]) if location else ""} or row["subject_id"] in {p["id"] for p in participants})
                else "generic_state",
                direct_entity_relevance=1.0 if row["subject_id"] == player_id or row["subject_id"] in {p["id"] for p in participants} else 0.0,
                current_scene_relevance=1.0 if row["subject_id"] == dialogue_subject else 0.0,
                current_location_relevance=1.0 if location and row["subject_id"] == str(location["id"]) else 0.0,
                retrieval_order=retrieval_order))
        for match in matches:
            retrieval_order += 1
            candidates.append(ContextCandidate("retrieval_hint", f"{campaign_id}:hint:{match.subject_type}:{match.subject_id}:{match.alias}",
                AuthorityLevel.RETRIEVAL_HINT, match.subject_type, match.subject_id, match.__dict__,
                "ambiguous_alias" if match.ambiguous else "alias_hint", exact_alias_match=1.0,
                current_scene_relevance=1.0 if match.in_scene else 0.0,
                current_location_relevance=1.0 if match.at_location else 0.0, retrieval_order=retrieval_order))
    finally:
        conn.close()

    context = TurnContext(
        campaign_id=campaign_id, campaign_name=campaign["display_name"],
        snapshot_conversation_turn=campaign["current_turn"], player_id=player_id, player_name=player["name"],
        location_id=str(location["id"]) if location else None, location_name=location["name"] if location else None,
        scene_id=scene_id, participants=participants, participant_basis=participant_basis,
        inferred_participants=inferred, alias_matches=matches, dialogue=dialogue, scene_state=scene_state,
        story_time=story_time,
        memory=memory, state=[item.content for item in candidates if item.source_type == "generic_state"],
        facts=facts, events=events, history=history,
        candidates=sorted(candidates, key=lambda item: item.rank_key()), lexical_query=raw_message)
    for namespace, subject_type, subject_id, revision in (
        ("narrative.dialogue", "narrative.scene", dialogue_subject, dialogue_rev),
        ("narrative.memory", "narrative.entity", player_id, memory_rev),
        ("narrative.time", "narrative.campaign", campaign_id, time_rev),
        ("narrative.scene", "narrative.scene", dialogue_subject, scene_rev),
    ):
        context.revisions[(namespace, subject_type, subject_id)] = revision
    for row in state_rows:
        context.revisions[(row["namespace"], row["subject_type"], row["subject_id"])] = row["revision"]
    context.addressee_hint = derive_addressee_hint(player_id, participants + inferred, matches, dialogue)
    return context
