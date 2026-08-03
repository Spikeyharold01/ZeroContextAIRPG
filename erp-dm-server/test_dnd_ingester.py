from pathlib import Path
import re

from dnd_ingester import DnDIngester


README_PATH = Path(__file__).with_name("readme.md")


def documented_character_sheet():
    """Return the complete, blank-line-separated D&D sheet from the README."""
    readme = README_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"\[D&D STATS\].*?\[PREPARED_SPELLS\]\nNone",
        readme,
        re.DOTALL,
    )
    assert match is not None, "The documented D&D character sheet is missing"
    return match.group(0)


def test_extracts_complete_documented_character_sheet():
    stats = DnDIngester().extract_from_text(documented_character_sheet())

    assert stats["class"] == "Fighter"
    assert stats["level"] == 6
    assert stats["strength"] == 16
    assert stats["hp_current"] == 52
    assert stats["armor_class"] == 16
    assert stats["skills"]["acrobatics"] == {
        "bonus": 6,
        "proficiency": True,
    }
    assert stats["skills"]["sleight_of_hand"]["bonus"] == 3

    longsword = next(
        item for item in stats["equipment"] if item["name"] == "LONGSWORD +1"
    )
    assert longsword == {
        "name": "LONGSWORD +1",
        "type": "weapon",
        "range": "melee",
        "attack_bonus": 7,
        "damage_dice": "1d8+4",
        "damage_type": "slashing",
    }

    assert stats["spellcasting_ability"] == "None"
    assert stats["spell_save_dc"] == 10
    assert stats["spell_attack_bonus"] == 4
    assert stats["spell_slots_level_1"] == 0
    assert stats["spell_slots_level_9"] == 0
    assert stats["known_spells"] == []
    assert stats["prepared_spells"] == []


def test_missing_optional_sections_return_empty_or_default_values():
    stats = DnDIngester().extract_from_text(
        """[D&D STATS]
CLASS: Rogue
LEVEL: 2

[ABILITIES]
STR: 9
"""
    )

    assert stats["class"] == "Rogue"
    assert stats["level"] == 2
    assert stats["strength"] == 9
    assert stats["skills"] == {}
    assert stats["equipment"] == []
    assert stats["spellcasting_ability"] == "none"
    assert stats["spell_save_dc"] is None
    assert stats["known_spells"] == []
    assert stats["prepared_spells"] == []


def test_malformed_numeric_values_are_returned_as_none():
    stats = DnDIngester().extract_from_text(
        """[D&D STATS]
CLASS: Wizard
LEVEL: six

[ABILITIES]
STR: strong

[COMBAT]
HP: many
ARMOR_CLASS: unknown

[SPELLCASTING]
SAVE_DC: high
ATTACK_BONUS: excellent
"""
    )

    assert stats["class"] == "Wizard"
    assert stats["level"] is None
    assert stats["strength"] is None
    assert stats["hp_current"] is None
    assert stats["armor_class"] is None
    assert stats["spell_save_dc"] is None
    assert stats["spell_attack_bonus"] is None
