"""Optional global keyboard shortcut.

Wayland doesn't let apps grab global keys, so NYXO registers a GNOME custom
keybinding (the supported mechanism) that runs `nyxo --chat`. The existing
custom-keybindings list is backed up before it is changed, and only NYXO's own
entry is ever added/removed."""
from __future__ import annotations

import ast
import time

from ..core import paths
from ..system import runner
from ..core import brand

SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
KEY = "custom-keybindings"
ENTRY = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/nyxo/"
ENTRY_SCHEMA = f"{SCHEMA}.custom-keybinding:{ENTRY}"
DEFAULT_BINDING = "<Super><Alt>n"


def _get_list() -> list[str]:
    r = runner.run(["gsettings", "get", SCHEMA, KEY], timeout=5)
    if not r.ok:
        raise RuntimeError(r.err.strip() or "gsettings failed")
    raw = r.out.strip()
    if raw.startswith("@as"):
        raw = raw[3:].strip()
    return list(ast.literal_eval(raw)) if raw else []


def _set_list(items: list[str]) -> None:
    val = "[" + ", ".join(f"'{i}'" for i in items) + "]"
    r = runner.run(["gsettings", "set", SCHEMA, KEY, val], timeout=5)
    if not r.ok:
        raise RuntimeError(r.err.strip())


def _backup(items: list[str]) -> None:
    d = paths.config_dir() / "backups"
    d.mkdir(parents=True, exist_ok=True)
    (d / time.strftime("custom-keybindings-%Y%m%d-%H%M%S.txt")).write_text(repr(items) + "\n")


def installed() -> str | None:
    try:
        if ENTRY not in _get_list():
            return None
    except RuntimeError:
        return None
    r = runner.run(["gsettings", "get", ENTRY_SCHEMA, "binding"], timeout=5)
    return r.out.strip().strip("'") if r.ok else None


def install(command: str, binding: str = DEFAULT_BINDING) -> str:
    items = _get_list()
    _backup(items)
    for k, v in (("name", f"{brand.NAME} chat"), ("command", command), ("binding", binding)):
        r = runner.run(["gsettings", "set", ENTRY_SCHEMA, k, v], timeout=5)
        if not r.ok:
            raise RuntimeError(r.err.strip())
    if ENTRY not in items:
        _set_list(items + [ENTRY])
    return binding


def remove() -> None:
    items = _get_list()
    if ENTRY in items:
        _backup(items)
        _set_list([i for i in items if i != ENTRY])
    for k in ("name", "command", "binding"):
        runner.run(["gsettings", "reset", ENTRY_SCHEMA, k], timeout=5)
