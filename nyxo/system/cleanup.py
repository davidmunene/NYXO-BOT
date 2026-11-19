"""Disk-space helper: find reclaimable space in well-known, safe-to-clear
locations and (only after confirmation) clear them.

Only the exact targets below can ever be cleared, and only their *contents*
(the folders themselves stay). Everything here is regenerated automatically by
the owning tool, except the Trash, which is clearly labelled as permanent.
System-level cleanups (old snaps, journal) need sudo, so NYXO only explains
the command."""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..security.policy import ValidationError
from . import runner
from .files import human

H = Path.home()


@dataclass(frozen=True)
class Target:
    key: str
    label: str
    path: Path
    note: str


TARGETS: dict[str, Target] = {t.key: t for t in (
    Target("trash", "Trash", H / ".local/share/Trash", "permanently deletes what's in the Trash"),
    Target("thumbnails", "Thumbnail cache", H / ".cache/thumbnails", "regenerated automatically"),
    Target("pip", "pip download cache", H / ".cache/pip", "re-downloaded when needed"),
    Target("npm", "npm cache", H / ".npm/_cacache", "re-downloaded when needed"),
    Target("yarn", "Yarn cache", H / ".cache/yarn", "re-downloaded when needed"),
    Target("chromium-cache", "Chromium cache", H / ".cache/chromium", "browser cache, refilled as you browse"),
    Target("brave-cache", "Brave cache", H / ".cache/BraveSoftware", "browser cache, refilled as you browse"),
    Target("chrome-cache", "Chrome cache", H / ".cache/google-chrome", "browser cache, refilled as you browse"),
    Target("mozilla-cache", "Firefox cache", H / ".cache/mozilla", "browser cache, refilled as you browse"),
    Target("go-build", "Go build cache", H / ".cache/go-build", "rebuilt when needed"),
)}


def dir_size(p: Path, budget: int = 400_000) -> int:
    total, seen = 0, 0
    for root, dirs, files in os.walk(p, onerror=lambda e: None):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
            seen += 1
            if seen > budget:
                return total
    return total


def scan() -> list[tuple[Target, int]]:
    out = [(t, dir_size(t.path)) for t in TARGETS.values() if t.path.exists()]
    return sorted([o for o in out if o[1] > 1024 * 1024], key=lambda o: -o[1])


def report() -> str:
    found = scan()
    du = shutil.disk_usage("/")
    head = (f"Your system drive has {human(du.free)} free of {human(du.total)} "
            f"({du.used / du.total * 100:.0f}% used).")
    if not found:
        return head + " I found nothing significant to clean in the usual places."
    lines = [f"  • {t.label}: {human(sz)} ({t.note}) — say “clean {t.key}”" for t, sz in found]
    total = sum(sz for _, sz in found)
    extra = ("\nSystem-level (needs your password, run in a terminal):\n"
             "  • old journal logs: sudo journalctl --vacuum-size=200M\n"
             "  • disabled snap revisions: snap list --all | grep disabled")
    return f"{head}\nI can safely free about {human(total)}:\n" + "\n".join(lines) + extra


def clean(key: str) -> str:
    t = TARGETS.get(key.strip().lower())
    if t is None:
        raise ValidationError(f"unknown cleanup target '{key}'. Options: {', '.join(TARGETS)}")
    if not t.path.exists():
        return f"{t.label} is already empty."
    before = dir_size(t.path)
    if t.key == "trash":
        r = runner.run(["gio", "trash", "--empty"], timeout=300)
        if not r.ok:
            raise RuntimeError(r.err.strip() or "could not empty the Trash")
    else:
        real = t.path.resolve()
        if H not in real.parents:                       # never follow a symlink out of $HOME
            raise ValidationError(f"{t.path} resolves outside your home folder; not touching it")
        for child in real.iterdir():
            if child.is_symlink() or child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
    freed = max(0, before - dir_size(t.path))
    return f"Cleared the {t.label}: freed {human(freed)}."
