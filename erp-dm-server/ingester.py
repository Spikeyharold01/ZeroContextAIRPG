# proxy_server/ingester.py

"""
Ingester for SillyTavern's OpenAI-compatible payload.

This module extracts structured data from the raw messages using
user-defined markers. It validates required sections and returns
a clean context dictionary for the rest of the pipeline.

Markers (configurable via settings):
    =CHARACTER CARD=   : Required. The full character description.
    =SYSTEM PROMPT=    : Optional. System instructions for the Main LLM.
    =SCENARIO=         : Optional. The current scenario / world state.
    =EXAMPLES=         : Optional. Example dialogue for few-shot learning.
    =USER=             : Optional. The user's own character stats (for games like D&D).
"""

import re
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

from config import settings

logger = logging.getLogger(__name__)


@dataclass
class IngestedContext:
    """Structured context after ingestion."""
    user_message: str
    chat_history: List[Dict[str, str]]
    system_prompt: str
    character_card_text: str
    character_name: str
    scenario: str
    examples: str
    user_character: str
    is_first_message: bool
    raw_system_prompts: List[Dict[str, str]]
    sampling_params: Dict[str, Any]
    raw_payload: Dict[str, Any]


class Ingester:
    """
    Extracts structured data from SillyTavern's OpenAI-compatible payload
    using user-defined markers.
    """

    def __init__(self):
        # Load markers from config (correct nested access)
        self.marker_character_card = settings.markers.character_card
        self.marker_system_prompt = settings.markers.system_prompt
        self.marker_scenario = settings.markers.scenario
        self.marker_examples = settings.markers.examples
        self.marker_user = settings.markers.user_character

        # Fallback patterns for character card detection (if marker missing)
        self.fallback_patterns = {
            "character_card": [
                r"\[Identity:.*?\]",
                r"\[Name:.*?\]",
                r"\[Personality:.*?\]",
            ]
        }

        logger.info(f"Ingester initialized with markers: "
                    f"card='{self.marker_character_card}', "
                    f"user='{self.marker_user}'")

    def ingest(self, payload: Dict[str, Any]) -> IngestedContext:
        """
        Main entry point: process the raw SillyTavern payload.
        """
        messages = payload.get("messages", [])

        user_message = self._extract_user_message(messages)
        chat_history = self._extract_chat_history(messages)
        system_prompts = self._extract_system_prompts(messages)
        is_first_message = self._is_first_message(chat_history)
        sampling_params = self._extract_sampling_params(payload)

        # Extract sections using markers
        extracted = self._extract_sections(system_prompts)

        # Validate required sections
        errors = []
        warnings = []

        character_card_text = extracted.get("character_card", "")
        if not character_card_text:
            errors.append(
                f"Required section '{self.marker_character_card}' not found. "
                "Please add this marker before your character card."
            )

        system_prompt = extracted.get("system_prompt", "")
        if not system_prompt:
            warnings.append(
                f"Optional section '{self.marker_system_prompt}' not found. "
                "Proceeding without a system prompt."
            )

        scenario = extracted.get("scenario", "")
        if not scenario:
            logger.info(f"Optional section '{self.marker_scenario}' not found.")

        examples = extracted.get("examples", "")
        if not examples:
            logger.info(f"Optional section '{self.marker_examples}' not found.")

        user_character = extracted.get("user_character", "")
        if not user_character:
            logger.info(f"Optional section '{self.marker_user}' not found.")

        if errors:
            error_msg = "\n".join(errors)
            logger.error(error_msg)
            raise ValueError(error_msg)

        for warn in warnings:
            logger.warning(warn)

        character_name = self._extract_name_from_card(character_card_text)
        if character_name == "Unknown":
            logger.warning("Could not extract character name from the card.")

        return IngestedContext(
            user_message=user_message,
            chat_history=chat_history,
            system_prompt=system_prompt,
            character_card_text=character_card_text,
            character_name=character_name,
            scenario=scenario,
            examples=examples,
            user_character=user_character,
            is_first_message=is_first_message,
            raw_system_prompts=system_prompts,
            sampling_params=sampling_params,
            raw_payload=payload,
        )

    def _extract_sections(self, system_prompts: List[Dict]) -> Dict[str, str]:
        """Extract each section by looking for the marker at the start."""
        result = {
            "character_card": "",
            "system_prompt": "",
            "scenario": "",
            "examples": "",
            "user_character": "",
        }

        for prompt in system_prompts:
            content = prompt.get("content", "")
            if content.startswith(self.marker_character_card):
                result["character_card"] = content[len(self.marker_character_card):].strip()
            elif content.startswith(self.marker_system_prompt):
                result["system_prompt"] = content[len(self.marker_system_prompt):].strip()
            elif content.startswith(self.marker_scenario):
                result["scenario"] = content[len(self.marker_scenario):].strip()
            elif content.startswith(self.marker_examples):
                result["examples"] = content[len(self.marker_examples):].strip()
            elif content.startswith(self.marker_user):
                result["user_character"] = content[len(self.marker_user):].strip()

        return result

    def _try_fallback_character_card(self, system_prompts: List[Dict]) -> str:
        """
        Attempt to find a character card using regex patterns.
        This is only a fallback if the user didn't use the marker.
        Returns the card text or empty string.
        """
        for prompt in system_prompts:
            content = prompt.get("content", "")
            # Skip obvious non-card system prompts
            if "Write" in content and "reply" in content:
                continue
            if "[Start a new Chat]" in content:
                continue
            if content.startswith('"') and content.endswith('"'):
                continue
            # Check if it contains any identity/name patterns
            for pattern in self.fallback_patterns["character_card"]:
                if re.search(pattern, content, re.IGNORECASE):
                    return content
        return ""

    def _extract_name_from_card(self, card_text: str) -> str:
        """
        Extract the character name from the card text using common patterns.
        """
        if not card_text:
            return "Unknown"

        # Pattern 1: [Identity: Tanis Half-Elven;]
        match = re.search(r"\[Identity:\s*([^\];]+)", card_text)
        if match:
            return match.group(1).strip()

        # Pattern 2: [Name: Tanis]
        match = re.search(r"\[Name:\s*([^\]]+)\]", card_text)
        if match:
            return match.group(1).strip()

        # Pattern 3: "Name: Tanis" (without brackets)
        match = re.search(r"Name:\s*([^\n]+)", card_text)
        if match:
            return match.group(1).strip()

        # Pattern 4: First line that looks like a proper name (single line, capitalised)
        lines = card_text.split("\n")
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # Skip obvious non-name lines
            if line.startswith("[") or line.startswith("("):
                continue
            # Check if it's a short line with capital letters
            words = line.split()
            if 1 <= len(words) <= 5 and all(w[0].isupper() for w in words if w):
                return line

        return "Unknown"

    # ------------------------------------------------------------------
    # Standard extraction methods (unchanged)
    # ------------------------------------------------------------------

    def _extract_user_message(self, messages: List[Dict]) -> str:
        """Extract the last user message."""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                return msg.get("content", "")
        return ""

    def _extract_chat_history(self, messages: List[Dict]) -> List[Dict]:
        """Extract all messages except the last user message."""
        history = []
        last_user_index = -1
        for i, msg in enumerate(messages):
            if msg.get("role") == "user":
                last_user_index = i
        # Keep all messages up to (but not including) the last user message
        for i, msg in enumerate(messages):
            if i == last_user_index:
                continue
            history.append(msg)
        return history

    def _extract_system_prompts(self, messages: List[Dict]) -> List[Dict]:
        """Extract all system messages."""
        return [msg for msg in messages if msg.get("role") == "system"]

    def _is_first_message(self, chat_history: List[Dict]) -> bool:
        """Determine if this is the first user message."""
        for msg in chat_history:
            if msg.get("role") == "assistant":
                return False
        return True

    def _extract_sampling_params(self, payload: Dict) -> Dict:
        """Extract all sampling parameters from the payload."""
        return {
            "temperature": payload.get("temperature", 0.85),
            "top_p": payload.get("top_p", 0.92),
            "top_k": payload.get("top_k", 40),
            "max_tokens": payload.get("max_tokens", 600),
            "presence_penalty": payload.get("presence_penalty", 0.0),
            "frequency_penalty": payload.get("frequency_penalty", 0.0),
            "stream": payload.get("stream", True),
        }

    # ------------------------------------------------------------------
    # Optional: Parse character card into structured sections
    # (Called by router if needed)
    # ------------------------------------------------------------------

    def parse_character_card(self, card_text: str) -> Dict[str, str]:
        """
        Parse the character card into structured sections.
        This is a helper that the router can call after ingestion.
        """
        if not card_text:
            return {}

        sections = {}

        # Extract common sections using regex
        section_patterns = {
            "identity": r"\[Identity:\s*([^\]]+)\]",
            "personality": r"\[Personality:\s*([^\]]+)\]",
            "physical_appearance": r"\[Physical Appearance:\s*([^\]]+)\]",
            "speech": r"\[Speech:\s*([^\]]+)\]",
            "loves_bonds": r"\[Loves/Bonds:\s*([^\]]+)\]",
            "dnd_sheet": r"\[D&D 5E CHARACTER SHEET\](.*?)(?=\n\n|$)",
        }

        for name, pattern in section_patterns.items():
            match = re.search(pattern, card_text, re.IGNORECASE | re.DOTALL)
            if match:
                sections[name] = match.group(1).strip()

        return sections