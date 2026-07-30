import os
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ==========================================
# 1. DATABASE CONFIGURATION
# ==========================================
@dataclass
class DatabaseConfig:
    path: str = os.getenv("DB_PATH", "data/game.db")

# ==========================================
# 2. THRESHOLD CONFIGURATION
# ==========================================
@dataclass
class ThresholdConfig:
    # Used for matching new 8B facts against existing active facts via cosine similarity
    similarity: float = float(os.getenv("SIMILARITY_THRESHOLD", "0.85"))
    
    # Used by the proxy to discard hallucinated emotional swings
    confidence: float = float(os.getenv("CONFIDENCE_THRESHOLD", "0.55"))

# ==========================================
# 3. TOKEN BUDGET & CONTEXT CONFIGURATION
# ==========================================
@dataclass
class TokenConfig:
    # Surgical prompt maximum token limit to prevent Context Overflow
    prompt_target: int = int(os.getenv("PROMPT_TOKEN_TARGET", "4000"))
    
    # The raw prose style-anchor stored in Working Memory
    working_memory_target: int = int(os.getenv("WORKING_MEMORY_TARGET", "300"))
    
    # How many recent user/character exchanges to include in the context
    chat_exchange_limit: int = int(os.getenv("CHAT_EXCHANGE_LIMIT", "6"))

# ==========================================
# 4. MODEL CONFIGURATIONS
# ==========================================
@dataclass
class ModelConfig:
    """Generic configuration block for an AI Model."""
    model_name_or_path: str
    device: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None

@dataclass
class EngineConfig:
    """The master configuration object for the Adaptive RPG/ERP Engine v5.2."""
    
    db: DatabaseConfig = field(default_factory=DatabaseConfig)
    thresholds: ThresholdConfig = field(default_factory=ThresholdConfig)
    tokens: TokenConfig = field(default_factory=TokenConfig)
    
    # -- A. Embedding Model (Semantic Memory / Fact Matching) --
    # e.g., sentence-transformers/all-MiniLM-L6-v2 runs perfectly on CPU
    embedding_model: ModelConfig = field(default_factory=lambda: ModelConfig(
        model_name_or_path=os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
        device=os.getenv("EMBEDDING_DEVICE", "cpu")
    ))
    
    # -- B. Rules Model (1.5B DnD-Unified) --
    # Used for deterministic adjudication of skill checks and combat
    rules_model: ModelConfig = field(default_factory=lambda: ModelConfig(
        model_name_or_path=os.getenv("RULES_MODEL", "local/1.5B-DnD-Unified"),
        device=os.getenv("RULES_DEVICE", "cpu") # Offload to CPU to save VRAM for the 8B
    ))
    
    # -- C. Main Storyteller Model (8B+) --
    # Used for prose generation and structured JSON state updates
    storyteller_model: ModelConfig = field(default_factory=lambda: ModelConfig(
        model_name_or_path=os.getenv("STORYTELLER_MODEL", "meta-llama/Meta-Llama-3-8B-Instruct"),
        device=os.getenv("STORYTELLER_DEVICE", "cuda"),
        api_key=os.getenv("API_KEY", None),               # Set if using RunPod/OpenRouter
        base_url=os.getenv("API_BASE_URL", None)          # Set if using a cloud endpoint like vLLM
    ))


def auto_configure() -> EngineConfig:
    """
    Initializes and validates the configuration engine.
    Called by main.py at application startup.
    """
    config = EngineConfig()
    
    # Print out a startup summary to confirm settings
    logger.info("=== Engine Configuration Initialized ===")
    logger.info(f"Database Path    : {config.db.path}")
    logger.info(f"Fact Similarity  : > {config.thresholds.similarity}")
    logger.info(f"Emotion Conf.    : > {config.thresholds.confidence}")
    logger.info(f"Max Context Size : {config.tokens.prompt_target} tokens")
    logger.info(f"Recent Exchanges : {config.tokens.chat_exchange_limit}")
    logger.info(f"Embedding Model  : {config.embedding_model.model_name_or_path} ({config.embedding_model.device})")
    logger.info(f"Rules Model      : {config.rules_model.model_name_or_path} ({config.rules_model.device})")
    
    cloud_msg = f" (Cloud Proxy via {config.storyteller_model.base_url})" if config.storyteller_model.base_url else " (Local Execution)"
    logger.info(f"Main Storyteller : {config.storyteller_model.model_name_or_path} on {config.storyteller_model.device}{cloud_msg}")
    
    return config

# Create a singleton instance that can be imported throughout the app
# Usage: from config import settings
settings = auto_configure()