The Adaptive RPG/ERP Engine v6.0 – OpenAI-Compatible Proxy Architecture
1. Overview
The System is a production‑ready, OpenAI‑compatible proxy server that intercepts SillyTavern prompts, rebuilds them as surgical 4,000‑token prompts, and forwards them to a Main Storyteller LLM. The proxy mimics the OpenAI API format so that SillyTavern connects to it as if it were an OpenAI endpoint – no complex configuration required.

Key Innovations (v6.0)
Feature	Description
OpenAI‑Compatible API	The proxy listens on /v1/chat/completions and accepts the same JSON format as OpenAI. SillyTavern connects with zero configuration changes.
Belief System	Facts are stored as world_fact, belief_fact, or rumor_fact with source_character_id tracking who expressed a belief.
Modular Rules Engine	The 1.5B DnD‑Unified is optional and swappable. Users can turn it off, or replace it with other game‑specific models (Pathfinder, Call of Cthulhu, Battletech, etc.).
Save & Load	Full campaign persistence: export all DB tables + configuration to a single file; import to continue a campaign.
Proxy‑Managed Facts	The 8B outputs current facts; the proxy compares to the database and decides what to add or update. Facts not mentioned persist unchanged.
In‑Game Turn Counter	Facts expire based on expires_at_turn, not real‑world time.
Confidence Scores	Emotional shifts below 0.55 are ignored to prevent hallucination.
2. Updated Architecture Diagram
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
3. OpenAI‑Compatible API
3.1 Endpoint: POST /v1/chat/completions
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
3.2 Character ID Extraction
SillyTavern sends a characterId in the request headers when using the OpenAI endpoint. The proxy extracts it.

Headers:

text
X-Character-ID: 1
Fallback: Extract character name from the system prompt or the first message.

4. Modular Rules Engine
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
The intent classifier still runs regardless of Rules Engine state. It determines if an action is combat, skill_check, or narrative. If the Rules Engine is disabled, all actions are treated as narrative.

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
5. Save & Load System
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
6. Updated Token Budget (v6.0)
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
7. File Structure (v6.0)
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
8. Updated Schema (v6.0)
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
    FOREIGN KEY (character_id) REFERENCES characters(id),
    FOREIGN KEY (source_character_id) REFERENCES characters(id)
);
9. Summary of Changes from v5.2
Change	Description
OpenAI‑Compatible API	Proxy now mimics OpenAI format – SillyTavern connects with zero config changes.
Belief System	fact_type (world_fact, belief_fact, rumor_fact) and source_character_id added.
Modular Rules Engine	Rules engine is optional and swappable (DnD, Pathfinder, CoC, Battletech, OFF).
Save & Load	Full campaign persistence via export/import of DB + config.
Rules Router	New component routes to the appropriate rules engine or bypasses it.
Header‑Based Character ID	SillyTavern sends X-Character-ID header; proxy uses it.
10. Final Configuration Example
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
Phase	Files	Priority
1	config.py (Updated)	High
2	database/schema.sql (Add belief columns)	High
3	database/db_manager.py (Belief support)	High
4	proxy_server/ingester.py (OpenAI payload + headers)	High
5	proxy_server/main.py (OpenAI endpoints)	High
6	proxy_server/router.py (Rules engine integration)	High
7	proxy_server/rules_router.py (NEW)	High
8	proxy_server/save_load.py (NEW)	Medium
9	proxy_server/fact_manager.py (Belief support)	Medium
10	proxy_server/rebuilder.py (Belief labels)	Medium
11	proxy_server/extractors.py (Belief JSON)	Medium
12	Integration and testing	Ongoing
12. Final Notes
OpenAI Compatibility – Users connect SillyTavern to http://proxy:5000/v1 as an OpenAI endpoint. No configuration changes.

Belief System – Facts are labelled so the system knows whether something is objective truth or a character's opinion.

Modular Rules Engine – Users can disable rules entirely for pure narrative ERP, or swap to other game systems.

Save & Load – Campaigns can be exported and imported, preserving all state and configuration.

The system is ready to implement.

UPDATE 1

Users will need to add specific tags to their character cards, system prompts etc
=CHARCTER CARD= A Character card and Stats
=SYSTEM PROMPT= System prompt
=SCENARIO= - The current scenario
=EXAMPLES= - Examples of chat
=USER= - users Stats