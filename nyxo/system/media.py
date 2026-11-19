"""Media control through MPRIS (the standard Linux media-player D-Bus API):
Spotify, Firefox/Chromium/Brave tabs, VLC, Rhythmbox, … No extra permissions.
`busctl` (systemd) is used with fixed, validated arguments to get JSON."""
from __future__ import annotations

import json
import re

from ..security.policy import ValidationError
from . import runner

PREFIX = "org.mpris.MediaPlayer2."
PATH = "/org/mpris/MediaPlayer2"
PLAYER = "org.mpris.MediaPlayer2.Player"
_NAME_RE = re.compile(r"^org\.mpris\.MediaPlayer2\.[A-Za-z0-9_.\-]+$")
COMMANDS = {"play": "Play", "pause": "Pause", "toggle": "PlayPause", "stop": "Stop",
            "next": "Next", "previous": "Previous"}


def _busctl(*args: str, timeout: float = 4) -> runner.RunResult:
    return runner.run(["busctl", "--user", *args], timeout=timeout)


def _prop(name: str, iface: str, prop: str):
    if not _NAME_RE.match(name):
        raise ValidationError("bad player name")
    r = _busctl("get-property", name, PATH, iface, prop, "--json=short")
    if not r.ok:
        return None
    try:
        return json.loads(r.out).get("data")
    except json.JSONDecodeError:
        return None


def players() -> list[dict]:
    r = _busctl("list", "--json=short", "--acquired")
    if not r.ok:
        return []
    try:
        names = [e["name"] for e in json.loads(r.out) if str(e.get("name", "")).startswith(PREFIX)]
    except (json.JSONDecodeError, TypeError):
        return []
    out = []
    for n in names:
        status = _prop(n, PLAYER, "PlaybackStatus") or "Stopped"
        ident = _prop(n, "org.mpris.MediaPlayer2", "Identity") or n[len(PREFIX):].split(".")[0].title()
        meta = _prop(n, PLAYER, "Metadata") or {}
        title = (meta.get("xesam:title") or {}).get("data", "")
        artists = (meta.get("xesam:artist") or {}).get("data", []) or []
        out.append({"name": n, "app": ident, "status": status, "title": title,
                    "artist": ", ".join(artists) if isinstance(artists, list) else str(artists)})
    return out


def pick(ps: list[dict]) -> dict | None:
    """The player the user most likely means: playing > paused > anything."""
    for st in ("Playing", "Paused"):
        for p in ps:
            if p["status"] == st:
                return p
    return ps[0] if ps else None


def describe(p: dict) -> str:
    what = p["title"] or "something"
    if p["artist"]:
        what = f"{what} by {p['artist']}"
    state = {"Playing": "playing", "Paused": "paused on"}.get(p["status"], "stopped on")
    return f"{p['app']} is {state} {what}." if p["title"] else f"{p['app']} is {p['status'].lower()}."


def control(command: str) -> str:
    cmd = COMMANDS.get(command.lower().strip())
    if not cmd:
        raise ValidationError(f"unknown media command '{command}' (use {', '.join(COMMANDS)})")
    ps = players()
    p = pick(ps)
    if p is None:
        return "No media player is running."
    if cmd == "PlayPause" and p["status"] == "Playing":
        cmd = "Pause"
    r = _busctl("call", p["name"], PATH, PLAYER, cmd)
    if not r.ok:
        raise RuntimeError(r.err.strip() or f"{p['app']} didn't accept {cmd}")
    verb = {"Play": "Playing", "Pause": "Paused", "PlayPause": "Toggled", "Stop": "Stopped",
            "Next": "Skipped to the next track in", "Previous": "Back to the previous track in"}[cmd]
    return f"{verb} {p['app']}."


def now_playing() -> str:
    ps = players()
    if not ps:
        return "Nothing is playing — no media players are open."
    p = pick(ps)
    return describe(p)
