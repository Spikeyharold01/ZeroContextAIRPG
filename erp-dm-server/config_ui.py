"""
config_ui.py

Browser-based configuration editor for Adaptive RPG Engine.

Run:

    python config_ui.py

Then open:

    http://127.0.0.1:8080
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Any

from nicegui import ui

from config import settings, EngineConfig


# ==========================================================
# UI State
# ==========================================================

WIDGETS: dict[str, Any] = {}


# ==========================================================
# Sections exposed to the user
# ==========================================================

SECTIONS = [
    ("Database", "db", settings.db),
    ("Memory", "memory", settings.memory),
    ("Context", "tokens", settings.tokens),
    ("Server", "server", settings.server),                      # NEW
    ("Rules Engine", "rules_engine", settings.rules_engine),
    ("Markers", "markers", settings.markers),                   # NEW
    ("Embedding Model", "embedding_model", settings.embedding_model),
    ("Rules Model", "rules_model", settings.rules_model),
    ("Storyteller Model", "storyteller_model", settings.storyteller_model),
]


# ==========================================================
# Visual helpers
# ==========================================================

ICONS = {
    "Database": "storage",
    "Memory": "psychology",
    "Context": "memory",
    "Server": "dns",                                           # NEW
    "Rules Engine": "gavel",
    "Markers": "tag",                                          # NEW
    "Embedding Model": "search",
    "Rules Model": "gavel",
    "Storyteller Model": "auto_stories",
}


# ==========================================================
# Metadata reader
# ==========================================================

def get_metadata(field):
    return {
        "label": field.metadata.get(
            "label",
            field.name,
        ),
        "description": field.metadata.get(
            "description",
            "",
        ),
        "minimum": field.metadata.get(
            "min",
        ),
        "maximum": field.metadata.get(
            "max",
        ),
        "choices": field.metadata.get(
            "choices",
        ),
        "secret": field.metadata.get(
            "secret",
            False,
        ),
    }


# ==========================================================
# Widget creation
# ==========================================================

def create_setting_widget(
    path: str,
    value,
    field,
):
    metadata = get_metadata(field)

    ui.label(
        metadata["label"]
    ).classes(
        "font-bold"
    )

    if metadata["description"]:
        ui.label(
            metadata["description"]
        ).classes(
            "text-sm text-grey"
        )

    choices = metadata["choices"]

    if choices:
        widget = ui.select(
            choices,
            value=value,
        )
    elif isinstance(value, bool):
        widget = ui.switch(
            value=value,
        )
    elif isinstance(value, int):
        widget = ui.number(
            value=value,
            min=metadata["minimum"],
            max=metadata["maximum"],
            precision=0,
        )
    elif isinstance(value, float):
        widget = ui.number(
            value=value,
            min=metadata["minimum"],
            max=metadata["maximum"],
            precision=3,
        )
    elif metadata["secret"]:
        widget = ui.input(
            value=value,
            password=True,
            password_toggle_button=True,
        )
    else:
        widget = ui.input(
            value=value,
        )

    widget.classes(
        "w-full"
    )

    WIDGETS[path] = widget


# ==========================================================
# Build Configuration Sections
# ==========================================================

def build_section(
    title: str,
    prefix: str,
    obj,
):
    """
    Creates one configuration card.
    """
    with ui.card().classes(
        "w-full shadow-md rounded-xl p-4"
    ):
        with ui.row().classes(
            "items-center"
        ):
            ui.icon(
                ICONS.get(title, "settings")
            ).classes(
                "text-2xl text-primary"
            )
            ui.label(
                title
            ).classes(
                "text-xl font-bold"
            )

        ui.separator()

        for field in fields(obj):
            value = getattr(
                obj,
                field.name,
            )
            path = f"{prefix}.{field.name}"

            with ui.column().classes(
                "w-full q-mb-md"
            ):
                create_setting_widget(
                    path,
                    value,
                    field,
                )


# ==========================================================
# Configuration Update Functions
# ==========================================================

def update_section(
    prefix: str,
    obj,
):
    """
    Copy values from GUI widgets back into config.
    """
    for field in fields(obj):
        path = f"{prefix}.{field.name}"
        widget = WIDGETS.get(path)
        if widget is not None:
            setattr(
                obj,
                field.name,
                widget.value,
            )


def refresh_section(
    prefix: str,
    obj,
):
    """
    Reload GUI values from config object.
    """
    for field in fields(obj):
        path = f"{prefix}.{field.name}"
        widget = WIDGETS.get(path)
        if widget is not None:
            widget.value = getattr(
                obj,
                field.name,
            )


# ==========================================================
# Save / Reload Actions
# ==========================================================

def save_configuration():
    for title, prefix, obj in SECTIONS:
        update_section(
            prefix,
            obj,
        )

    settings.save()

    ui.notify(
        "Configuration saved.",
        color="positive",
    )


def reload_configuration():
    new_settings = EngineConfig.load()

    for title, prefix, obj in SECTIONS:
        new_obj = getattr(
            new_settings,
            prefix,
        )
        refresh_section(
            prefix,
            new_obj,
        )

        # update current settings object
        for field in fields(obj):
            setattr(
                obj,
                field.name,
                getattr(
                    new_obj,
                    field.name,
                ),
            )

    ui.notify(
        "Configuration reloaded.",
        color="primary",
    )


def reset_defaults():
    defaults = EngineConfig()

    for title, prefix, obj in SECTIONS:
        default_obj = getattr(
            defaults,
            prefix,
        )

        for field in fields(obj):
            setattr(
                obj,
                field.name,
                getattr(
                    default_obj,
                    field.name,
                ),
            )

        refresh_section(
            prefix,
            obj,
        )

    ui.notify(
        "Defaults restored. Save to keep changes.",
        color="warning",
    )


# ==========================================================
# Main Page
# ==========================================================

@ui.page("/")
def configuration_page():
    with ui.column().classes(
        "w-full max-w-5xl mx-auto p-6 gap-6"
    ):
        # Header
        with ui.card().classes(
            "w-full shadow-md rounded-xl p-6"
        ):
            ui.label(
                "Adaptive RPG Engine"
            ).classes(
                "text-3xl font-bold"
            )
            ui.label(
                "Configuration Manager"
            ).classes(
                "text-lg"
            )
            ui.label(
                "Edit settings and save them to engine.toml."
            ).classes(
                "text-sm text-grey"
            )

        # Configuration cards
        for title, prefix, obj in SECTIONS:
            build_section(
                title,
                prefix,
                obj,
            )

        # Action buttons
        with ui.row().classes(
            "w-full justify-center gap-4"
        ):
            ui.button(
                "Save Configuration",
                icon="save",
                on_click=save_configuration,
            )
            ui.button(
                "Reload",
                icon="refresh",
                on_click=reload_configuration,
            )
            ui.button(
                "Reset Defaults",
                icon="restart_alt",
                on_click=reset_defaults,
            )

        ui.separator()

        ui.label(
            "Adaptive RPG Engine Configuration Interface"
        ).classes(
            "text-sm text-grey"
        )


# ==========================================================
# Start Web Server
# ==========================================================

ui.run(
    host="127.0.0.1",
    port=8080,
    title="Adaptive RPG Engine Configuration",
)