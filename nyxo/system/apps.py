"""Installed-application discovery (.desktop files) and launching."""
from __future__ import annotations

import configparser
import difflib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import runner


@dataclass
class AppEntry:
    app_id: str            # desktop id without .desktop
    name: str
    path: Path
    generic: str = ""
    keywords: list[str] = field(default_factory=list)
    exec_: str = ""


ALIASES: dict[str, list[str]] = {
    "terminal": ["org.gnome.Ptyxis", "org.gnome.Console", "org.gnome.Terminal", "kgx"],
    "console": ["org.gnome.Ptyxis", "org.gnome.Console", "org.gnome.Terminal"],
    "files": ["org.gnome.Nautilus", "nautilus"],
    "file manager": ["org.gnome.Nautilus", "nautilus"],
    "nautilus": ["org.gnome.Nautilus"],
    "settings": ["org.gnome.Settings", "gnome-control-center"],
    "system settings": ["org.gnome.Settings", "gnome-control-center"],
    "vs code": ["code_code", "code", "com.visualstudio.code"],
    "vscode": ["code_code", "code", "com.visualstudio.code"],
    "code": ["code_code", "code", "com.visualstudio.code"],
    "visual studio code": ["code_code", "code"],
    "firefox": ["firefox_firefox", "firefox", "org.mozilla.firefox"],
    "browser": ["firefox_firefox", "firefox", "google-chrome", "brave-browser"],
    "web browser": ["firefox_firefox", "firefox", "google-chrome"],
    "chrome": ["google-chrome"],
    "calculator": ["org.gnome.Calculator", "gnome-calculator"],
    "text editor": ["org.gnome.TextEditor", "gnome-text-editor"],
    "editor": ["org.gnome.TextEditor", "gnome-text-editor"],
    "system monitor": ["org.gnome.SystemMonitor", "gnome-system-monitor"],
    "task manager": ["org.gnome.SystemMonitor", "gnome-system-monitor"],
}


def _dirs() -> list[Path]:
    data_dirs = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
    home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    out = [Path(home) / "applications"]
    out += [Path(d) / "applications" for d in data_dirs if d]
    out += [Path("/var/lib/snapd/desktop/applications"),
            Path("/var/lib/flatpak/exports/share/applications"),
            Path.home() / ".local/share/flatpak/exports/share/applications"]
    seen, uniq = set(), []
    for d in out:
        if d not in seen and d.is_dir():
            seen.add(d)
            uniq.append(d)
    return uniq


def _parse(path: Path) -> AppEntry | None:
    cp = configparser.RawConfigParser(strict=False, interpolation=None)
    cp.optionxform = str
    try:
        cp.read(path, encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError, OSError):
        return None
    if "Desktop Entry" not in cp:
        return None
    e = cp["Desktop Entry"]
    if e.get("Type", "Application") != "Application":
        return None
    if e.get("NoDisplay", "false").lower() == "true" or e.get("Hidden", "false").lower() == "true":
        return None
    name = e.get("Name")
    if not name:
        return None
    return AppEntry(app_id=path.stem, name=name, path=path, generic=e.get("GenericName", ""),
                    keywords=[k for k in e.get("Keywords", "").split(";") if k],
                    exec_=e.get("Exec", ""))


_cache: dict[str, AppEntry] | None = None


def list_apps(refresh: bool = False) -> dict[str, AppEntry]:
    global _cache
    if _cache is not None and not refresh:
        return _cache
    apps: dict[str, AppEntry] = {}
    for d in _dirs():
        for f in sorted(d.glob("*.desktop")):
            if f.stem in apps:      # earlier dirs (user) win
                continue
            a = _parse(f)
            if a:
                apps[a.app_id] = a
    _cache = apps
    return apps


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower()).strip()


def find_app(query: str) -> AppEntry | None:
    apps = list_apps()
    q = _norm(query)
    if not q:
        return None
    for cand in ALIASES.get(q, []):
        if cand in apps:
            return apps[cand]
    by_name = {_norm(a.name): a for a in apps.values()}
    if q in by_name:
        return by_name[q]
    for a in apps.values():
        if q == _norm(a.app_id) or q == _norm(a.app_id.split(".")[-1]):
            return a
    starts = [a for n, a in by_name.items() if n.startswith(q)]
    if starts:
        return sorted(starts, key=lambda a: len(a.name))[0]
    contains = [a for n, a in by_name.items() if q in n.split() or q in n]
    if contains:
        return sorted(contains, key=lambda a: len(a.name))[0]
    for a in apps.values():
        if any(q == _norm(k) for k in a.keywords) or q == _norm(a.generic):
            return a
    close = difflib.get_close_matches(q, list(by_name), n=1, cutoff=0.8)
    return by_name[close[0]] if close else None


def launch(app: AppEntry) -> None:
    runner.spawn(["gio", "launch", str(app.path)])
