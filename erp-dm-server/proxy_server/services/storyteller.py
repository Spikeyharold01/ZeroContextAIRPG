"""Provider-neutral storyteller dependency and deterministic Stage 2B mock."""

import json
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from proxy_server.models import TurnContext


class StorytellerProtocol(Protocol):
    def tell(self, prompt: str, raw_user_message: str, context: TurnContext) -> str: ...


class DeterministicMockStoryteller:
    """Stateless raw-text mock; reserved triggers are intentionally test-only."""

    def _patch(self, context, raw, namespace, subject_type, subject_id, path, value):
        revision = context.revisions.get((namespace, subject_type, subject_id), 0)
        key = uuid5(NAMESPACE_URL, f"stage2b:{context.campaign_id}:{context.snapshot_conversation_turn}:{raw}:{namespace}:{subject_type}:{subject_id}")
        return {"target": {"namespace": namespace, "subject_type": subject_type, "subject_id": subject_id},
                "base_revision": revision, "operations": [{"op": "set", "path": path, "value": value}],
                "idempotency_key": str(key)}

    def tell(self, prompt: str, raw_user_message: str, context: TurnContext) -> str:
        if "__TEST_TRIGGER_EXCEPTION__" in raw_user_message:
            raise RuntimeError("deterministic storyteller exception")
        if "__TEST_TRIGGER_TIMEOUT__" in raw_user_message:
            raise TimeoutError("deterministic storyteller timeout")
        narrative = "The storyteller accepts the moment and lets the world respond."
        if "__TEST_TRIGGER_DIALOGUE_LAST_SPEAKER__" in raw_user_message:
            narrative = "The previous speaker recognizes the reply and answers carefully."
        elif "__TEST_TRIGGER_AMBIGUOUS_ADDRESSEE__" in raw_user_message:
            narrative = "Several listeners exchange uncertain glances, unsure who was addressed."
        elif "__TEST_TRIGGER_MOVE_BLOCKED__" in raw_user_message:
            narrative = "The route south is blocked, and the attempted journey stops here."
        elif "__TEST_TRIGGER_MOVE_SUCCEEDS__" in raw_user_message:
            narrative = "The journey succeeds, carrying you onward toward the village."
        elif "__TEST_TRIGGER_STORY_TIME_SLEEP__" in raw_user_message:
            narrative = "Rest comes, and eight uncertain hours pass before waking."
        elif "__TEST_TRIGGER_STORY_TIME_TRAVEL__" in raw_user_message:
            narrative = "The long journey succeeds; six months pass before the capital appears."
        elif "__TEST_TRIGGER_STORY_TIME_UNCERTAIN__" in raw_user_message:
            narrative = "Time passes without a reliable measure."

        dialogue_subject = context.scene_id or context.campaign_id
        patches = [
            self._patch(context, raw_user_message, "narrative.memory", "narrative.entity", context.player_id,
                        ["summary"], narrative[:500]),
            self._patch(context, raw_user_message, "narrative.dialogue", "narrative.scene", dialogue_subject,
                        ["last_dialogue_conversation_turn"], context.snapshot_conversation_turn + 1),
        ]
        if "__TEST_TRIGGER_STORY_TIME_SLEEP__" in raw_user_message:
            patches.append(self._patch(context, raw_user_message, "narrative.time", "narrative.campaign",
                                       context.campaign_id, ["elapsed_duration"],
                                       {"value": 8, "unit": "hours", "precision": "resolved"}))
        elif "__TEST_TRIGGER_STORY_TIME_TRAVEL__" in raw_user_message:
            patches.append(self._patch(context, raw_user_message, "narrative.time", "narrative.campaign",
                                       context.campaign_id, ["elapsed_duration"],
                                       {"value": 6, "unit": "months", "precision": "resolved"}))
        elif "__TEST_TRIGGER_STORY_TIME_UNCERTAIN__" in raw_user_message:
            patches.append(self._patch(context, raw_user_message, "narrative.time", "narrative.campaign",
                                       context.campaign_id, ["uncertainty"], "unknown narrative duration"))
        if "__TEST_TRIGGER_MOVE_SUCCEEDS__" in raw_user_message:
            destinations = sorted(match.subject_id for match in context.alias_matches
                                  if match.subject_type == "narrative.location"
                                  and match.subject_id != context.location_id)
            destination = destinations[0] if destinations else "2"
            patches.append(self._patch(context, raw_user_message, "narrative.location", "narrative.entity",
                                       context.player_id, ["current_location_id"], destination))

        output = {"narrative": narrative, "state_update": {"schema_version": 1,
                  "emotional_shifts": [], "conversational_facts": [], "major_events": [],
                  "state_patches": patches, "scene_operations": []}}
        if "__TEST_TRIGGER_SCHEMA_FAILURE__" in raw_user_message:
            output["narrative"] = 7
        if "__TEST_TRIGGER_INVALID_PATCH_TARGET__" in raw_user_message:
            output["state_update"]["state_patches"][0]["target"]["namespace"] = "narrative.forbidden"
        payload = json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if "__TEST_TRIGGER_MALFORMED_JSON__" in raw_user_message:
            payload = payload.replace('"schema_version":1,', '"schema_version":1', 1)
        return f"{narrative}\n{START}\n{payload}\n{END}"


from .structured_tail import END, START  # constants only; injection remains application-owned
