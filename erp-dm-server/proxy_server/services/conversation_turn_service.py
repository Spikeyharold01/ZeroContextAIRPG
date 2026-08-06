"""Synchronous orchestration and atomic accepted conversation exchange commit."""

import hashlib
import json
import logging
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid4, uuid5

from contracts.openai import (AssistantResponseMessage, ChatCompletionResponse,
                              CompletionChoice, NonStreamingChatCompletionRequest, TokenUsage)
from contracts.state import StatePatchConflict, canonical_json
from contracts.storyteller import StorytellerOutput
from database.state_repository import (StateAuthorizationError, StateBusyError,
                                       StateIdempotencyConflict, StatePersistenceError,
                                       TrustedStatePolicy)
from structured_output import StructuredOutputPolicy, validate_structured_output

from proxy_server.errors import TurnError
from proxy_server.models import (AuthorityLevel, ContextCandidate, RequestReservation,
                                 ReservationOutcome)
from .budget import approximate_token_count
from .context_retrieval import retrieve_context
from .prompt_builder import PromptLimits, build_prompt
from .storyteller import StorytellerProtocol
from .structured_tail import extract_tail


_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
logger = logging.getLogger(__name__)


def _hash(domain: str, value: str) -> str:
    return hashlib.sha256((domain + "\0" + value).encode("utf-8")).hexdigest()


def canonical_request_hash(request: NonStreamingChatCompletionRequest) -> str:
    return _hash("zero-context-chat-request-v1", canonical_json(request.model_dump(mode="json")))


class ConversationTurnService:
    def __init__(self, session, storyteller: StorytellerProtocol, *, prompt_limits: PromptLimits | None = None,
                 lease_seconds: int = 30, clock=None, failure_injector=None):
        self.session = session
        self.storyteller = storyteller
        self.prompt_limits = prompt_limits or PromptLimits()
        self.lease_seconds = lease_seconds
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.failure_injector = failure_injector

    def complete(self, request: NonStreamingChatCompletionRequest,
                 idempotency_header: str | None = None) -> ChatCompletionResponse:
        request_id = uuid4().hex
        try:
            self.session.ensure_active()
        except Exception as error:
            raise TurnError("closed_session", "The active campaign session is closed.", request_id, False, 409) from error
        final_user_index = max(index for index, message in enumerate(request.messages) if message.role == "user")
        raw_user_message = request.messages[final_user_index].content
        request_hash = canonical_request_hash(request)
        try:
            context = retrieve_context(str(self.session.database_path), self.session.campaign_id,
                                       raw_user_message, request_id,
                                       history_limit=self.prompt_limits.history_messages)
        except TurnError:
            raise
        except sqlite3.OperationalError as error:
            code = "database_busy" if "locked" in str(error).lower() else "unresolved_campaign_context"
            raise TurnError(code, "Campaign context is temporarily unavailable.", request_id,
                            code == "database_busy", 503 if code == "database_busy" else 500) from error
        except (ValueError, TypeError) as error:
            raise TurnError("unresolved_campaign_context", "Stored campaign context is invalid.",
                            request_id, False, 500) from error
        except Exception as error:
            raise TurnError("unresolved_campaign_context", "Campaign context could not be retrieved.",
                            request_id, False, 500) from error
        # Earlier request messages are conversation context only. The final user
        # message remains the unchanged current input and is persisted once.
        current_messages = [{"role": item.role, "content": item.content}
                            for index, item in enumerate(request.messages) if index != final_user_index]
        context.history.extend(current_messages)
        for index, item in enumerate(current_messages):
            context.candidates.append(ContextCandidate(
                "chat", f"{context.campaign_id}:request-message:{index}", AuthorityLevel.CHAT_HISTORY,
                None, None, item, "chat_history",
                relevant_conversation_turn=context.snapshot_conversation_turn, retrieval_order=100000 + index))
        key_supplied = idempotency_header is not None
        idempotency_key = self._key(idempotency_header, context, request_hash, request_id)
        reservation_outcome = self._reserve_or_replay(
            idempotency_key, request_hash, request_id, context, key_supplied)
        if reservation_outcome.replay_json is not None:
            return ChatCompletionResponse.model_validate_json(reservation_outcome.replay_json)
        reservation = reservation_outcome.reservation
        assert reservation is not None
        try:
            prompt = build_prompt(context, raw_user_message, request_id, self.prompt_limits)
            try:
                raw_storyteller = self.storyteller.tell(prompt, raw_user_message, context)
            except TimeoutError as error:
                raise TurnError("storyteller_timeout", "The storyteller timed out.", request_id, True, 504) from error
            except Exception as error:
                raise TurnError("storyteller_error", "The storyteller could not complete the request.", request_id, True, 502) from error
            try:
                tail = extract_tail(raw_storyteller, request_id=request_id,
                    secure_debug_raw_output=self.session.settings.structured_output_recovery.secure_debug_raw_output)
            except ValueError as error:
                raise TurnError("malformed_structured_output", "The storyteller returned malformed structured output.", request_id, False, 502) from error
            policy = StructuredOutputPolicy.from_config(self.session.settings.structured_output_recovery)
            recovery = validate_structured_output(tail.payload, StorytellerOutput, policy)
            logger.debug(
                "structured validation request_id=%s model=StorytellerOutput payload_bytes=%d "
                "payload_hash=%s status=%s repair_attempted=%s repaired_payload_hash=%s error=%s",
                request_id, len(tail.payload.encode("utf-8")), tail.payload_hash, recovery.status,
                recovery.repair_attempted, recovery.repaired_content_hash,
                (recovery.error_summary or "")[:policy.max_error_summary_characters],
            )
            if not recovery.accepted:
                code = "schema_invalid_structured_output" if recovery.failure_category == "schema" else "malformed_structured_output"
                raise TurnError(code, "The storyteller returned invalid structured output.", request_id, False, 502)
            output = recovery.validated_model
            assert isinstance(output, StorytellerOutput)
            self._validate_subset(output, tail.narrative, context, request_id)
            response = self._response(request, prompt, tail.narrative, request_id)
            self._commit(context, request_id, idempotency_key, request_hash, reservation, raw_user_message,
                         tail.payload_hash, tail.narrative, recovery.status, output, response)
            return response
        except TurnError as error:
            try:
                self._mark_failed(idempotency_key, request_hash, request_id, error)
            except Exception as marking_error:
                logger.error("request failure marking failed request_id=%s category=%s",
                             request_id, type(marking_error).__name__)
                try:
                    error.add_note(f"secondary idempotency marking failure: {type(marking_error).__name__}")
                except AttributeError:
                    pass
            raise

    def _key(self, header, context, request_hash, request_id):
        if header is not None:
            value = header.strip()
            if not _KEY.fullmatch(value):
                raise TurnError("invalid_request", "X-Idempotency-Key is invalid.", request_id, False, 400)
            return value
        source = canonical_json({"campaign_id": context.campaign_id,
                                 "snapshot_conversation_turn": context.snapshot_conversation_turn,
                                 "active_player_entity_id": context.player_id, "request_hash": request_hash})
        return "fallback-" + _hash("zero-context-fallback-idempotency-v1", source)

    def _connect(self):
        conn = sqlite3.connect(str(self.session.database_path), timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _reserve_or_replay(self, key, request_hash, request_id, context, key_supplied):
        conn = self._connect()
        now = self.clock()
        now_text = now.isoformat()
        lease_text = (now + timedelta(seconds=self.lease_seconds)).isoformat()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM conversation_turn_requests WHERE campaign_id=? AND idempotency_key=?",
                               (context.campaign_id, key)).fetchone()
            if row is not None:
                if row["request_hash"] != request_hash:
                    raise TurnError("duplicate_request_conflict", "The idempotency key was used for a different request.", request_id, False, 409)
                if row["status"] == "completed":
                    conn.commit()
                    return ReservationOutcome(replay_json=row["response_json"])
                reclaim = False
                if row["status"] == "in_progress":
                    reclaim = not row["lease_expires_at"] or row["lease_expires_at"] <= now_text
                    if not reclaim:
                        raise TurnError("request_in_progress", "The matching request is still in progress.",
                                        request_id, True, 409)
                elif row["status"] == "failed":
                    if not row["retryable"]:
                        raise TurnError("duplicate_request_conflict", "The matching request failed permanently.",
                                        request_id, False, 409)
                    reclaim = True
                if reclaim:
                    changed = conn.execute(
                        "UPDATE conversation_turn_requests SET status='in_progress',attempt_number=attempt_number+1,"
                        "last_request_id=?,lease_expires_at=?,snapshot_conversation_turn=?,active_player_subject_id=?,"
                        "error_code=NULL,retryable=0,completed_at=NULL,updated_at=? "
                        "WHERE id=? AND campaign_id=? AND request_hash=? AND attempt_number=? AND status=?",
                        (request_id, lease_text, context.snapshot_conversation_turn, context.player_id, now_text,
                         row["id"], context.campaign_id, request_hash,
                         row["attempt_number"], row["status"]),
                    ).rowcount
                    if changed != 1:
                        raise TurnError("request_in_progress", "The matching request was reclaimed concurrently.",
                                        request_id, True, 409)
                    conn.commit()
                    return ReservationOutcome(reservation=RequestReservation(
                        request_row_id=row["id"], last_request_id=request_id,
                        attempt_number=row["attempt_number"] + 1))
            request_row_id = str(uuid4())
            conn.execute("INSERT INTO conversation_turn_requests "
                         "(id,campaign_id,idempotency_key,request_hash,request_id,last_request_id,status,attempt_number,"
                         "lease_expires_at,snapshot_conversation_turn,active_player_subject_id) "
                         "VALUES (?,?,?,?,?,?,'in_progress',1,?,?,?)",
                         (request_row_id, context.campaign_id, key, request_hash, request_id, request_id,
                          lease_text, context.snapshot_conversation_turn, context.player_id))
            conn.commit()
            return ReservationOutcome(reservation=RequestReservation(
                request_row_id=request_row_id, last_request_id=request_id, attempt_number=1))
        except TurnError:
            conn.rollback()
            raise
        except sqlite3.OperationalError as error:
            conn.rollback()
            raise TurnError("database_busy", "The campaign database is busy.", request_id, True, 503) from error
        finally:
            conn.close()

    def _validate_subset(self, output, visible, context, request_id):
        update = output.state_update
        if output.narrative != visible:
            raise TurnError("schema_invalid_structured_output", "Visible and validated narratives differ.", request_id, False, 502)
        if update.emotional_shifts or update.conversational_facts or update.major_events or update.scene_operations:
            raise TurnError("schema_invalid_structured_output", "The storyteller proposed unsupported consequences.", request_id, False, 502)
        dialogue_subject = context.scene_id or context.campaign_id
        approved = {("narrative.memory", "narrative.entity", context.player_id),
                    ("narrative.dialogue", "narrative.scene", dialogue_subject),
                    ("narrative.time", "narrative.campaign", context.campaign_id),
                    ("narrative.location", "narrative.entity", context.player_id),
                    ("narrative.world", "narrative.campaign", context.campaign_id)}
        for patch in update.state_patches:
            target = patch.target
            if (target.namespace, target.subject_type, target.subject_id) not in approved:
                raise TurnError("schema_invalid_structured_output", "The storyteller proposed an unapproved state target.", request_id, False, 502)

    def _response(self, request, prompt, narrative, request_id):
        prompt_tokens = approximate_token_count(prompt)
        completion_tokens = approximate_token_count(narrative)
        return ChatCompletionResponse(id=f"chatcmpl-{request_id}", created=int(time.time()), model=request.model,
            choices=[CompletionChoice(index=0, message=AssistantResponseMessage(content=narrative), finish_reason="stop")],
            usage=TokenUsage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                             total_tokens=prompt_tokens + completion_tokens))

    def _commit(self, context, request_id, key, request_hash, reservation, raw_user, output_hash,
                narrative, repair_status, output, response):
        dialogue_subject = context.scene_id or context.campaign_id
        allowed = {"narrative.memory": {"narrative.entity"},
                   "narrative.dialogue": {"narrative.scene"},
                   "narrative.time": {"narrative.campaign"},
                   "narrative.location": {"narrative.entity"},
                   "narrative.world": {"narrative.campaign"}}
        authorized = {(context.player_id, "narrative.entity"), (dialogue_subject, "narrative.scene"),
                      (context.campaign_id, "narrative.campaign")}
        policy = TrustedStatePolicy(allowed, lambda _campaign, kind, subject: (subject, kind) in authorized)
        repository = self.session.create_state_repository(policy)
        conn = repository._connection()
        turn_after = context.snapshot_conversation_turn + 1
        commit_id = str(uuid4())
        try:
            repository._begin_with_retry(conn)
            self.session.ensure_active()
            campaign = conn.execute("SELECT current_turn,lifecycle_status FROM campaigns WHERE id=?", (context.campaign_id,)).fetchone()
            if campaign is None or campaign["lifecycle_status"] != "active":
                raise TurnError("closed_session", "The active campaign session is closed.", request_id, False, 409)
            if campaign["current_turn"] != context.snapshot_conversation_turn:
                raise TurnError("conversation_turn_conflict", "The campaign conversation advanced concurrently.", request_id, True, 409)
            request_row = conn.execute("SELECT id,status,request_hash,last_request_id,attempt_number "
                                       "FROM conversation_turn_requests "
                                       "WHERE campaign_id=? AND idempotency_key=?", (context.campaign_id, key)).fetchone()
            owns_lease = (request_row is not None
                          and request_row["id"] == reservation.request_row_id
                          and request_row["status"] == "in_progress"
                          and request_row["request_hash"] == request_hash
                          and request_row["last_request_id"] == reservation.last_request_id == request_id
                          and request_row["attempt_number"] == reservation.attempt_number)
            if not owns_lease:
                raise TurnError("request_lease_lost", "The request no longer owns its processing lease.",
                                request_id, True, 409)
            for patch in output.state_update.state_patches:
                repository.apply_patch_in_transaction(conn, patch, request_id=request_id,
                    producer_type="core.storyteller", producer_id="deterministic",
                    turn_number=turn_after, failure_injector=self.failure_injector)
                self._inject("first_state_patch")
                namespace = patch.target.namespace
                if namespace == "narrative.time": self._inject("story_time_write")
                elif namespace == "narrative.memory": self._inject("memory_write")
                elif namespace == "narrative.dialogue": self._inject("dialogue_focus_write")
            conn.execute("INSERT INTO conversation_turn_commits "
                         "(id,campaign_id,conversation_turn_request_id,conversation_turn_before,conversation_turn_after,"
                         "active_player_subject_id,storyteller_output_hash,visible_narrative_hash,repair_status) "
                         "VALUES (?,?,?,?,?,?,?,?,?)",
                         (commit_id, context.campaign_id, request_row["id"], context.snapshot_conversation_turn,
                          turn_after, context.player_id, output_hash,
                          _hash("zero-context-visible-narrative-v1", narrative), repair_status))
            self._inject("conversation_turn_commit")
            conn.execute("INSERT INTO conversation_turn_messages "
                         "(id,campaign_id,conversation_turn_commit_id,role,content,message_index,source) VALUES (?,?,?,?,?,?,?)",
                         (str(uuid4()), context.campaign_id, commit_id, "user", raw_user, 0, "http_request"))
            self._inject("user_message")
            conn.execute("INSERT INTO conversation_turn_messages "
                         "(id,campaign_id,conversation_turn_commit_id,role,content,message_index,source) VALUES (?,?,?,?,?,?,?)",
                         (str(uuid4()), context.campaign_id, commit_id, "assistant", narrative, 1, "storyteller_response"))
            self._inject("assistant_message")
            changed = conn.execute("UPDATE campaigns SET current_turn=?,updated_at=CURRENT_TIMESTAMP "
                                   "WHERE id=? AND current_turn=?", (turn_after, context.campaign_id,
                                                                     context.snapshot_conversation_turn)).rowcount
            if changed != 1:
                raise TurnError("conversation_turn_conflict", "The campaign conversation advanced concurrently.", request_id, True, 409)
            self._inject("campaign_conversation_turn")
            completed = conn.execute(
                "UPDATE conversation_turn_requests SET status='completed',response_json=?,retryable=0,lease_expires_at=NULL,"
                "updated_at=CURRENT_TIMESTAMP,completed_at=CURRENT_TIMESTAMP WHERE id=? AND campaign_id=? "
                "AND status='in_progress' AND request_hash=? AND last_request_id=? AND attempt_number=?",
                (canonical_json(response.model_dump(mode="json")), reservation.request_row_id,
                 context.campaign_id, request_hash, reservation.last_request_id,
                 reservation.attempt_number)).rowcount
            if completed != 1:
                raise TurnError("request_lease_lost", "The request no longer owns its processing lease.",
                                request_id, True, 409)
            self._inject("completed_response")
            conn.commit()
        except TurnError:
            conn.rollback()
            raise
        except StatePatchConflict as error:
            conn.rollback()
            raise TurnError("state_patch_conflict", "Campaign state changed concurrently.", request_id, True, 409) from error
        except StateBusyError as error:
            conn.rollback()
            raise TurnError("database_busy", "The campaign database is busy.", request_id, True, 503) from error
        except (StateAuthorizationError, StateIdempotencyConflict, StatePersistenceError) as error:
            conn.rollback()
            raise TurnError("persistence_failure", "The conversation could not be persisted.", request_id, False, 500) from error
        except sqlite3.OperationalError as error:
            conn.rollback()
            code = "database_busy" if "locked" in str(error).lower() else "persistence_failure"
            raise TurnError(code, "The campaign database could not be updated.", request_id,
                            code == "database_busy", 503 if code == "database_busy" else 500) from error
        except Exception as error:
            conn.rollback()
            raise TurnError("persistence_failure", "The conversation could not be persisted.", request_id, False, 500) from error
        finally:
            conn.close()
            repository.close()
            self.session._repositories.discard(repository)

    def _inject(self, point: str) -> None:
        if self.failure_injector is not None:
            self.failure_injector(point)

    def _mark_failed(self, key, request_hash, request_id, error):
        conn = self._connect()
        try:
            conn.execute("UPDATE conversation_turn_requests SET status='failed',error_code=?,retryable=?,"
                         "lease_expires_at=NULL,updated_at=CURRENT_TIMESTAMP,completed_at=CURRENT_TIMESTAMP "
                         "WHERE campaign_id=? AND idempotency_key=? AND request_hash=? AND last_request_id=? "
                         "AND status='in_progress'",
                         (error.code, int(error.retryable), self.session.campaign_id, key, request_hash, request_id))
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            conn.close()
