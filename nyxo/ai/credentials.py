"""API key storage.

Lookup order: process environment (e.g. ANTHROPIC_API_KEY), then
~/.config/nyxo/credentials.env (mode 0600, KEY=value lines). Keys are never
written to the database, settings, logs or chat history.
"""
from __future__ import annotations

import os
import re

from ..core import paths
from ..core import brand

ALLOWED = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")
_LINE = re.compile(r"^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$")


def _read_file() -> dict[str, str]:
    p = paths.credentials_path()
    if not p.exists():
        return {}
    out = {}
    for line in p.read_text().splitlines():
        m = _LINE.match(line)
        if m and not line.lstrip().startswith("#"):
            out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def get(name: str) -> str:
    return os.environ.get(name) or os.environ.get(f"NYXO_{name}") or _read_file().get(name, "")


def source(name: str) -> str:
    if os.environ.get(name) or os.environ.get(f"NYXO_{name}"):
        return "environment"
    return "credentials file" if _read_file().get(name) else "not set"


def set_key(name: str, value: str) -> None:
    if name not in ALLOWED:
        raise ValueError(f"unknown credential {name}")
    value = value.strip()
    if any(c in value for c in "\n\r"):
        raise ValueError("invalid key")
    data = _read_file()
    if value:
        data[name] = value
    else:
        data.pop(name, None)
    p = paths.credentials_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(f"# {brand.NAME} credentials — keep private (mode 600)\n")
        for k, v in data.items():
            f.write(f"{k}={v}\n")
    os.chmod(p, 0o600)


def mask(value: str) -> str:
    return "" if not value else (value[:6] + "…" + value[-4:] if len(value) > 12 else "••••")
