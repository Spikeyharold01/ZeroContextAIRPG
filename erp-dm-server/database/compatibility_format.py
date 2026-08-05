"""Trusted constants for legacy compatibility document extraction and reading."""

from __future__ import annotations

from uuid import UUID

COMPATIBILITY_FORMAT_VERSION = "legacy-sqlite-row.v1"
EXTRACTION_SCHEMA_VERSION = 8
EXTRACTOR_REVISION = "legacy-state-extractor.v1"
METADATA_OWNER = "legacy-state-extractor.v1"
DETERMINISTIC_ID_NAMESPACE = UUID("72a62b13-f476-51ad-a2c4-f3c90bb03e5b")
DETERMINISTIC_ID_NAMESPACE_VERSION = "legacy-state-extraction-uuidv5.v1"

COMPATIBILITY_NAMESPACES = frozenset({
    "legacy.world-state.v1",
    "legacy.world-additional-state.v1",
    "legacy.character-narrative.v1",
    "legacy.character-plot.v1",
    "legacy.character-plot-state.v1",
    "legacy.ambiance.v1",
    "legacy.emotional-state.v1",
    "legacy.mechanical-stats.v1",
    "rules.dnd5e.legacy-v1",
    "legacy.inventory-mechanics.v1",
    "legacy.relationship-state.v1",
    "legacy.scene-graph.v1",
    "legacy.game-state.v1",
    "legacy.combat-state.v1",
    "legacy.unknown-columns.v1",
})
