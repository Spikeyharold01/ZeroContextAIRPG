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

# proxy_server/ingesters/dnd_ingester.py

"""
D&D 5e Ingester – Clean tag-based parser.

The user provides stats in a structured format with clear tags:
    [D&D STATS]
    [ABILITIES]
    [COMBAT]
    [SAVING_THROWS]
    [SKILLS]
    [PROFICIENCIES]
    [SENSES]
    [FEATURES]
    [MANEUVERS]
    [EQUIPMENT]
    [SPELLCASTING]
    [SPELL_SLOTS]
    [KNOWN_SPELLS]
    [PREPARED_SPELLS]
"""

import re
import logging
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class DnDIngester:
    """
    Parses D&D 5e stats from a tag-based format in the character card.
    Each section is clearly marked with [TAG] and contains key: value pairs.
    """

    # ============================================================
    # Section Parsers
    # ============================================================

    def extract_from_text(self, card_text: str) -> Dict[str, Any]:
        """
        Extract D&D stats from the character card using the tag-based format.
        """
        if not card_text:
            return {}

        # Keep the complete card available to the section parsers. Individual
        # sections are already bounded by the next ``[TAG]`` marker, while a
        # blank line is valid formatting between sections in the documented
        # character-sheet format.
        match = re.search(
            r"\[D&D STATS\]",
            card_text,
            re.IGNORECASE,
        )
        if not match:
            # Fallback: try to parse the whole card
            logger.warning("No [D&D STATS] section found. Parsing whole card.")
            section_text = card_text
        else:
            section_text = card_text[match.start():]

        stats = {}

        # ============================================================
        # 1. CHARACTER BASICS
        # ============================================================
        stats["class"] = self._get_value(section_text, r"CLASS:\s*(.+)")
        stats["subclass"] = self._get_value(section_text, r"SUBCLASS:\s*(.+)")
        stats["level"] = self._get_int(section_text, r"LEVEL:\s*(\d+)")
        stats["race"] = self._get_value(section_text, r"RACE:\s*(.+)")
        stats["alignment"] = self._get_value(section_text, r"ALIGNMENT:\s*(.+)")
        stats["background"] = self._get_value(section_text, r"BACKGROUND:\s*(.+)")
        stats["experience_points"] = self._get_int(section_text, r"EXPERIENCE_POINTS:\s*(\d+)")

        # ============================================================
        # 2. ABILITIES
        # ============================================================
        abilities = self._parse_section(section_text, "ABILITIES")
        ability_names = {
            "STR": "strength",
            "DEX": "dexterity",
            "CON": "constitution",
            "INT": "intelligence",
            "WIS": "wisdom",
            "CHA": "charisma",
        }
        for ability, field_name in ability_names.items():
            if ability in abilities:
                stats[field_name] = self._to_int(abilities[ability])

        # ============================================================
        # 3. COMBAT STATS
        # ============================================================
        combat = self._parse_section(section_text, "COMBAT")
        stats["hp_current"] = self._to_int(combat.get("HP"))
        stats["hp_max"] = self._to_int(combat.get("HP_MAX"))
        stats["armor_class"] = self._to_int(combat.get("ARMOR_CLASS"))
        stats["speed"] = self._to_int(combat.get("SPEED"))
        stats["initiative_bonus"] = self._to_int(combat.get("INITIATIVE"))
        stats["proficiency_bonus"] = self._to_int(combat.get("PROFICIENCY_BONUS"))
        stats["hit_dice"] = combat.get("HIT_DICE", "1d8")

        # ============================================================
        # 4. SAVING THROWS
        # ============================================================
        saves = self._parse_section(section_text, "SAVING_THROWS")
        for save in ["STR", "DEX", "CON", "INT", "WIS", "CHA"]:
            bonus = self._to_int(saves.get(save))
            if bonus:
                stats[f"{save.lower()}_save_bonus"] = bonus
                stats[f"{save.lower()}_save_proficiency"] = True

        # ============================================================
        # 5. SKILLS
        # ============================================================
        skills = self._parse_section(section_text, "SKILLS")
        parsed_skills = {}
        for skill, bonus in skills.items():
            skill_key = skill.lower().replace(" ", "_")
            parsed_skills[skill_key] = {
                "bonus": self._to_int(bonus),
                "proficiency": True
            }
        stats["skills"] = parsed_skills

        # ============================================================
        # 6. PROFICIENCIES
        # ============================================================
        profs = self._parse_section(section_text, "PROFICIENCIES")
        stats["armor_proficiencies"] = self._split_list(profs.get("ARMOR"))
        stats["weapon_proficiencies"] = self._split_list(profs.get("WEAPONS"))
        stats["tool_proficiencies"] = self._split_list(profs.get("TOOLS"))
        stats["language_proficiencies"] = self._split_list(profs.get("LANGUAGES"))

        # ============================================================
        # 7. SENSES
        # ============================================================
        senses = self._parse_section(section_text, "SENSES")
        stats["passive_perception"] = self._to_int(senses.get("PASSIVE_PERCEPTION"))
        stats["darkvision"] = self._to_int(senses.get("DARKVISION"))

        # ============================================================
        # 8. FEATURES
        # ============================================================
        features = self._parse_section(section_text, "FEATURES")
        stats["racial_traits"] = self._split_list(features.get("RACIAL_TRAITS"))
        stats["class_features"] = self._split_list(features.get("CLASS_FEATURES"))
        stats["feats"] = self._split_list(features.get("FEATS"))

        # ============================================================
        # 9. MANEUVERS
        # ============================================================
        maneuvers = self._parse_section(section_text, "MANEUVERS")
        stats["maneuvers"] = list(maneuvers.keys())

        # ============================================================
        # 10. EQUIPMENT
        # ============================================================
        equipment = self._parse_section(section_text, "EQUIPMENT")
        stats["equipment"] = self._parse_equipment_lines(equipment)

        # ============================================================
        # 11. SPELLCASTING
        # ============================================================
        spellcasting = self._parse_section(section_text, "SPELLCASTING")
        stats["spellcasting_ability"] = spellcasting.get("ABILITY", "none")
        stats["spell_save_dc"] = self._to_int(spellcasting.get("SAVE_DC"))
        stats["spell_attack_bonus"] = self._to_int(spellcasting.get("ATTACK_BONUS"))
        stats["cantrips_known"] = self._to_int(spellcasting.get("CANTRIPS_KNOWN"))
        stats["spells_known"] = self._to_int(spellcasting.get("SPELLS_KNOWN"))

        # ============================================================
        # 12. SPELL SLOTS
        # ============================================================
        slots = self._parse_section(section_text, "SPELL_SLOTS")
        for level in range(1, 10):
            stats[f"spell_slots_level_{level}"] = self._to_int(slots.get(f"LEVEL_{level}"))

        # ============================================================
        # 13. KNOWN SPELLS
        # ============================================================
        known = self._parse_section(section_text, "KNOWN_SPELLS")
        stats["known_spells"] = self._split_list(known.get("SPELLS"))

        # ============================================================
        # 14. PREPARED SPELLS
        # ============================================================
        prepared = self._parse_section(section_text, "PREPARED_SPELLS")
        stats["prepared_spells"] = self._split_list(prepared.get("SPELLS"))

        return stats

    # ============================================================
    # Utility Methods
    # ============================================================

    def _get_section(self, text: str, tag: str) -> str:
        """Extract a section by its tag."""
        pattern = rf"\[{tag}\](.*?)(?=\n\[|$)"
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        return match.group(1).strip() if match else ""

    def _parse_section(self, text: str, tag: str) -> Dict[str, str]:
        """Parse a section into key: value pairs."""
        section = self._get_section(text, tag)
        items = {}
        for line in section.split("\n"):
            line = line.strip()
            if ":" in line:
                key, value = line.split(":", 1)
                items[key.strip().upper()] = value.strip()
        return items

    def _get_value(self, text: str, pattern: str) -> Optional[str]:
        """Extract a single value using regex."""
        match = re.search(pattern, text, re.IGNORECASE)
        return match.group(1).strip() if match else None

    def _get_int(self, text: str, pattern: str) -> Optional[int]:
        """Extract a single integer using regex."""
        match = re.search(pattern, text, re.IGNORECASE)
        return int(match.group(1)) if match else None

    def _to_int(self, value: Any) -> Optional[int]:
        """Convert a value to integer safely."""
        if value is None:
            return None
        try:
            return int(value)
        except (ValueError, TypeError):
            return None

    def _split_list(self, value: str) -> List[str]:
        """Split a comma or comma+space separated list."""
        if not value:
            return []
        return [v.strip() for v in re.split(r"[,;\n]+", value) if v.strip()]

    def _parse_equipment_lines(self, equipment_dict: Dict[str, str]) -> List[Dict]:
        """Parse equipment lines into structured objects."""
        equipment = []
        for name, line in equipment_dict.items():
            parts = re.split(r"[|,]", line)
            parts = [p.strip() for p in parts]
            if not parts:
                continue

            item = {"name": name}

            if len(parts) >= 2:
                item["type"] = parts[0].lower()
                if parts[0].lower() == "weapon":
                    if len(parts) >= 5:
                        item["range"] = parts[1]
                        item["attack_bonus"] = self._to_int(parts[2])
                        item["damage_dice"] = parts[3]
                        item["damage_type"] = parts[4]
                elif parts[0].lower() == "armor":
                    item["armor_class"] = self._to_int(parts[1])
                else:
                    item["description"] = line

            equipment.append(item)

        return equipment
