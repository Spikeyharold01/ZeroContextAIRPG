# The Adaptive RPG/ERP Engine
# It turns casual AI chatbots into permanent, living RPG worlds that never forget, never break character, and run with unprecedented speed and efficiency.
# Copyright (C) 2026 Spikeyharold01 Stephen Dutton
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
#Contact Details - Stevedutton42@gmail.com
#Source https://github.com/Spikeyharold01/ZeroContextAIRPG
#

"""
config.py

Central configuration system for the Adaptive RPG Engine.

Configuration precedence:
1. Environment Variables (highest priority)
2. engine.toml
3. Dataclass defaults

This module is the ONLY place that knows how configuration is loaded
or saved.

Everywhere else in the engine simply imports:

    from config import settings

and accesses values normally.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Optional

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib

try:
    import tomli_w
except ImportError:
    tomli_w = None


logger = logging.getLogger(__name__)

CONFIG_FILE = Path("engine.toml")


# ==========================================================
# Field Helper
# ==========================================================

def setting(
    default: Any,
    *,
    label: str,
    description: str,
    group: str,
    minimum: Any | None = None,
    maximum: Any | None = None,
    choices: list[str] | None = None,
    secret: bool = False,
):
    """Defines a configurable field together with metadata required by the GUI."""
    return field(
        default=default,
        metadata={
            "label": label,
            "description": description,
            "group": group,
            "min": minimum,
            "max": maximum,
            "choices": choices,
            "secret": secret,
        },
    )


# ==========================================================
# Configuration Dataclasses
# ==========================================================

@dataclass
class DatabaseConfig:
    path: str = setting(
        "data/game.db",
        label="Database Path",
        description="SQLite database used to store memories.",
        group="Database",
    )
    campaign_id: Optional[str] = setting(
        None,
        label="Campaign ID",
        description="Stable UUID copied from the campaign database after first initialization.",
        group="Database",
    )
    archive_path: str = setting(
        "archives",
        label="Archive Directory",
        description="Campaign-relative directory reserved for future verified archives.",
        group="Database",
    )


@dataclass
class StatePatchLimitsConfig:
    max_bytes: int = 32 * 1024
    max_operations: int = 100
    max_value_depth: int = 5
    max_total_keys: int = 100
    max_key_length: int = 128
    max_array_elements_per_operation: int = 1000
    max_apply_milliseconds: Optional[int] = 2000


@dataclass
class StateDocumentLimitsConfig:
    warning_bytes: int = 256 * 1024
    safety_ceiling_bytes: int = 4 * 1024 * 1024
    max_depth: int = 32
    max_total_keys: int = 10000
    max_array_elements: int = 10000
    warning_fraction: float = 0.80
    threshold_action: str = "warn"


@dataclass
class StateSQLiteConfig:
    busy_timeout_ms: int = 5000
    retry_count: int = 3
    retry_backoff_ms: int = 50


@dataclass
class StateGrowthConfig:
    total_campaign_bytes_limit: Optional[int] = None
    max_turns: Optional[int] = None
    max_scenes: Optional[int] = None
    max_entities: Optional[int] = None
    max_facts: Optional[int] = None
    max_events: Optional[int] = None
    max_documents: Optional[int] = None
    max_audit_records: Optional[int] = None
    max_campaign_age_days: Optional[int] = None


@dataclass
class StatePersistenceConfig:
    patch: StatePatchLimitsConfig = field(default_factory=StatePatchLimitsConfig)
    document: StateDocumentLimitsConfig = field(default_factory=StateDocumentLimitsConfig)
    sqlite: StateSQLiteConfig = field(default_factory=StateSQLiteConfig)
    growth: StateGrowthConfig = field(default_factory=StateGrowthConfig)


@dataclass
class StructuredOutputRecoveryConfig:
    enabled: bool = True
    library: str = "json_repair"
    max_input_bytes: int = 1024 * 1024
    max_repair_input_bytes: int = 256 * 1024
    max_attempts: int = 1
    reject_duplicate_keys: bool = True
    reject_multiple_objects: bool = True
    allow_markdown_fence_extraction: bool = True
    max_nesting_depth: int = 32
    max_object_keys: int = 10000
    max_array_elements: int = 10000
    repair_time_warning_ms: int | None = 250
    max_error_summary_characters: int = 500
    secure_debug_raw_output: bool = False
    fail_closed_for_authoritative_state: bool = True

    def __post_init__(self):
        if self.library != "json_repair":
            raise ValueError("structured-output recovery supports only json_repair")
        if type(self.max_attempts) is not int or self.max_attempts != 1:
            raise ValueError("structured-output recovery permits exactly one repair attempt")
        positive = (
            self.max_input_bytes, self.max_repair_input_bytes, self.max_nesting_depth,
            self.max_object_keys, self.max_array_elements, self.max_error_summary_characters,
        )
        if any(type(value) is not int or value <= 0 for value in positive):
            raise ValueError("structured-output recovery limits must be positive integers")
        if self.max_repair_input_bytes > self.max_input_bytes:
            raise ValueError("max_repair_input_bytes cannot exceed max_input_bytes")
        if self.repair_time_warning_ms is not None and self.repair_time_warning_ms <= 0:
            raise ValueError("repair_time_warning_ms must be positive when configured")
        if self.max_error_summary_characters < 32:
            raise ValueError("max_error_summary_characters must be at least 32")
        if not self.reject_duplicate_keys or not self.reject_multiple_objects:
            raise ValueError("duplicate keys and multiple JSON values must be rejected")
        if not self.fail_closed_for_authoritative_state:
            raise ValueError("authoritative structured state must fail closed")


@dataclass
class MemoryConfig:
    similarity: float = setting(
        0.85,
        label="Fact Similarity",
        description="Minimum cosine similarity before two facts are considered identical.",
        group="Memory",
        minimum=0.50,
        maximum=1.00,
    )

    confidence: float = setting(
        0.55,
        label="Emotion Confidence",
        description="Minimum confidence required before emotional updates are accepted.",
        group="Memory",
        minimum=0.0,
        maximum=1.0,
    )


@dataclass
class TokenConfig:
    prompt_target: int = setting(
        4000,
        label="Prompt Budget",
        description="Maximum prompt size supplied to the storyteller model.",
        group="Context",
        minimum=1000,
        maximum=32000,
    )

    working_memory_target: int = setting(
        300,
        label="Working Memory Target",
        description="Approximate token size of working memory.",
        group="Context",
        minimum=50,
        maximum=5000,
    )

    chat_exchange_limit: int = setting(
        6,
        label="Recent Chat Exchanges",
        description="Number of recent exchanges included in context.",
        group="Context",
        minimum=1,
        maximum=50,
    )


@dataclass
class ServerConfig:
    host: str = setting(
        "0.0.0.0",
        label="Server Host",
        description="IP network interface for the proxy server.",
        group="Server",
    )
    port: int = setting(
        5000,
        label="Server Port",
        description="Network port for the proxy server.",
        group="Server",
        minimum=1024,
        maximum=65535,
    )


@dataclass
class RulesEngineConfig:
    enabled: bool = setting(
        True,
        label="Rules Engine Enabled",
        description="Toggle mechanical dice/rules adjudication.",
        group="Rules Engine",
    )
    engine_type: str = setting(
        "dnd_5e",
        label="Rules System",
        description="Active RPG rule system.",
        group="Rules Engine",
        choices=["dnd_5e", "pathfinder", "call_of_cthulhu", "battletech", "off"],
    )


@dataclass
class ParserConfig:
    time_parser_enabled: bool = setting(
        True,
        label="Time Parser Enabled",
        description="Enable deterministic time parser for 'last week', 'yesterday', etc.",
        group="Parsing",
    )

@dataclass
class ModelConfig:
    model_name_or_path: str = setting(
        "",
        label="Model",
        description="Model name or local path.",
        group="Models",
    )

    device: str = setting(
        "cpu",
        label="Device",
        description="Execution device.",
        group="Models",
        choices=["cpu", "cuda"],
    )

    api_key: Optional[str] = setting(
        "",
        label="API Key",
        description="Optional API key.",
        group="Cloud",
        secret=True,
    )

    base_url: Optional[str] = setting(
        "",
        label="Base URL",
        description="OpenAI-compatible endpoint.",
        group="Cloud",
    )


# ==========================================================
# Prompt Markers (NEW)
# ==========================================================

@dataclass
class MarkerConfig:
    """User-defined markers for parsing SillyTavern system prompts."""

    character_card: str = setting(
        "=CHARACTER CARD=",
        label="Character Card Marker",
        description="Marker that identifies the character card section.",
        group="Markers",
    )

    system_prompt: str = setting(
        "=SYSTEM PROMPT=",
        label="System Prompt Marker",
        description="Marker that identifies the system prompt section.",
        group="Markers",
    )

    scenario: str = setting(
        "=SCENARIO=",
        label="Scenario Marker",
        description="Marker that identifies the scenario section.",
        group="Markers",
    )

    examples: str = setting(
        "=EXAMPLES=",
        label="Examples Marker",
        description="Marker that identifies the example messages section.",
        group="Markers",
    )

    user_character: str = setting(
        "=USER=",
        label="User Character Marker",
        description="Marker that identifies the user's own character stats.",
        group="Markers",
    )


# ==========================================================
# Main Engine Config
# ==========================================================

@dataclass
class EngineConfig:
    db: DatabaseConfig = field(default_factory=DatabaseConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    tokens: TokenConfig = field(default_factory=TokenConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    parser: ParserConfig = field(default_factory=ParserConfig)
    rules_engine: RulesEngineConfig = field(default_factory=RulesEngineConfig)
    state_persistence: StatePersistenceConfig = field(default_factory=StatePersistenceConfig)
    structured_output_recovery: StructuredOutputRecoveryConfig = field(
        default_factory=StructuredOutputRecoveryConfig
    )
    markers: MarkerConfig = field(default_factory=MarkerConfig)  # NEW

    embedding_model: ModelConfig = field(
        default_factory=lambda: ModelConfig(
            model_name_or_path="all-MiniLM-L6-v2",
            device="cpu",
        )
    )

    rules_model: ModelConfig = field(
        default_factory=lambda: ModelConfig(
            model_name_or_path="models/dnd-unified-1.5b.Q4_K_M.gguf",
            device="cpu",
        )
    )

    storyteller_model: ModelConfig = field(
        default_factory=lambda: ModelConfig(
            model_name_or_path="meta-llama/Meta-Llama-3-8B-Instruct",
            device="cuda",
        )
    )


# ==========================================================
# Environment Variable Overrides
# ==========================================================

ENVIRONMENT_MAP = {
    # --- Database ---
    "DB_PATH": ("db", "path"),
    "CAMPAIGN_ID": ("db", "campaign_id"),

    # --- Memory Thresholds ---
    "SIMILARITY_THRESHOLD": ("memory", "similarity"),
    "CONFIDENCE_THRESHOLD": ("memory", "confidence"),

    # --- Token & Context Budget ---
    "PROMPT_TOKEN_TARGET": ("tokens", "prompt_target"),
    "WORKING_MEMORY_TARGET": ("tokens", "working_memory_target"),
    "CHAT_EXCHANGE_LIMIT": ("tokens", "chat_exchange_limit"),

    # --- Server Settings ---
    "SERVER_HOST": ("server", "host"),
    "SERVER_PORT": ("server", "port"),

    # --- Rules Engine Settings ---
    "RULES_ENGINE_ENABLED": ("rules_engine", "enabled"),
    "RULES_ENGINE_TYPE": ("rules_engine", "engine_type"),

    # --- Markers (NEW) ---
    "MARKER_CHARACTER_CARD": ("markers", "character_card"),
    "MARKER_SYSTEM_PROMPT": ("markers", "system_prompt"),
    "MARKER_SCENARIO": ("markers", "scenario"),
    "MARKER_EXAMPLES": ("markers", "examples"),
    "MARKER_USER": ("markers", "user_character"),

    # --- Embedding Model ---
    "EMBEDDING_MODEL": ("embedding_model", "model_name_or_path"),
    "EMBEDDING_DEVICE": ("embedding_model", "device"),

    # --- Rules Model ---
    "RULES_MODEL": ("rules_model", "model_name_or_path"),
    "RULES_DEVICE": ("rules_model", "device"),

    # --- Main Storyteller Model ---
    "STORYTELLER_MODEL": ("storyteller_model", "model_name_or_path"),
    "STORYTELLER_DEVICE": ("storyteller_model", "device"),
    "API_KEY": ("storyteller_model", "api_key"),
    "API_BASE_URL": ("storyteller_model", "base_url"),
	"TIME_PARSER_ENABLED": ("parser", "time_parser_enabled"),
}


# ==========================================================
# Utility Functions
# ==========================================================

def _dataclass_to_dict(obj):
    if not is_dataclass(obj):
        return obj

    result = {}
    for f in fields(obj):
        value = getattr(obj, f.name)
        if is_dataclass(value):
            result[f.name] = _dataclass_to_dict(value)
        elif value is not None:
            result[f.name] = value

    return result


def _update_dataclass(instance, values: dict):
    for f in fields(instance):
        if f.name not in values:
            continue

        current = getattr(instance, f.name)
        new_value = values[f.name]

        if is_dataclass(current):
            if isinstance(new_value, dict):
                _update_dataclass(current, new_value)
            continue

        setattr(instance, f.name, new_value)


def _validate(instance):
    if not is_dataclass(instance):
        return

    for f in fields(instance):
        value = getattr(instance, f.name)

        if is_dataclass(value):
            _validate(value)
            continue

        minimum = f.metadata.get("min")
        maximum = f.metadata.get("max")
        choices = f.metadata.get("choices")

        if minimum is not None and value < minimum:
            logger.warning("%s below minimum (%s). Resetting.", f.name, minimum)
            setattr(instance, f.name, minimum)

        if maximum is not None and value > maximum:
            logger.warning("%s above maximum (%s). Resetting.", f.name, maximum)
            setattr(instance, f.name, maximum)

        if choices is not None and value not in choices:
            logger.warning("%s is not a valid option. Resetting to %s", f.name, choices[0])
            setattr(instance, f.name, choices[0])

    if isinstance(instance, StructuredOutputRecoveryConfig):
        instance.__post_init__()


def _load_from_file(instance, config_path: Path = CONFIG_FILE, *, required: bool = False):
    if not config_path.exists():
        if required:
            raise FileNotFoundError(f"campaign configuration does not exist: {config_path}")
        return
    try:
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
        _update_dataclass(instance, data)
    except Exception as e:
        raise RuntimeError(f"failed to load configuration {config_path}: {e}") from e


def _save_to_file(instance, config_path: Path = CONFIG_FILE):
    if tomli_w is None:
        raise RuntimeError("tomli-w is required to save configuration")
    config_path = config_path.resolve()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        data = _dataclass_to_dict(instance)
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=config_path.parent, prefix=f".{config_path.name}.",
            suffix=".tmp", delete=False
        ) as config_file:
            temporary_path = Path(config_file.name)
            tomli_w.dump(data, config_file)
            config_file.flush()
            os.fsync(config_file.fileno())
        os.replace(temporary_path, config_path)
        directory_fd = os.open(config_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception as e:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise RuntimeError(f"failed to save configuration {config_path}: {e}") from e


def _apply_environment_overrides(instance):
    for env_var, path in ENVIRONMENT_MAP.items():
        if env_var in os.environ:
            val = os.environ[env_var]
            
            target = instance
            for p in path[:-1]:
                target = getattr(target, p)
            
            field_name = path[-1]
            current_val = getattr(target, field_name)
            
            try:
                if isinstance(current_val, bool):
                    val = str(val).lower() in ("true", "1", "yes", "y", "on")
                elif isinstance(current_val, int):
                    val = int(val)
                elif isinstance(current_val, float):
                    val = float(val)
                
                setattr(target, field_name, val)
            except ValueError:
                logger.warning("Env var %s invalid type. Expected %s", env_var, type(current_val).__name__)


def _log_summary(config):
    logger.info("=== Engine Configuration Initialized (v6.0) ===")
    logger.info("Server")
    logger.info("  Host:Port   : %s:%d", config.server.host, config.server.port)
    logger.info("Rules Engine")
    logger.info("  Enabled     : %s", config.rules_engine.enabled)
    logger.info("  System Type : %s", config.rules_engine.engine_type)
    logger.info("Memory")
    logger.info("  Similarity  : %.2f", config.memory.similarity)
    logger.info("  Confidence  : %.2f", config.memory.confidence)
    logger.info("Context")
    logger.info("  Prompt Target   : %d", config.tokens.prompt_target)
    logger.info("  Working Memory  : %d", config.tokens.working_memory_target)
    logger.info("  Chat Exchanges  : %d", config.tokens.chat_exchange_limit)
    logger.info("Markers")
    logger.info("  Character Card  : %s", config.markers.character_card)
    logger.info("  System Prompt   : %s", config.markers.system_prompt)
    logger.info("  Scenario        : %s", config.markers.scenario)
    logger.info("  Examples        : %s", config.markers.examples)
    logger.info("  User Character  : %s", config.markers.user_character)
    logger.info("Models")
    logger.info("  Embedding   : %s (%s)", config.embedding_model.model_name_or_path, config.embedding_model.device)
    logger.info("  Rules       : %s (%s)", config.rules_model.model_name_or_path, config.rules_model.device)
    logger.info("  Storyteller : %s (%s)", config.storyteller_model.model_name_or_path, config.storyteller_model.device)
    if config.storyteller_model.base_url:
        logger.info("  Endpoint    : %s", config.storyteller_model.base_url)
    logger.info("==============================================")


# ==========================================================
# Initialization Methods
# ==========================================================

def engine_load(
    cls, config_path: str | Path | None = None, *, required: bool = False,
    apply_environment: bool = True
):
    config = cls()
    selected_path = Path(config_path) if config_path is not None else CONFIG_FILE
    _load_from_file(config, selected_path, required=required)
    if apply_environment:
        _apply_environment_overrides(config)
    _validate(config)

    _log_summary(config)
    return config


def engine_save(self, config_path: str | Path | None = None):
    _validate(self)
    selected_path = Path(config_path) if config_path is not None else CONFIG_FILE
    _save_to_file(self, selected_path)
    reloaded = EngineConfig.load(selected_path, required=True, apply_environment=False)
    if _dataclass_to_dict(reloaded) != _dataclass_to_dict(self):
        raise RuntimeError(f"configuration verification failed after saving {selected_path}")
    return selected_path.resolve()


EngineConfig.load = classmethod(engine_load)
EngineConfig.save = engine_save


def auto_configure() -> EngineConfig:
    return EngineConfig.load()


def initialize_configuration() -> EngineConfig:
    """Load the effective configuration and explicitly persist it."""
    config = EngineConfig.load()
    config.save()
    return config


# ==========================================================
# Global Singleton
# ==========================================================

settings = auto_configure()


# ==========================================================
# Convenience Aliases (for backward compatibility with ingester)
# ==========================================================

MARKER_CHARACTER_CARD = settings.markers.character_card
MARKER_SYSTEM_PROMPT = settings.markers.system_prompt
MARKER_SCENARIO = settings.markers.scenario
MARKER_EXAMPLES = settings.markers.examples
MARKER_USER = settings.markers.user_character

if __name__ == "__main__":
    initialize_configuration()
