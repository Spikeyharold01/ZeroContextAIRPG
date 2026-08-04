The Adaptive RPG/ERP Engine – Product Architecture v6.0
1. Overview
This document describes the target OpenAI-compatible proxy architecture and records the contracts that the implementation is working toward. The repository currently provides the configuration system and UI, OpenAI-message ingestion, D&D character-sheet parsing, SQLite persistence and migrations, and automated tests. It does not yet provide the FastAPI proxy endpoint, prompt rebuilder, storyteller or rules-model clients, fact manager, runtime save/load service, or complete end-to-end orchestration.

1.1 Version concepts

The project uses three independent version numbers:

- Product or architecture version: v6.0. This versions the overall engine design described by this document. It is not a database or API compatibility number.
- Database schema version: 7. This is stored in SQLite's schema_version table and is managed by DatabaseManager migrations.
- API version: v1. This is the planned HTTP namespace in routes such as /v1/chat/completions. It does not imply that the endpoint is implemented yet.

1.2 Implementation status

Implemented foundations:

- typed configuration and the NiceGUI configuration editor;
- OpenAI-format message ingestion and marker extraction;
- D&D character-sheet parsing;
- SQLite schema version 7, reconciliation migrations, generic state access, and tests.

1.3 Generic state persistence foundation (Stage 1.5B)

One SQLite database file represents exactly one campaign. Its stable UUID is
stored in the single non-deleted `campaigns` row and should be copied into that
campaign's selected `engine.toml`. Production campaign lifecycle code must use
`create_campaign`, `open_campaign`, `repair_campaign`, or the explicit
`repair_missing_configuration` workflow
from `campaign.py`; callers must not construct a manager from a bare database
path. These workflows resolve database and archive paths relative to the selected
configuration directory, atomically save and reload configuration, and require
the configuration and database UUIDs to match. Campaign databases are never
attached, merged, or active together.

Opening an existing campaign requires an explicitly selected configuration.
Missing configuration or a missing/mismatched version-7 identity is an incomplete
package error, not permission to use defaults. A legacy version-6 package is
backed up and migrated, then its persisted identity is written to and verified
from the selected configuration. If configuration synchronization fails, the
migrated database and backup remain recoverable and retry reuses the database ID.
If `engine.toml` is completely missing, `repair_missing_configuration` requires
both the intended existing database path and replacement configuration destination,
validates the version-7 database read-only, writes rules-off defaults unless
approved base settings are supplied, and then reopens through normal validation.

An ordinary application runtime treats only one `CampaignSession` as active.
Repositories must be created with `session.create_state_repository(...)`; calling
`session.close()` idempotently invalidates those repositories and releases the
session's manager reference. Sequential campaign changes close the old session,
discard its derived objects, and explicitly open the next package (the minimal
`change_campaign` helper performs those two steps). Full live hot-switch request,
model, prompt-cache, and background-job orchestration is deferred until a
long-running application runtime exists. Module-level `config.settings` remains
application defaults only and is never the authority for an opened campaign.

`state_documents` is authoritative only for writes made through the new generic
state repository. Legacy world, plot, scene, character, fact, memory, event, and
rules tables remain compatibility-readable and have not undergone an authority
cutover or automatic dual write. Future legacy extraction is a separate stage.

Rules profiles are optional. A campaign may keep `rules_profile_id` null and use
generic campaign, plot, scene, and entity documents, facts, events, turns, and
memory without creating mechanical, D&D, or combat rows.

Campaign duration and total database, document-count, turn, entity, fact, event,
and audit growth have no product limit. Configurable `state_persistence` patch
and individual-document thresholds are operational memory/SQLite safeguards,
not storytelling limits. Large live state should be split by entity, scene,
location, plot thread, time period, subsystem, or user-defined subject; history
belongs in append-only events, facts, audit records, summaries, knowledge chunks,
and future verified archives. Archive pruning and legacy authority cutover are
not implemented in Stage 1.5B.

Planned runtime components:

- FastAPI/OpenAI-compatible endpoints and streaming;
- prompt rebuilding and token-budget enforcement;
- storyteller, rules-engine, embedding, and RAG clients;
- fact synchronization, structured-output extraction, orchestration, and campaign archive services;
- the character-card conversion and SillyTavern installation UI.

Target Design Capabilities (Product v6.0)
Feature	Description
OpenAI‑Compatible API	The planned proxy will listen on /v1/chat/completions and accept the same JSON format as OpenAI.
Belief System	The database stores world_fact, belief_fact, and rumor_fact records with source_character_id provenance; runtime synchronization remains planned.
Modular Rules Engine	The target runtime makes the rules model optional and replaceable with game-specific adapters.
Save & Load	The planned archive service will export database state and portable configuration for later import.
Proxy‑Managed Facts	The planned storyteller output and fact manager will propose and reconcile facts; absence will not imply deletion.
In‑Game Turn Counter	The implemented persistence layer expires facts using expires_at_turn rather than real-world time.
Confidence Scores	The target state-update pipeline will filter low-confidence emotional shifts.
2. Target Architecture Diagram (planned runtime)
text
┌─────────────────────────────────────────────────────────────────────────────┐
│                         SILLYTAVERN (UI)                                   │
│                                                                             │
│  ┌────────────────────────────────────────────────────────────────────┐   │
│  │  API Connection: OpenAI Compatible                                 │   │
│  │  URL: http://proxy:5000/v1/chat/completions                       │   │
│  │  Model: "proxy" (or any name)                                     │   │
│  └────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         PROXY SERVER (FastAPI)                             │
│                    OpenAI-Compatible Endpoints                             │
│                                                                             │
│  ┌────────────────────────────────────────────────────────────────────┐   │
│  │  ENDPOINT: POST /v1/chat/completions                               │   │
│  │  INPUT:  OpenAI Chat Completion format                             │   │
│  │  OUTPUT: OpenAI Chat Completion format                             │   │
│  └────────────────────────────────────────────────────────────────────┘   │
│                                      │                                     │
│                                      ▼                                     │
│  ┌────────────────────────────────────────────────────────────────────┐   │
│  │                         INGESTER                                   │   │
│  │  • Extracts character_id (from custom header or prompt)            │   │
│  │  • Extracts user_message, chat_history, sampling params            │   │
│  └────────────────────────────────────────────────────────────────────┘   │
│                                      │                                     │
│                                      ▼                                     │
│  ┌────────────────────────────────────────────────────────────────────┐   │
│  │                         DATABASE                                   │   │
│  │  • Characters, cores, emotions, stats, facts, beliefs, events      │   │
│  │  • World state, scene graph, plot state                            │   │
│  └────────────────────────────────────────────────────────────────────┘   │
│                                      │                                     │
│                                      ▼                                     │
│  ┌────────────────────────────────────────────────────────────────────┐   │
│  │              RULES ENGINE (Modular, Optional)                      │   │
│  │                                                                   │   │
│  │  ┌─────────────────────────────────────────────────────────────┐  │   │
│  │  │  DnD 5e (1.5B DnD‑Unified) – Default                       │  │   │
│  │  └─────────────────────────────────────────────────────────────┘  │   │
│  │  ┌─────────────────────────────────────────────────────────────┐  │   │
│  │  │  Pathfinder (Configurable)                                  │  │   │
│  │  └─────────────────────────────────────────────────────────────┘  │   │
│  │  ┌─────────────────────────────────────────────────────────────┐  │   │
│  │  │  Call of Cthulhu (Configurable)                             │  │   │
│  │  └─────────────────────────────────────────────────────────────┘  │   │
│  │  ┌─────────────────────────────────────────────────────────────┐  │   │
│  │  │  Battletech (Configurable)                                  │  │   │
│  │  └─────────────────────────────────────────────────────────────┘  │   │
│  │  ┌─────────────────────────────────────────────────────────────┐  │   │
│  │  │  OFF (Pure Narrative)                                       │  │   │
│  │  └─────────────────────────────────────────────────────────────┘  │   │
│  └────────────────────────────────────────────────────────────────────┘   │
│                                      │                                     │
│                                      ▼                                     │
│  ┌────────────────────────────────────────────────────────────────────┐   │
│  │              REBUILDER (Jinja2 + Headers)                         │   │
│  │  • Builds 4,000‑token prompt with headers                         │   │
│  │  • Injects Character Cores, Beliefs, Facts, Stats, History        │   │
│  └────────────────────────────────────────────────────────────────────┘   │
│                                      │                                     │
│                                      ▼                                     │
│  ┌────────────────────────────────────────────────────────────────────┐   │
│  │              MAIN STORYTELLER (8B+ Local/Cloud)                   │   │
│  │  • Generates narrative + JSON state in one pass                   │   │
│  │  • Sampling parameters passed through from SillyTavern            │   │
│  └────────────────────────────────────────────────────────────────────┘   │
│                                      │                                     │
│                                      ▼                                     │
│  ┌────────────────────────────────────────────────────────────────────┐   │
│  │              EXTRACTORS + FACT MANAGER                            │   │
│  │  • Extracts JSON with regex (no GBNF)                             │   │
│  │  • Compares facts to DB → add/update (never delete on absence)    │   │
│  │  • Processes emotional shifts (confidence > 0.55)                 │   │
│  │  • Logs major events to event_log                                 │   │
│  └────────────────────────────────────────────────────────────────────┘   │
│                                      │                                     │
│                                      ▼                                     │
│  ┌────────────────────────────────────────────────────────────────────┐   │
│  │              RESPONSE + SAVE & LOAD                               │   │
│  │  • Returns OpenAI‑compatible response to SillyTavern              │   │
│  │  • Saves DB state + config to file (Export)                       │   │
│  │  • Loads DB state + config from file (Import)                     │   │
│  └────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
3. Planned OpenAI‑Compatible API Contract
3.1 Planned endpoint: POST /v1/chat/completions
Input (OpenAI Format):

json
{
  "model": "proxy",
  "messages": [
    {"role": "system", "content": "You are Natasha..."},
    {"role": "user", "content": "What's the plan?"},
    {"role": "assistant", "content": "We let the meet happen..."},
    {"role": "user", "content": "Great. What's the plan?"}
  ],
  "temperature": 0.85,
  "top_p": 0.92,
  "max_tokens": 600,
  "stream": true
}
Output (OpenAI Format):

json
{
  "id": "chatcmpl-123",
  "object": "chat.completion",
  "created": 1699000000,
  "model": "proxy",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "My fingers lightly ghost over the back of your hand..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 1500,
    "completion_tokens": 250,
    "total_tokens": 1750
  }
}
3.2 Planned character ID extraction
The target integration allows SillyTavern to send a character ID in the request headers. The future proxy will extract it.

Headers:

text
X-Character-ID: 1
Fallback design: extract the character name from the system prompt or first message.

3.3 Chat-history ownership and authority

- SillyTavern may send raw chat history with each request.
- The proxy treats that history as untrusted input material, not persistent authority.
- The prompt builder selects only the configured number of recent exchanges; receiving more history does not mean all of it enters the model prompt.
- SQLite is the authoritative source for persistent facts, beliefs, character state, world state, scene state, goals, mechanics, and event history.
- Older chat text must not override authoritative database state. Conflicts are resolved in favor of validated SQLite state.

4. Planned Modular Rules Engine
The configuration schema exists, but the router and model adapters shown below are illustrative design examples and are not present in the repository yet.
4.1 Configuration
python
# config.py

class Config:
    # Rules Engine Configuration
    RULES_ENGINE_ENABLED = True          # False = pure narrative
    RULES_ENGINE_TYPE = "dnd_5e"         # "dnd_5e", "pathfinder", "call_of_cthulhu", "battletech", "off"
    
    # Rules Engine Models (each is a separate GGUF file)
    RULES_ENGINE_MODELS = {
        "dnd_5e": "models/dnd-unified-1.5b.Q4_K_M.gguf",
        "pathfinder": "models/pathfinder-1.5b.Q4_K_M.gguf",
        "call_of_cthulhu": "models/coc-1.5b.Q4_K_M.gguf",
        "battletech": "models/battletech-1.5b.Q4_K_M.gguf"
    }
4.2 Rules Router
python
# proxy_server/rules_router.py

class RulesRouter:
    def __init__(self, config):
        self.config = config
        self.enabled = config.RULES_ENGINE_ENABLED
        self.engine_type = config.RULES_ENGINE_TYPE
        self.model = self._load_engine() if self.enabled else None
    
    def _load_engine(self):
        if self.engine_type == "off" or not self.enabled:
            return None
        
        model_path = self.config.RULES_ENGINE_MODELS.get(self.engine_type)
        if not model_path:
            raise ValueError(f"Unknown rules engine: {self.engine_type}")
        
        return llama_cpp.Llama(
            model_path=model_path,
            n_ctx=4096,
            n_threads=6,
            n_gpu_layers=0  # CPU only
        )
    
    def adjudicate(self, action, state):
        if not self.enabled or self.model is None:
            return {"requires_roll": False, "narrative": "Pure narrative mode."}
        # ... existing adjudication logic ...
4.3 Intent Classification (Still Used)
In the target runtime, the intent classifier runs regardless of rules-engine state. It determines whether an action is combat, skill_check, or narrative. If the rules engine is disabled, the router treats actions as narrative.

python
# proxy_server/router.py

def process_message(self, context):
    # Intent classification always runs
    intent = self.classifier.classify(context["user_message"])
    
    # Only run rules engine if enabled AND action requires it
    if self.rules_router.enabled and intent in ["combat", "skill_check"]:
        rules_result = self.rules_router.adjudicate(...)
        # ... roll dice, etc.
    else:
        # Skip rules, proceed to prompt building
        mechanical_result = {"success": True, "narrative": "Narrative mode."}
5. Planned Save & Load System
The following snippets define the intended archive contract. `SaveLoadManager` and its HTTP endpoints are not implemented yet.
5.1 Export: Save Campaign
python
# proxy_server/save_load.py

import zipfile
import json
import os
from datetime import datetime

class SaveLoadManager:
    def __init__(self, db_manager, config):
        self.db = db_manager
        self.config = config
    
    def export_campaign(self, campaign_name: str) -> str:
        """Export entire campaign state to a single file."""
        export_dir = f"exports/{campaign_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        os.makedirs(export_dir, exist_ok=True)
        
        # 1. Export database (SQLite dump)
        db_dump_path = f"{export_dir}/game.db"
        self.db.export_to_file(db_dump_path)
        
        # 2. Export config
        config_path = f"{export_dir}/config.json"
        with open(config_path, "w") as f:
            json.dump({
                "rules_engine_enabled": self.config.RULES_ENGINE_ENABLED,
                "rules_engine_type": self.config.RULES_ENGINE_TYPE,
                "main_model": self.config.MAIN_LLM_MODE,
                "chat_history_exchanges": self.config.CHAT_HISTORY_EXCHANGES
            }, f)
        
        # 3. Zip everything
        zip_path = f"{export_dir}.zip"
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(export_dir):
                for file in files:
                    zipf.write(os.path.join(root, file), file)
        
        return zip_path
5.2 Import: Load Campaign
python
def import_campaign(self, zip_path: str) -> bool:
    """Load a campaign from a zip file."""
    extract_dir = "imports/" + os.path.splitext(os.path.basename(zip_path))[0]
    os.makedirs(extract_dir, exist_ok=True)
    
    # 1. Extract zip
    with zipfile.ZipFile(zip_path, 'r') as zipf:
        zipf.extractall(extract_dir)
    
    # 2. Restore database
    db_path = f"{extract_dir}/game.db"
    if os.path.exists(db_path):
        self.db.import_from_file(db_path)
    
    # 3. Restore config
    config_path = f"{extract_dir}/config.json"
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            config_data = json.load(f)
            self.config.RULES_ENGINE_ENABLED = config_data.get("rules_engine_enabled", True)
            self.config.RULES_ENGINE_TYPE = config_data.get("rules_engine_type", "dnd_5e")
            # ... update other settings ...
    
    return True
5.3 API Endpoints
python
# main.py

@router.post("/v1/campaign/export")
async def export_campaign(campaign_name: str = "campaign"):
    zip_path = save_load_manager.export_campaign(campaign_name)
    return FileResponse(zip_path, media_type='application/zip', filename=os.path.basename(zip_path))

@router.post("/v1/campaign/import")
async def import_campaign(file: UploadFile):
    temp_path = f"tmp/{file.filename}"
    with open(temp_path, "wb") as f:
        f.write(await file.read())
    success = save_load_manager.import_campaign(temp_path)
    return {"success": success}
6. Target Token Budget (Product Architecture v6.0)
Component	Tokens
System Prompt	~200
Character Core	~150
Emotional State	~50
World State + Scene Graph	~80
Conversational Facts (with labels)	~80–120
Belief Facts (with sources)	~50–100
Working Memory (last 300 tokens)	~300
Chat History (6 exchanges)	~1,200–1,500
RAG Lore	~200
Combat State	~100
User Message	~50
Instruction	~50
TOTAL	~2,680–2,950 tokens
7. Target File Structure (planned; Product Architecture v6.0)
text
erp-dm-server/
├── config.py
├── requirements.txt
├── main.py                         # FastAPI + OpenAI-compatible endpoints
├── database/
│   ├── __init__.py
│   ├── schema.sql
│   ├── db_manager.py
│   └── test_data.py
├── proxy_server/
│   ├── __init__.py
│   ├── main.py                     # FastAPI routes
│   ├── router.py                   # Main orchestration
│   ├── ingester.py                 # OpenAI payload ingestion
│   ├── rebuilder.py                # Jinja2 prompt builder
│   ├── ner.py                      # spaCy NER
│   ├── memory.py                   # RAG (ChromaDB)
│   ├── classifier.py               # Intent classification
│   ├── staging.py                  # Transaction staging
│   ├── fact_manager.py             # Fact sync with belief support
│   ├── event_manager.py            # Event logging
│   ├── rules_router.py             # Modular rules engine
│   ├── save_load.py                # Export/Import
│   └── extractors.py               # JSON extraction + validation
├── llm_clients/
│   ├── __init__.py
│   ├── rules_model.py              # Base rules engine
│   ├── main_llm.py                 # 8B+ Storyteller
│   └── utils.py                    # Token counting
├── models/                         # GGUF files
├── data/                           # SQLite + ChromaDB
├── exports/                        # Campaign exports
└── imports/                        # Campaign imports
8. Database Schema Version 6
8.1 conversational_facts Table (With Belief Support)
sql
CREATE TABLE conversational_facts (
    id TEXT PRIMARY KEY,
    character_id INTEGER,
    fact_text TEXT NOT NULL,
    fact_references TEXT,
    embedding BLOB,
    importance FLOAT DEFAULT 0.5,
    confidence FLOAT DEFAULT 0.9,
    source_type TEXT,                  -- 'narrative', 'user', 'system'
    fact_type TEXT DEFAULT 'world_fact',   -- 'world_fact', 'belief_fact', 'rumor_fact'
    source_character_id INTEGER,            -- Who expressed this belief/rumor
    created_turn INTEGER DEFAULT 0,
    last_referenced_turn INTEGER DEFAULT 0,
    expires_at_turn INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT 1,
    game_day INTEGER DEFAULT 1,
    FOREIGN KEY (character_id) REFERENCES characters(id),
    FOREIGN KEY (source_character_id) REFERENCES characters(id)
);
9. Product Architecture Changes from v5.2
Change	Description
OpenAI‑Compatible API	The target proxy contract uses OpenAI-compatible request and response formats.
Belief System	fact_type (world_fact, belief_fact, rumor_fact) and source_character_id added.
Modular Rules Engine	The planned rules engine is optional and swappable (DnD, Pathfinder, CoC, Battletech, OFF).
Save & Load	A campaign archive service is planned; it is not implemented yet.
Rules Router	A planned component will route to the selected rules engine or bypass it.
Header‑Based Character ID	The planned API accepts X-Character-ID as its primary character selector.
10. Target Configuration Example (illustrative, not the current configuration API)
python
# config.py

class Config:
    # ---- Main Model ----
    MAIN_LLM_MODE = "local"           # "local", "runpod", "openrouter"
    MAIN_LLM_PATH = "models/raven-8b-v1-q6_K.gguf"
    MAIN_LLM_GPU_LAYERS = 99
    
    # ---- Rules Engine (Optional) ----
    RULES_ENGINE_ENABLED = True       # Set False for pure narrative ERP
    RULES_ENGINE_TYPE = "dnd_5e"      # "dnd_5e", "pathfinder", "call_of_cthulhu", "battletech", "off"
    
    RULES_ENGINE_MODELS = {
        "dnd_5e": "models/dnd-unified-1.5b.Q4_K_M.gguf",
        "pathfinder": "models/pathfinder-1.5b.Q4_K_M.gguf",
        "call_of_cthulhu": "models/coc-1.5b.Q4_K_M.gguf",
        "battletech": "models/battletech-1.5b.Q4_K_M.gguf"
    }
    
    # ---- Fact / Belief ----
    FACT_TYPE_DEFAULT = "world_fact"
    
    # ---- Sampling Parameters ----
    DEFAULT_TEMPERATURE = 0.85
    DEFAULT_TOP_P = 0.92
    DEFAULT_TOP_K = 40
    DEFAULT_MAX_TOKENS = 600
    
    # ---- Turn Counter ----
    CHAT_HISTORY_EXCHANGES = 6
    
    # ---- Server ----
    SERVER_HOST = "0.0.0.0"
    SERVER_PORT = 5000
11. Implementation Checklist
Component	Status
config.py and config_ui.py	Implemented foundation
database/schema.sql, migrations, and db_manager.py	Implemented foundation
ingester.py and dnd_ingester.py	Implemented foundation
FastAPI /v1 endpoints	Planned, not implemented
Prompt rebuilder and token budgeting	Planned, not implemented
Rules and storyteller model clients	Planned, not implemented
Fact manager, extractors, and orchestration router	Planned, not implemented
Campaign archive import/export service	Planned, not implemented
Character-card conversion and SillyTavern setup UI	Planned, not implemented
End-to-end integration testing	Blocked on runtime implementation
12. Final Notes
OpenAI Compatibility – The target runtime will expose http://proxy:5000/v1 as an OpenAI-compatible endpoint; this endpoint is not implemented in the current repository.

Belief System – Facts are labelled so the system knows whether something is objective truth or a character's opinion.

Modular Rules Engine – The design allows rules to be disabled or replaced, but the runtime router and model adapters remain planned.

Save & Load – Export/import behavior is an architectural requirement; the runtime archive service remains planned.

The persistence and ingestion foundations are implemented; the proxy runtime remains to be built.

UPDATE 1

Users will need to add specific tags to their character cards, system prompts etc
=CHARACTER CARD= A Character card and Stats
=SYSTEM PROMPT= System prompt
=SCENARIO= - The current scenario
=EXAMPLES= - Examples of chat
=USER= - users Stats

UPDATE 2
Users wanting to use D&D stats must use the following character-sheet format. The parser acceptance test reads this exact complete example, including the blank lines between sections:
[D&D STATS]
CLASS: Fighter
SUBCLASS: Battle Master
LEVEL: 6
RACE: Half-Elf
ALIGNMENT: Neutral Good
BACKGROUND: Outlander
EXPERIENCE_POINTS: 0

[ABILITIES]
STR: 16
DEX: 16
CON: 14
INT: 12
WIS: 14
CHA: 15

[COMBAT]
HP: 52
HP_MAX: 52
ARMOR_CLASS: 16
SPEED: 30
INITIATIVE: 3
PROFICIENCY_BONUS: 3
HIT_DICE: 6d10

[SAVING_THROWS]
STR: 6
DEX: 3
CON: 5
INT: 1
WIS: 2
CHA: 2

[SKILLS]
Acrobatics: 6
Animal Handling: 5
Arcana: 1
Athletics: 6
Deception: 2
History: 1
Insight: 5
Intimidation: 2
Investigation: 1
Medicine: 2
Nature: 1
Perception: 5
Performance: 2
Persuasion: 5
Religion: 1
Sleight of Hand: 3
Stealth: 6
Survival: 5

[PROFICIENCIES]
ARMOR: Light, Medium, Heavy, Shields
WEAPONS: Simple, Martial
TOOLS: Thieves' Tools
LANGUAGES: Common, Elvish, Goblin, Dwarvish

[SENSES]
PASSIVE_PERCEPTION: 15
DARKVISION: 60

[FEATURES]
RACIAL_TRAITS: Fey Ancestry, Darkvision
CLASS_FEATURES: Fighting Style (Archery), Action Surge, Second Wind, Extra Attack
FEATS: None

[MANEUVERS]
Commander's Strike: Forgo one attack to direct an ally to strike.
Rally: Grant an ally temporary HP (1d8 + CHA mod).
Trip Attack: Add 1d8 damage and force a STR save or target falls prone.

[EQUIPMENT]
Longsword +1: weapon|melee|7|1d8+4|slashing
Longbow: weapon|ranged|8|1d8+3|piercing
Dagger: weapon|finesse|6|1d4+3|piercing
Studded Leather Armor: armor|16
Quiver of 20 Arrows: ammunition
Explorer's Pack: gear
Elven Ring: gear
Hunting Trap: gear
Traveler's Clothes: gear

[SPELLCASTING]
ABILITY: None
SAVE_DC: 10
ATTACK_BONUS: 4
CANTRIPS_KNOWN: 0
SPELLS_KNOWN: 0

[SPELL_SLOTS]
LEVEL_1: 0
LEVEL_2: 0
LEVEL_3: 0
LEVEL_4: 0
LEVEL_5: 0
LEVEL_6: 0
LEVEL_7: 0
LEVEL_8: 0
LEVEL_9: 0

[KNOWN_SPELLS]
None

[PREPARED_SPELLS]
None

The acceptance test verifies that this exact example is parsed completely.

PLANNED ADDITION -

Add a NiceGUI interface to enable users to upload a character card before playing and add the special SillyTavern config file.
Will require adding a tab to NiceGUI that is disabled until a connection is made t=wth the soryteller AI.
Tab should allow users to drop a card file (png/text) onto the form and convert it to the required standard. 
D&D stats & Character card will be editable before exporting. Request users to add character card to ST.
Also has a checkbox option to add D&D stats if missing.
The character card can be auto installed into ST or saved to be installed manually.
Must install pillow to read and write png with text
The ST config lives in .\SillyTavern\data\default-user\context and will be named ZerocontextAI.json. But we must also allow users to manually download the config and add it manually to their ST (for mobile / cloud users)


License
Adaptive RPG Engine is licensed under the GNU Affero General Public License v3.0 (AGPLv3). See the LICENSE file for details.
