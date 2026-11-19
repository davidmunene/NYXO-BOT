"""File search / info / storage helpers (read-only except trash)."""
from __future__ import annotations

import fnmatch
import os
import stat
import time
from pathlib import Path

from ..security import policy
from . import runner

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".cache", "snap",
             ".npm", ".cargo", ".rustup", ".local", ".mozilla", ".var", "site-packages",
             ".gradle", ".m2", "build", "dist", ".tox", ".mypy_cache", ".pytest_cache"}

XDG_NAMES = {
    "desktop": "DESKTOP", "documents": "DOCUMENTS", "docs": "DOCUMENTS",
    "downloads": "DOWNLOAD", "download": "DOWNLOAD", "music": "MUSIC",
    "pictures": "PICTURES", "photos": "PICTURES", "videos": "VIDEOS",
    "templates": "TEMPLATES", "public": "PUBLICSHARE",
}


def xdg_dir(name: str) -> Path | None:
    key = name.strip().lower().rstrip("/")
    if key in ("home", "~", "home folder"):
        return Path.home()
    xdg = XDG_NAMES.get(key)
    if not xdg:
        return None
    try:
        r = runner.run(["xdg-user-dir", xdg], timeout=3)
        if r.ok and r.out.strip():
            return Path(r.out.strip())
    except policy.ValidationError:
        pass
    return Path.home() / key.capitalize()


def search(query: str, root: str | None = None, limit: int = 25, budget_s: float = 5.0) -> list[Path]:
    q = query.strip()
    if not q:
        raise policy.ValidationError("empty search")
    base = policy.readable_path(root) if root else Path.home()
    pattern = q if any(c in q for c in "*?[") else None
    ql = q.lower()
    results: list[Path] = []
    deadline = time.monotonic() + budget_s
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        if policy.is_sensitive(Path(dirpath)):
            dirnames[:] = []
            continue
        for name in dirnames + filenames:
            hit = fnmatch.fnmatch(name.lower(), pattern.lower()) if pattern else ql in name.lower()
            if hit:
                results.append(Path(dirpath) / name)
                if len(results) >= limit:
                    return results
        if time.monotonic() > deadline:
            break
    return results


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def info(path: str) -> str:
    p = policy.readable_path(path)
    st = p.stat()
    kind = "folder" if p.is_dir() else ("symlink" if p.is_symlink() else "file")
    lines = [f"{p}", f"Type: {kind}", f"Size: {human(st.st_size)}",
             f"Modified: {time.strftime('%Y-%m-%d %H:%M', time.localtime(st.st_mtime))}",
             f"Permissions: {stat.filemode(st.st_mode)}"]
    if p.is_dir():
        try:
            n = sum(1 for _ in p.iterdir())
            lines.append(f"Items: {n}")
        except PermissionError:
            lines.append("Items: (no permission)")
    return "\n".join(lines)


def storage_breakdown(path: str | None = None, top: int = 8) -> str:
    base = policy.readable_path(path) if path else Path.home()
    try:
        r = runner.run(["du", "-x", "-b", "--max-depth=1", str(base)], timeout=25)
    except policy.ValidationError as e:
        return str(e)
    rows = []
    for line in r.out.splitlines():
        try:
            size, p = line.split("\t", 1)
            if Path(p) != base:
                rows.append((int(size), p))
        except ValueError:
            continue
    rows.sort(reverse=True)
    total = sum(s for s, _ in rows)
    out = [f"Largest items in {base} (total {human(total)}):"]
    out += [f"  {human(s):>9}  {Path(p).name}" for s, p in rows[:top]]
    if r.code == 124:
        out.append("(stopped early: the scan took too long)")
    return "\n".join(out)


def is_risky_to_open(p: Path) -> bool:
    if p.is_dir():
        return False
    return (os.access(p, os.X_OK) or p.suffix.lower() in
            {".desktop", ".sh", ".run", ".appimage", ".bin", ".deb", ".py", ".exe", ".jar"})


def open_path(p: Path) -> None:
    runner.spawn(["gio", "open", str(p)])


def trash(p: Path) -> str:
    r = runner.run(["gio", "trash", "--", str(p)], timeout=15)
    if not r.ok:
        raise RuntimeError(r.err.strip() or "could not move to trash")
    return f"Moved {p.name} to the Trash (you can restore it from Files → Trash)."


def open_url(u: str) -> None:
    runner.spawn(["gio", "open", u])


