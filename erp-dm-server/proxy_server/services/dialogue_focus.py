"""Deterministic, non-authoritative addressee hints."""


def derive_addressee_hint(player_id: str, participants: list[dict], matches, dialogue: dict | None) -> dict:
    present = [item["id"] for item in participants if item["id"] != player_id]
    named = sorted({item.subject_id for item in matches if item.subject_type == "narrative.entity"})
    if named:
        candidates, basis, confidence = named, "explicit_alias", 1.0
    elif dialogue and dialogue.get("last_speaker_id") in present:
        candidates, basis, confidence = [dialogue["last_speaker_id"]], "previous_speaker", 0.8
    elif dialogue and dialogue.get("conversation_focus_entity_ids"):
        candidates = sorted(set(dialogue["conversation_focus_entity_ids"]) & set(present))
        basis, confidence = "conversation_focus", 0.65
    elif len(present) == 1:
        candidates, basis, confidence = present, "only_other_participant", 0.6
    else:
        candidates, basis, confidence = sorted(present), "ambiguous_participants", 0.25
    return {"candidate_entity_ids": candidates, "basis": basis, "confidence": confidence,
            "ambiguous": len(candidates) != 1, "participants": sorted(present),
            "last_speaker": dialogue.get("last_speaker_id") if dialogue else None,
            "current_focus": dialogue.get("conversation_focus_entity_ids", []) if dialogue else []}
