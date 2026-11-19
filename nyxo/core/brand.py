"""The assistant's display name (user-configurable; default "Kings").

Internal identifiers (package `nyxo`, service `nyxo.service`, config folders)
stay unchanged so settings, data and autostart keep working across renames.
The name is read from settings before the UI modules are imported; changing
it in Settings applies after a restart."""
from __future__ import annotations

DEFAULT = "Kings"
NAME = DEFAULT


def set_name(name: str | None) -> str:
    global NAME
    name = (name or "").strip()
    NAME = name[:24] if name else DEFAULT
    return NAME
