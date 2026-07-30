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
# Field helper
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
    """
    Defines a configurable field together with all metadata
    required by the GUI.
    """

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
# Database
# ==========================================================

@dataclass
class DatabaseConfig:

    path: str = setting(
        "data/game.db",
        label="Database Path",
        description="SQLite database used to store memories.",
        group="Database",
    )


# ==========================================================
# Thresholds
# ==========================================================

@dataclass
class MemoryConfig:
    """
    Controls semantic memory behaviour.
    """

    similarity: float = setting(
        0.85,
        label="Fact Similarity",
        description="Minimum cosine similarity before two facts are considered the same.",
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

    # Future expansion:
    #
    # decay_rate
    # summarisation_interval
    # max_active_memories
    # importance_multiplier


# ==========================================================
# Token Budget
# ==========================================================

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


# ==========================================================
# AI Model
# ==========================================================

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
# Engine
# ==========================================================

@dataclass
class EngineConfig:

    db: DatabaseConfig = field(default_factory=DatabaseConfig)

    memory: MemoryConfig = field(default_factory=MemoryConfig)

    tokens: TokenConfig = field(default_factory=TokenConfig)

    embedding_model: ModelConfig = field(
        default_factory=lambda: ModelConfig(
            model_name_or_path="all-MiniLM-L6-v2",
            device="cpu",
        )
    )

    rules_model: ModelConfig = field(
        default_factory=lambda: ModelConfig(
            model_name_or_path="local/1.5B-DnD-Unified",
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
# Utility Functions
# ==========================================================

def _dataclass_to_dict(obj):
    """
    Recursively converts dataclasses into dictionaries suitable
    for writing to TOML.
    """

    if not is_dataclass(obj):
        return obj

    result = {}

    for f in fields(obj):
        value = getattr(obj, f.name)

        if is_dataclass(value):
            result[f.name] = _dataclass_to_dict(value)
        else:
            result[f.name] = value

    return result


def _update_dataclass(instance, values: dict):
    """
    Recursively updates a dataclass using values loaded from TOML.
    Unknown fields are ignored so older configuration files remain
    compatible with newer engine versions.
    """

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


# ==========================================================
# Validation
# ==========================================================

def _validate(instance):
    """
    Validate values using metadata attached to each field.
    """

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
            logger.warning(
                "%s below minimum (%s). Resetting.",
                f.name,
                minimum,
            )
            setattr(instance, f.name, minimum)

        if maximum is not None and value > maximum:
            logger.warning(
                "%s above maximum (%s). Resetting.",
                f.name,
                maximum,
            )
            setattr(instance, f.name, maximum)

        if choices is not None:
            if value not in choices:
                logger.warning(
                    "%s is not a valid option. Resetting to %s",
                    f.name,
                    choices[0],
                )
                setattr(instance, f.name, choices[0])


# ==========================================================
# Environment Variable Overrides
# ==========================================================

ENVIRONMENT_MAP = {

    "DB_PATH":
        ("db", "path"),

"SIMILARITY_THRESHOLD":
    ("memory", "similarity"),

"CONFIDENCE_THRESHOLD":
    ("memory", "confidence"),

    "PROMPT_TOKEN_TARGET":
        ("tokens", "prompt_target"),

    "WORKING_MEMORY_TARGET":
        ("tokens", "working_memory_target"),

    "CHAT_EXCHANGE_LIMIT":
        ("tokens", "chat_exchange_limit"),

    "EMBEDDING_MODEL":
        ("embedding_model", "model_name_or_path"),

    "EMBEDDING_DEVICE":
        ("embedding_model", "device"),

    "RULES_MODEL":
        ("rules_model", "model_name_or_path"),

    "RULES_DEVICE":
        ("rules_model", "device"),

    "STORYTELLER_MODEL":
        ("storyteller_model", "model_name_or_path"),

    "STORYTELLER_DEVICE":
        ("storyteller_model", "device"),

    "API_KEY":
        ("storyteller_model", "api_key"),

"API_BASE_URL":
        ("storyteller_model", "base_url"),
}

# ==========================================================
# Missing File & Environment Functions
# ==========================================================

def _load_from_file(instance):
    """Loads configuration from TOML file if it exists."""
    if not CONFIG_FILE.exists():
        return
    try:
        with open(CONFIG_FILE, "rb") as f:
            data = tomllib.load(f)
        _update_dataclass(instance, data)
    except Exception as e:
        logger.error("Failed to load config file: %s", e)

def _save_to_file(instance):
    """Saves current configuration to TOML file."""
    if tomli_w is None:
        logger.warning("tomli-w not installed. Cannot save config to %s", CONFIG_FILE)
        return
    try:
        data = _dataclass_to_dict(instance)
        with open(CONFIG_FILE, "wb") as f:
            tomli_w.dump(data, f)
    except Exception as e:
        logger.error("Failed to save config file: %s", e)

def _apply_environment_overrides(instance):
    """Applies environment variables, casting them to the correct types."""
    for env_var, path in ENVIRONMENT_MAP.items():
        if env_var in os.environ:
            val = os.environ[env_var]
            
            # Navigate to the correct nested dataclass
            target = instance
            for p in path[:-1]:
                target = getattr(target, p)
            
            field_name = path[-1]
            current_val = getattr(target, field_name)
            
            # Cast the string env var to match the existing dataclass type
            try:
                if isinstance(current_val, int):
                    val = int(val)
                elif isinstance(current_val, float):
                    val = float(val)
                elif isinstance(current_val, bool):
                    val = str(val).lower() in ("true", "1", "yes", "y", "on")
                
                setattr(target, field_name, val)
            except ValueError:
                logger.warning("Env var %s invalid type. Expected %s", env_var, type(current_val).__name__)

def _log_summary(config):
    """Prints a clean summary of the active configuration."""
    logger.info("=== Engine Configuration Initialized ===")
    logger.info("Memory")
    logger.info("  Similarity : %.2f", config.memory.similarity)
    logger.info("  Confidence : %.2f", config.memory.confidence)
    logger.info("Context")
    logger.info("  Prompt Target : %d", config.tokens.prompt_target)
    logger.info("  Working Memory : %d", config.tokens.working_memory_target)
    logger.info("  Chat Exchanges : %d", config.tokens.chat_exchange_limit)
    logger.info("Embedding")
    logger.info("  %s (%s)", config.embedding_model.model_name_or_path, config.embedding_model.device)
    logger.info("Rules")
    logger.info("  %s (%s)", config.rules_model.model_name_or_path, config.rules_model.device)
    logger.info("Storyteller")
    logger.info("  %s (%s)", config.storyteller_model.model_name_or_path, config.storyteller_model.device)
    if config.storyteller_model.base_url:
        logger.info("  Endpoint : %s", config.storyteller_model.base_url)
    logger.info("==========================================")


# ------------------------------------------------------------------
# Monkey-patch convenience methods onto EngineConfig
# ------------------------------------------------------------------

def engine_load(cls):
    config = cls()

    _load_from_file(config)

    _apply_environment_overrides(config)

    _validate(config)

    # Automatically create a config file on first run
    if not CONFIG_FILE.exists():
        logger.info("Creating default engine.toml")
        _save_to_file(config)

    _log_summary(config)

    return config


def engine_save(self):
    _validate(self)
    _save_to_file(self)


EngineConfig.load = classmethod(engine_load)
EngineConfig.save = engine_save


# ==========================================================
# Backwards Compatibility
# ==========================================================

def auto_configure() -> EngineConfig:
    """
    Preserved for backwards compatibility with the
    original engine.

    Existing code can continue calling:

        settings = auto_configure()

    or simply:

        from config import settings
    """
    return EngineConfig.load()


# ==========================================================
# Singleton
# ==========================================================

settings = auto_configure()