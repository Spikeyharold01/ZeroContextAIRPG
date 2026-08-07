# ZeroContextAI Project Continuation

**Status:** Canonical project handover
**Repository branch represented:** main
**Last verified commit:** ec4145cb5d388114fd4c6d88ccc7853722af2c31
**Schema version:** 009
**Last updated:** 2026-08-07
**Last completed stage:** 2B
**Next planned stage:** 2C

> This document describes the accepted repository state.
> It must distinguish implemented functionality from approved or planned functionality.
> Before proposing architectural changes, inspect this document and the Living Architecture.

Project Continuation Summary
Project Identity
ZeroContextAI is a universal AI storytelling and RPG platform intended to maintain coherent, persistent campaign state across model interactions.

It is not fundamentally a D&D-specific engine. D&D, Pathfinder, Call of Cthulhu, and other mechanical systems are optional adapters around the universal storytelling core.

The campaign SQLite database—not an AI model—owns authoritative campaign truth. AI models propose narrative and state consequences; trusted engine code validates and atomically commits approved effects.

Core Architectural Principles
Settled principles include:

one SQLite database file per campaign;

database authority;

AI proposes and the engine validates;

Generic State for genre-neutral state;

atomic Conversation Turns;

separate Conversation Turn, Story Time, and State Document Revision;

optimistic document-revision checks;

deterministic ordering, retrieval, and prompt construction;

prompt reconstruction from campaign state every request;

provider-independent model boundaries;

complete rules-free operation;

optional rules systems;

optional derived embeddings;

completed replay without model calls or persistence;

all-or-nothing accepted-turn transactions.

Three-Service AI Architecture
Storyteller LLM
Responsibility:

interpret unrestricted player input;

resolve attempted actions and dialogue;

generate visible narrative;

propose approved Generic State consequences.

Implemented:

StorytellerProtocol;

dependency injection;

deterministic mock Storyteller.

Planned:

real local or remote Storyteller providers.

Rules LLM
Responsibility:

optional specialist rules interpretation for campaigns with enabled rules adapters.

Current status:

not executed by Stage 2B;

not required for rules-free campaigns;

real orchestration remains planned.

Embedding Service
Responsibility:

optional future derived semantic retrieval.

Current status:

not implemented;

Stage 2B has no embedding requirement;

Stage 2C is the intended embedding-service stage.

Client Architecture
Implemented client boundary
A synchronous, non-streaming OpenAI-compatible endpoint exists at:

POST /v1/chat/completions
It supports approved string messages and sampling fields, one assistant response, and visible narrative only.

Planned client strategy
ZeroContextAI should support OpenAI-compatible clients generally.

SillyTavern is an important intended compatible client, but it is not authoritative campaign storage.

Planned future functionality includes:

one-time Character Card import;

conversion into a reusable approximately 400-token Character Profile;

stripping repeated raw card content from later ordinary requests;

complete prompt target below approximately 4,000 tokens;

selective treatment of client system prompts;

Lorebook ingestion;

selective Lorebook retrieval instead of complete insertion each turn.

Character Card conversion, repeated-card stripping, and Lorebook ingestion are not implemented at the reviewed commit.

## Current Development Position

- **Last completed stage:** Stage 2B
- **Current branch represented:** main
- **Current verified main commit:** ec4145cb5d388114fd4c6d88ccc7853722af2c31
- **Schema:** 9
- **Migration:** 009_conversation_turns.py
- **Stage 2B review verdict:** Safe to merge
- **Stage 2B merge status:** merged into main
- **Next planned stage:** Stage 2C — Embedding Service and Semantic Retrieval
- **Remote CI:** passed on main

Stage 2B is now part of the accepted mainline architecture.

Earlier implemented foundations include campaign lifecycle, Generic State, StatePatch persistence, compatibility extraction/read-only access, strict structured-output recovery, request/session lifecycle, Conversation Turns, deterministic retrieval, prompt construction, and atomic persistence.

Implemented Capabilities
Verified capabilities include:

campaign creation, opening, repair, backup, and migration;

stable campaign/database identity;

one database per campaign;

Generic State documents;

canonical JSON and hashes;

per-document revisions;

lifecycle state;

strict StatePatch contracts;

expected-value checks;

namespace authorization;

patch idempotency;

patch audit logs;

transaction-aware projections;

legacy compatibility extraction;

read-only compatibility reader;

strict OpenAI-compatible Pydantic contracts;

exact raw-message preservation;

strict hidden-tail framing;

Pydantic StorytellerOutput validation;

syntax-only, one-attempt json_repair;

request-level idempotency;

lease expiry and reclamation;

lease-owner validation at commit;

completed replay;

Conversation Turn request, commit, and message persistence;

generic working memory;

generic dialogue focus;

optional Story Time;

required core-state exact retrieval;

bounded optional context retrieval;

relational retrieval;

exact aliases;

identity-based ambiguity;

Unicode and case normalization;

overlap-aware phrase matching;

pronoun exclusion;

lexical retrieval;

fuzzy lexical similarity;

exact-alias state promotion;

deterministic ranking;

item-level prompt eviction;

prompt hashing;

deterministic mock Storyteller;

injected Storyteller abstraction;

synchronous OpenAI-compatible chat;

atomic persistence;

complete rollback;

rules-free operation;

local test and CI workflow configuration.

Not Yet Implemented
Not implemented:

real embeddings;

configurable local Embedding Service and reference embedding-model integration;

embedding-model swapping through configuration;

semantic vector storage;

semantic vector retrieval;

stale embedding detection;

embedding rebuild management;

Stage 2D retrieval evaluation;

real Storyteller providers;

Character Card conversion;

repeated card stripping;

Lorebook ingestion;

Lorebook retrieval;

Storyteller-generated Facts persistence;

Storyteller-generated Events persistence;

Rules LLM orchestration;

connected specialist rules model;

deterministic dice;

complete Rules Adapters;

multiplayer actor selection;

streaming;

production authentication and authorization;

complete import/export and deployment tooling.

Database and Persistence Position
Latest schema: 9.

Stage 2B tables:

conversation_turn_requests;

conversation_turn_commits;

conversation_turn_messages.

Request-to-commit and commit-to-message ownership is campaign-scoped with composite foreign keys.

A reservation capability contains:

request-row ID;

current request ID;

attempt number.

A successful request:

validates the session and request;

retrieves context;

reserves an idempotency attempt;

calls the Storyteller outside the transaction;

validates hidden output;

starts BEGIN IMMEDIATE;

rechecks campaign turn, lease ownership, and document revisions;

applies State Patches;

records audit, patch-idempotency, and projection changes;

writes memory, dialogue, and optional Story Time patches;

writes the accepted commit and messages;

increments Conversation Turn once;

stores the replay response;

commits.

Any failure rolls back all effects.

Completed replay returns the stored response without a new model call or write.

Retrieval Position
Required context is retrieved independently:

active scene state;

dialogue focus;

player memory;

campaign Story Time.

Optional state remains bounded.

Current retrieval includes:

direct relational context;

exact alias matching;

normalized Unicode/case/whitespace handling;

whole-word/phrase matching;

identity-based ambiguity;

lexical retrieval;

fuzzy lexical similarity;

recent facts/events/chat;

deterministic ranking;

exact-alias state promotion.

Alias ambiguity means:

more than one distinct (subject_type, subject_id)
for the same normalized alias
Repeated occurrences and duplicate sources for one identity do not create ambiguity.

Embedding-based semantic retrieval remains future Stage 2C work.

Prompt Position
Mandatory prompt core includes:

engine instruction;

campaign and Conversation Turn;

active player;

scene and location;

authoritative participants;

scene state;

dialogue focus;

memory;

Story Time;

exact raw user message;

output contract.

Optional items are evicted deterministically one at a time.

Directly relevant authoritative state, including exact-alias-matched remote subjects, is retained longer than unrelated state and ambiguous hints.

Prompt hashes are deterministic for identical input.

Current approximation:

ceil(len(text) / 4)
Future product targets:

full prompt below approximately 4,000 tokens;

reusable Character Profile around 400 tokens.

## Test Position

Final Stage 2B pre-merge review at SHA
`ba758408c1968f75e257064b8a6780c1b82bb481`:

- Full suite: 430 passed
- Failed: 0
- Skipped: 0
- Warnings: 1
- Stage 2B: 71 passed
- Alias/context: 22 passed
- Migration v9: 5 passed
- Contracts: 90 passed
- Structured output: 71 passed
- scripts/check.sh: passed
- Compileall: passed
- Diff check: passed
- Working tree: clean
- Review verdict: Safe to merge

Stage 2B has subsequently been merged into `main`.

Current accepted main commit represented by this document:
`ec4145cb5d388114fd4c6d88ccc7853722af2c31`.

## Remaining Work

### Merge blockers

None currently known from Stage 2B.

### Major follow-ups

- Reconcile stale implementation-status documentation.
- Begin Stage 2C — Embedding Service and genuine semantic retrieval.
- Introduce an application-owned EmbeddingService protocol.
- Implement a configurable local embedding-model adapter.
- Add persistent derived embedding storage and stale-embedding detection.
- Preserve deterministic lexical/relational fallback when embeddings are unavailable.

### Minor follow-ups

- Resolve the Starlette/httpx TestClient deprecation warning.
- Replace the production reservation invariant assert with explicit error handling.
- Add no-active-scene and inactive-required-document tests.
- Clarify common-word alias registration policy.

### Immediate next task

Implement Stage 2C — Embedding Service and Semantic Retrieval.

### Following stage

Stage 2D — Retrieval Evaluation and Prompt Context.

Important Repository Files
Inspect first:

erp-dm-server/campaign.py

erp-dm-server/database/db_manager.py

erp-dm-server/database/schema.sql

erp-dm-server/database/migrations/009_conversation_turns.py

erp-dm-server/contracts/openai.py

erp-dm-server/contracts/state.py

erp-dm-server/contracts/storyteller.py

erp-dm-server/database/state_repository.py

erp-dm-server/database/generic_state_reader.py

erp-dm-server/proxy_server/app.py

erp-dm-server/proxy_server/routes/chat.py

erp-dm-server/proxy_server/services/conversation_turn_service.py

erp-dm-server/proxy_server/services/context_retrieval.py

erp-dm-server/proxy_server/services/alias_matcher.py

erp-dm-server/proxy_server/services/prompt_builder.py

erp-dm-server/proxy_server/services/storyteller.py

erp-dm-server/proxy_server/services/structured_tail.py

erp-dm-server/structured_output/

erp-dm-server/tests/stage2b/

erp-dm-server/database/test_migration_v9.py

erp-dm-server/proxy_server/README.md

erp-dm-server/readme.md

.github/workflows/test.yml

Settled Architectural Decisions
Do not reopen without explicit approval:

one SQLite database per campaign;

database authority;

AI proposes, engine validates;

no direct model database writes;

Generic State as the universal state model;

StatePatch authorization and revisions;

separate Story Time, Conversation Turn, and revision;

no automatic Story Time progression per request;

all-or-nothing persistence;

no second LLM for JSON repair;

syntax-only, one-attempt repair;

rules remain optional;

embeddings remain optional and derived;

no embedding requirement for basic operation;

fuzzy lexical matching is not semantic retrieval;

deterministic retrieval and prompt construction;

no provider-specific tool requirement;

no cross-campaign state sharing;

compatibility documents remain read-only;

prompts are engine-owned and rebuilt;

future Character Cards are imported and converted once;

repeated raw Character Cards should not consume normal prompts after conversion;

Stage boundaries must remain explicit; do not introduce later-stage functionality without approval.

ZeroContextAI Project Continuation Prompt
You are continuing development of ZeroContextAI, a universal AI storytelling and RPG engine.

ZeroContextAI is not fundamentally a D&D game. One SQLite database represents one campaign and is authoritative. AI models propose narrative and state consequences; trusted Python validates and atomically persists approved effects. Rules systems are optional adapters, and rules-free campaigns must work fully.

Current repository state:

branch: main
commit: ec4145cb5d388114fd4c6d88ccc7853722af2c31
schema: 9
migration: 009_conversation_turns.py
last completed stage: Stage 2B
Stage 2B review verdict: Safe to merge
Stage 2B merge status: merged into main
next planned stage: Stage 2C — Embedding Service and Semantic Retrieval
Not yet implemented: the Stage 2C Embedding Service, persistent semantic vectors, stale-embedding detection, Stage 2D retrieval evaluation, real Storyteller providers, SillyTavern Character Card conversion, repeated-card stripping, Lorebook retrieval, Facts/Events persistence, Rules LLM orchestration, deterministic dice, complete Rules Adapters, multiplayer, streaming, production authentication, and complete deployment/import/export.
Immediate next steps:

1. Inspect the merged Stage 2B mainline implementation and this continuation document.
2. Implement Stage 2C Embedding Service and genuine semantic retrieval on a new feature branch.
3. Keep embeddings optional and derived.
4. Preserve Stage 2B relational, exact-alias, lexical, and fuzzy-lexical retrieval as the fallback path.
5. Store model/version/dimension/preprocessing metadata so changing embedding models makes incompatible vectors stale rather than corrupting retrieval.
6. After Stage 2C implementation, perform an independent read-only review before merge.
7. Proceed to Stage 2D only after Stage 2C is accepted.

Preserve settled architecture. Do not redesign without explicit approval. In particular preserve database authority, one database per campaign, AI-proposes/engine-validates, Generic State, atomic Conversation Turns, Story Time separation, no second repair LLM, optional rules, optional embeddings, deterministic retrieval, no direct model database access, no provider-specific tool requirement, and no partial persistence.

Before proposing architectural changes, inspect the repository and Living Architecture documentation, especially database migrations, Generic State contracts/repository, ConversationTurnService, context retrieval, alias matcher, prompt builder, structured-output recovery, and Stage 2B tests. Distinguish implemented functionality from approved or planned work, and verify older documentation claims against current source.


