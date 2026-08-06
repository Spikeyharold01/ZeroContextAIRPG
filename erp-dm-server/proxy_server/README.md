# Stage 2B synchronous storytelling boundary

`campaigns.current_turn` is the **accepted conversation exchange sequence
number**. It is not fictional Story Time, a combat/rules turn, a game day, a
scene count, or a state-document revision.

Story Time is optional generic state at `narrative.time` /
`narrative.campaign` / the actual campaign UUID. It is created or changed only
by an approved storyteller `StatePatch`. Generic document revisions advance
independently and only for documents that a validated patch changes.

When no scene is active, dialogue focus uses the campaign UUID as the
campaign-global fallback subject ID while retaining subject type
`narrative.scene`.

Stage 2B estimates prompt tokens as `ceil(len(text) / 4)` through one shared
helper. `PromptLimits` is injectable and its conservative default is intended
for deterministic tests, not as a permanent production/provider budget.

The application factory owns `StorytellerProtocol` injection. The default is
the synchronous `DeterministicMockStoryteller`; no remote provider, streaming,
worker, queue, scheduler, or rules adapter participates in this path.

Stage 2B supports exactly one active player-controlled entity per campaign
request. Campaigns with zero or multiple active PCs receive a controlled error;
multi-player actor selection is deferred.

Required active-scene state, dialogue focus, active-player memory, and campaign
Story Time are read by exact document identity before the independently bounded
optional-state query. Directly relevant authoritative state has stronger prompt
retention than unrelated optional state.

Stage 2B retrieval is deterministic relational, exact-alias, lexical, and fuzzy
lexical retrieval. Its character-trigram comparison is lexical similarity, not
an embedding or semantic score. Embedding-based semantic retrieval is deferred
to Stage 2C and no embedding model is required for rules-free Stage 2B.

`X-Idempotency-Key` is the only reliable completed-response replay mechanism.
The headerless fallback key protects duplicates only while they share the same
Conversation Turn snapshot. Once a request commits, repeating identical text
without the header is a new Conversation Turn.
The request-row ID, current request ID, and reservation attempt form a commit
capability; authoritative completion rechecks all three so an expired owner
cannot commit after another request reclaims its lease.

Secure structured-output diagnostics log hashes and bounded status metadata.
Raw hidden state is disabled by default. Explicit secure debugging can expose a
bounded, DEBUG-only payload that may contain private campaign information and
must never be enabled in ordinary user-visible logs.
