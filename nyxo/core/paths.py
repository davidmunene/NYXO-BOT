"""XDG-compliant locations. User data is kept apart from the application code."""
from __future__ import annotations

import os
from pathlib import Path

APP = "nyxo"


def _xdg(var: str, default: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / default)


def config_dir() -> Path:          # settings that the user may hand-edit
    return _xdg("XDG_CONFIG_HOME", ".config") / APP


def data_dir() -> Path:            # database, models, backups
    return _xdg("XDG_DATA_HOME", ".local/share") / APP


def state_dir() -> Path:           # logs
    return _xdg("XDG_STATE_HOME", ".local/state") / APP


def runtime_dir() -> Path:         # lock + IPC socket
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/nyxo-{os.getuid()}"
    return Path(base)


def db_path() -> Path:
    return data_dir() / "nyxo.db"


def log_dir() -> Path:
    return state_dir() / "logs"


def backups_dir() -> Path:
    return data_dir() / "backups"


def models_dir() -> Path:
    return data_dir() / "models"


def credentials_path() -> Path:
    return config_dir() / "credentials.env"


def systemd_user_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "systemd" / "user"


def assets_dir() -> Path:
    """Assets ship next to the package (both in the repo and the install dir)."""
    here = Path(__file__).resolve().parent.parent.parent
    return here / "assets"


def avatar_dir(name: str) -> Path:
    return data_dir() / "avatars" / name


def animations_dir(avatar: str) -> Path:
    """Sprites for the chosen avatar; falls back to the bundled robot."""
    if avatar and avatar != "robot":
        d = avatar_dir(avatar) / "animations"
        if (d / "manifest.json").exists():
            return d
    return assets_dir() / "animations"


def icon_path(avatar: str, size: int = 256) -> Path:
    if avatar and avatar != "robot":
        p = avatar_dir(avatar) / "icons" / f"icon-{size}.png"
        if p.exists():
            return p
    return assets_dir() / "icons" / f"nyxo-{size}.png"


def ensure_dirs() -> None:
    for d in (config_dir(), data_dir(), log_dir(), backups_dir(), models_dir()):
        d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(config_dir(), 0o700)
        os.chmod(data_dir(), 0o700)
    except OSError:
        pass
