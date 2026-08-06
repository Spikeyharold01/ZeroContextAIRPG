from proxy_server.services.alias_matcher import match_aliases
from proxy_server.services.context_retrieval import retrieve_context
from proxy_server.models import AuthorityLevel, ContextCandidate
from proxy_server.services.budget import approximate_token_count
from proxy_server.services.prompt_builder import PromptLimits, build_prompt, prompt_hash


def test_alias_matching_is_normalized_bounded_longest_and_excludes_pronouns():
    aliases = [
        {"alias": "Tan", "subject_type": "narrative.entity", "subject_id": "1", "canonical": False},
        {"alias": "Tanis", "subject_type": "narrative.entity", "subject_id": "2", "canonical": True},
        {"alias": "I", "subject_type": "narrative.entity", "subject_id": "3", "canonical": True},
    ]
    result = match_aliases("I call TANIS; not a tantamount claim.", aliases)
    assert [(item.alias, item.subject_id) for item in result] == [("tanis", "2")]


def test_ambiguous_alias_is_retained_with_stable_order():
    aliases = [{"alias": "Guard", "subject_type": "narrative.entity", "subject_id": value, "canonical": False}
               for value in ("b", "a")]
    result = match_aliases("Guard!", aliases)
    assert [item.subject_id for item in result] == ["a", "b"]
    assert all(item.ambiguous for item in result)


def test_context_preserves_raw_named_message_and_retrieves_relevant_fact(rules_free_campaign):
    raw = "I say to Tanis, “You get the horses and I’ll scout ahead for goblins.”"
    context = retrieve_context(str(rules_free_campaign.database_path), rules_free_campaign.campaign_id, raw, "req")
    assert context.alias_matches[0].subject_id == "2"
    assert [fact["id"] for fact in context.facts] == ["horses"]
    prompt = build_prompt(context, raw, "req")
    assert raw in prompt
    assert "attempted, or claimed" in prompt
    assert "retrieval hints are relevance candidates" in prompt.casefold()


def test_previous_speaker_hint_and_deterministic_prompt(rules_free_campaign):
    context = retrieve_context(str(rules_free_campaign.database_path), rules_free_campaign.campaign_id,
                               "Hey, look… I’m just kidding.", "req")
    assert context.addressee_hint["basis"] == "previous_speaker"
    assert context.addressee_hint["candidate_entity_ids"] == ["2"]
    first = build_prompt(context, "Hey, look… I’m just kidding.", "req", PromptLimits(4096))
    second = build_prompt(context, "Hey, look… I’m just kidding.", "req", PromptLimits(4096))
    assert first == second


def test_movement_context_contains_current_and_connected_locations(rules_free_campaign):
    raw = "I head south to the village."
    context = retrieve_context(str(rules_free_campaign.database_path), rules_free_campaign.campaign_id, raw, "req")
    prompt = build_prompt(context, raw, "req")
    assert context.location_name == "Crossroads"
    assert "South Village" in prompt
    assert "journey succeeds" not in prompt


def test_positional_longest_match_only_suppresses_overlapping_occurrence():
    aliases = [{"alias":"Ann","subject_type":"narrative.entity","subject_id":"1","canonical":True},
               {"alias":"Ann Marie","subject_type":"narrative.entity","subject_id":"2","canonical":True}]
    result = match_aliases("Ann met Ann Marie.", aliases)
    assert [(item.alias, item.subject_id) for item in result] == [("ann", "1"), ("ann marie", "2")]


def test_fuzzy_lexical_retrieval_uses_complete_raw_message_without_alias(rules_free_campaign):
    raw = "Goblin tracks were seen south near where the horses are kept."
    context = retrieve_context(str(rules_free_campaign.database_path), rules_free_campaign.campaign_id, raw, "req")
    assert context.lexical_query == raw
    assert context.retrieval_mode == "relational_lexical"
    assert context.semantic_retrieval == "unavailable"
    fact = next(candidate for candidate in context.candidates if candidate.source_type == "fact")
    assert fact.fuzzy_lexical_score > 0 and fact.semantic_score is None
    assert fact.content["id"] == "horses"


def test_authority_ranking_and_prompt_hash_are_reproducible(rules_free_campaign):
    raw = "I ask Tanis about horses."
    first = retrieve_context(str(rules_free_campaign.database_path), rules_free_campaign.campaign_id, raw, "req")
    second = retrieve_context(str(rules_free_campaign.database_path), rules_free_campaign.campaign_id, raw, "req")
    assert [item.source_id for item in first.candidates] == [item.source_id for item in second.candidates]
    assert all(first.candidates[index].rank_key() <= first.candidates[index + 1].rank_key()
               for index in range(len(first.candidates) - 1))
    one = build_prompt(first, raw, "req"); two = build_prompt(second, raw, "req")
    assert one == two and prompt_hash(one) == prompt_hash(two)


def test_item_level_eviction_removes_low_fact_before_high_fact(rules_free_campaign):
    context = retrieve_context(str(rules_free_campaign.database_path), rules_free_campaign.campaign_id, "Hello", "req")
    context.candidates = []
    mandatory = build_prompt(context, "Hello", "req", PromptLimits(10000))
    low = ContextCandidate("fact", "low", AuthorityLevel.ACTIVE_FACT, None, None,
                           {"text":"LOW-FACT-" + "x" * 300}, "fuzzy_lexical_fact", fuzzy_lexical_score=.1)
    high = ContextCandidate("fact", "high", AuthorityLevel.ACTIVE_FACT, None, None,
                            {"text":"HIGH-FACT-" + "y" * 80}, "fuzzy_lexical_fact", fuzzy_lexical_score=.9)
    context.candidates = [low, high]
    budget = approximate_token_count(mandatory) + 45
    prompt = build_prompt(context, "Hello", "req", PromptLimits(budget))
    assert "LOW-FACT" not in prompt and "HIGH-FACT" in prompt
    assert approximate_token_count(prompt) <= budget and "## RAW USER MESSAGE\nHello" in prompt


def test_mandatory_only_overflow_is_controlled(rules_free_campaign):
    context = retrieve_context(str(rules_free_campaign.database_path), rules_free_campaign.campaign_id, "Hello", "req")
    context.candidates = []; context.memory = None; context.dialogue = None; context.story_time = None
    import pytest
    from proxy_server.errors import TurnError
    with pytest.raises(TurnError) as captured:
        build_prompt(context, "Hello", "req", PromptLimits(1))
    assert captured.value.code == "context_budget_exceeded"


def test_scene_participant_source_precedence(rules_free_campaign):
    import json, sqlite3
    from database.state_repository import _hash
    state = {"participant_ids": ["1", "2"]}
    encoded = json.dumps(state, sort_keys=True, separators=(",", ":"))
    conn = sqlite3.connect(rules_free_campaign.database_path)
    conn.execute("INSERT INTO state_documents(id,campaign_id,namespace,subject_type,subject_id,state_json,revision,content_hash) "
                 "VALUES('scene-members',?,'narrative.scene','narrative.scene','scene-crossroads',?,1,?)",
                 (rules_free_campaign.campaign_id, encoded, _hash(encoded)))
    conn.commit(); conn.close()
    context = retrieve_context(str(rules_free_campaign.database_path), rules_free_campaign.campaign_id, "Hello", "req")
    assert context.participant_basis == "authoritative_generic_scene"
    assert [item["id"] for item in context.participants] == ["1", "2"]
    assert all(item.get("basis") == "co_location_hint" for item in context.inferred_participants)


def test_alias_and_candidate_queries_are_database_bounded(rules_free_campaign):
    import sqlite3
    from proxy_server.services.context_retrieval import MAX_ALIAS_SOURCE_ROWS, MAX_FACT_ROWS, MAX_STATE_ROWS
    conn = sqlite3.connect(rules_free_campaign.database_path)
    conn.executemany("INSERT INTO characters(name,type,status,is_active,current_location_id) VALUES(?, 'NPC','active',1,2)",
                     [(f"Remote {index:03d}",) for index in range(MAX_ALIAS_SOURCE_ROWS + 25)])
    conn.commit(); conn.close()
    context = retrieve_context(str(rules_free_campaign.database_path), rules_free_campaign.campaign_id,
                               "No registered alias appears here", "req")
    assert len(context.alias_matches) <= 20
    assert len([item for item in context.candidates if item.source_type == "fact"]) <= MAX_FACT_ROWS
    assert len([item for item in context.candidates if item.source_type == "generic_state"]) <= MAX_STATE_ROWS


def test_required_core_state_bypasses_optional_document_limit(rules_free_campaign):
    import json, sqlite3
    from database.state_repository import _hash
    from proxy_server.services.context_retrieval import MAX_STATE_ROWS

    conn = sqlite3.connect(rules_free_campaign.database_path)
    required = [
        ("story-time", "narrative.time", "narrative.campaign", rules_free_campaign.campaign_id,
         {"label": "Dawn"}, 4),
        ("scene-core", "narrative.scene", "narrative.scene", "scene-crossroads",
         {"participant_ids": ["1", "2"]}, 5),
    ]
    for document_id, namespace, subject_type, subject_id, state, revision in required:
        encoded = json.dumps(state, sort_keys=True, separators=(",", ":"))
        conn.execute("INSERT INTO state_documents(id,campaign_id,namespace,subject_type,subject_id,state_json,revision,content_hash) "
                     "VALUES(?,?,?,?,?,?,?,?)", (document_id, rules_free_campaign.campaign_id, namespace,
                     subject_type, subject_id, encoded, revision, _hash(encoded)))
    conn.execute("UPDATE state_documents SET revision=6 WHERE id='memory'")
    conn.execute("UPDATE state_documents SET revision=7 WHERE id='dialogue'")
    for index in range(MAX_STATE_ROWS + 20):
        state = json.dumps({"aliases": [f"Alias {index}"]}, separators=(",", ":"))
        conn.execute("INSERT INTO state_documents(id,campaign_id,namespace,subject_type,subject_id,state_json,revision,content_hash) "
                     "VALUES(?,?,?,?,?,?,1,?)", (f"many-aliases-{index:03d}", rules_free_campaign.campaign_id,
                     "narrative.aliases", "narrative.entity", f"optional-{index:03d}", state, _hash(state)))
    conn.commit(); conn.close()

    context = retrieve_context(str(rules_free_campaign.database_path), rules_free_campaign.campaign_id, "Hello", "req")
    assert context.memory == {"summary": "The party paused at the crossroads."}
    assert context.dialogue["last_speaker_id"] == "2"
    assert context.story_time == {"label": "Dawn"}
    assert context.scene_state == {"participant_ids": ["1", "2"]}
    assert context.revisions[("narrative.memory", "narrative.entity", "1")] == 6
    assert context.revisions[("narrative.dialogue", "narrative.scene", "scene-crossroads")] == 7
    assert context.revisions[("narrative.time", "narrative.campaign", rules_free_campaign.campaign_id)] == 4
    assert context.revisions[("narrative.scene", "narrative.scene", "scene-crossroads")] == 5
    assert len([item for item in context.candidates if item.source_type == "generic_state"]) <= MAX_STATE_ROWS

    from proxy_server.services.conversation_turn_service import ConversationTurnService
    from proxy_server.services.storyteller import DeterministicMockStoryteller
    from contracts.openai import NonStreamingChatCompletionRequest
    request = NonStreamingChatCompletionRequest.model_validate(
        {"model": "mock", "messages": [{"role": "user", "content": "Hello"}], "stream": False})
    ConversationTurnService(rules_free_campaign, DeterministicMockStoryteller()).complete(request, "core-revision")
    conn = sqlite3.connect(rules_free_campaign.database_path)
    assert conn.execute("SELECT revision FROM state_documents WHERE id='memory'").fetchone()[0] == 7
    conn.close()


def test_direct_authoritative_state_outlives_unrelated_state_and_hints(rules_free_campaign):
    context = retrieve_context(str(rules_free_campaign.database_path), rules_free_campaign.campaign_id, "Hello", "req")
    context.candidates = [
        ContextCandidate("generic_state", "unrelated", AuthorityLevel.GENERIC_AUTHORITATIVE,
                         "narrative.entity", "remote", {"marker": "DROP-UNRELATED-" + "x" * 300},
                         "generic_state"),
        ContextCandidate("retrieval_hint", "hint", AuthorityLevel.RETRIEVAL_HINT,
                         "narrative.entity", "maybe", {"marker": "DROP-HINT-" + "h" * 120},
                         "ambiguous_alias"),
        ContextCandidate("generic_state", "direct", AuthorityLevel.GENERIC_AUTHORITATIVE,
                         "narrative.scene", context.scene_id, {"marker": "KEEP-DIRECT-" + "d" * 80},
                         "direct_generic_state", current_scene_relevance=1.0),
    ]
    full = build_prompt(context, "Hello", "req", PromptLimits(10000))
    budget = approximate_token_count(full) - 90
    prompt = build_prompt(context, "Hello", "req", PromptLimits(budget))
    assert "DROP-UNRELATED" not in prompt and "KEEP-DIRECT" in prompt
    assert approximate_token_count(prompt) <= budget
    assert '"memory"' in prompt and '"dialogue"' in prompt
    assert prompt_hash(prompt) == prompt_hash(build_prompt(context, "Hello", "req", PromptLimits(budget)))


def test_unrelated_paraphrase_is_not_claimed_as_semantic_retrieval(rules_free_campaign):
    context = retrieve_context(str(rules_free_campaign.database_path), rules_free_campaign.campaign_id,
                               "Celestial harmonies illuminate abstract philosophy.", "req")
    assert context.semantic_retrieval == "unavailable"
    assert all(candidate.semantic_score is None for candidate in context.candidates)
