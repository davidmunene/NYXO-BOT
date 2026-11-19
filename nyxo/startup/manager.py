"""Login autostart via a systemd *user* service bound to GNOME's
graphical-session.target: it starts only once the desktop session is up,
inherits WAYLAND_DISPLAY/DISPLAY from the session, runs as the user (never
root), and is restarted automatically if it crashes (but not after Quit)."""
from __future__ import annotations

from pathlib import Path

from ..core import paths
from ..system import runner
from ..core import brand

UNIT = "nyxo.service"
INSTALL_HOME = Path.home() / ".local" / "opt" / "nyxo"
LAUNCHER = Path.home() / ".local" / "bin" / "nyxo"


def unit_path() -> Path:
    return paths.systemd_user_dir() / UNIT


def is_installed() -> bool:
    return LAUNCHER.exists() and (INSTALL_HOME / "app" / "nyxo").is_dir()


def running_from_install() -> bool:
    here = Path(__file__).resolve()
    return INSTALL_HOME in here.parents


def unit_text(launcher: Path = LAUNCHER) -> str:
    return f"""[Unit]
Description=NYXO desktop companion
Documentation=file://{INSTALL_HOME}/app/README.md
PartOf=graphical-session.target
After=graphical-session.target
Requisite=graphical-session.target
StartLimitIntervalSec=120
StartLimitBurst=5

[Service]
Type=simple
ExecStart={launcher} --service
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1
# NYXO is an unprivileged desktop app
NoNewPrivileges=yes

[Install]
WantedBy=graphical-session.target
"""


def _ctl(*args: str, timeout: float = 15) -> runner.RunResult:
    return runner.run(["systemctl", "--user", *args], timeout=timeout)


def write_unit() -> Path:
    p = unit_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(unit_text())
    _ctl("daemon-reload")
    return p


def is_enabled() -> bool:
    return _ctl("is-enabled", UNIT, timeout=5).out.strip() == "enabled"


def is_active() -> bool:
    return _ctl("is-active", UNIT, timeout=5).out.strip() == "active"


def enable() -> tuple[bool, str]:
    if not is_installed():
        return False, f"{brand.NAME} isn't installed yet. Run scripts/install.sh from the project folder."
    if not unit_path().exists():
        write_unit()
    r = _ctl("enable", UNIT)
    return r.ok, (r.err or r.out).strip() or "Autostart enabled."


def disable() -> tuple[bool, str]:
    r = _ctl("disable", UNIT)
    return r.ok, (r.err or r.out).strip() or "Autostart disabled."


def start() -> bool:
    if _ctl("start", "--no-block", UNIT).ok:
        return True
    # e.g. start-limit-hit after repeated crashes: clear and retry once
    _ctl("reset-failed", UNIT)
    return _ctl("start", "--no-block", UNIT).ok


def stop_pending() -> bool:
    """Stop the unit if systemd is about to (re)start it. Returns True if it did."""
    state = _ctl("is-active", UNIT, timeout=5).out.strip()
    if state in ("activating", "active", "reloading"):
        return _ctl("stop", UNIT).ok
    return False


def restart() -> bool:
    return _ctl("restart", "--no-block", UNIT).ok


def status() -> dict:
    return {
        "installed": is_installed(),
        "unit": unit_path().exists(),
        "enabled": is_enabled() if unit_path().exists() else False,
        "active": is_active() if unit_path().exists() else False,
        "launcher": str(LAUNCHER),
        "install_home": str(INSTALL_HOME),
    }
