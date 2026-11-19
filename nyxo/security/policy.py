"""Security policy: risk levels, path rules, argument validators and the
binary allowlist. Nothing in NYXO ever runs a shell string; every external
command is an argv list checked against these rules."""
from __future__ import annotations

import os
import re
import shutil
from enum import IntEnum
from pathlib import Path


class Risk(IntEnum):
    SAFE = 0       # read-only or opening things for the user
    CONFIRM = 1    # changes state: requires explicit approval every time
    BLOCKED = 2


class ValidationError(ValueError):
    pass


class NeedsConfirmation(Exception):
    def __init__(self, action: str, args: dict, explanation: str):
        super().__init__(explanation)
        self.action = action
        self.params = args
        self.explanation = explanation


# Binaries NYXO itself is allowed to invoke (argv[0]).
ALLOWED_BINARIES = frozenset({
    "git", "gio", "gtk-launch", "xdg-open", "notify-send", "du", "df",
    "espeak", "espeak-ng", "spd-say", "piper", "pw-record", "arecord", "pw-play",
    "paplay", "aplay", "ollama", "systemctl", "gsettings", "nmcli", "xdg-user-dir",
    "canberra-gtk-play", "dbus-monitor", "busctl",
})

# Never runnable, not even as an approved custom command.
FORBIDDEN_BINARIES = frozenset({
    "sudo", "su", "pkexec", "doas", "run0", "rm", "dd", "mkfs", "shred", "wipefs",
    "fdisk", "parted", "chmod", "chown", "chattr", "bash", "sh", "zsh", "dash", "fish",
    "python", "python3", "perl", "ruby", "node", "eval", "exec", "env", "xargs",
    "systemd-run", "crontab", "at", "curl", "wget", "nc", "ncat", "socat",
    "reboot", "shutdown", "poweroff", "halt", "init", "kill", "killall", "pkill",
})

_HOME = Path.home()

SENSITIVE_PATHS = tuple(_HOME / p for p in (
    ".ssh", ".gnupg", ".password-store", ".local/share/keyrings", ".pki",
    ".config/nyxo/credentials.env", ".aws", ".kube", ".docker/config.json",
    ".netrc", ".git-credentials", ".mozilla", ".config/google-chrome",
    ".config/BraveSoftware", ".local/share/nyxo/nyxo.db",
))
SYSTEM_PREFIXES = ("/etc", "/boot", "/usr", "/bin", "/sbin", "/lib", "/lib64", "/var",
                   "/proc", "/sys", "/dev", "/root", "/snap", "/opt")


def expand(path: str) -> Path:
    if not isinstance(path, str) or not path.strip():
        raise ValidationError("empty path")
    if "\x00" in path:
        raise ValidationError("invalid path")
    return Path(os.path.expandvars(os.path.expanduser(path.strip()))).resolve()


def is_sensitive(p: Path) -> bool:
    p = p.resolve()
    for s in SENSITIVE_PATHS:
        if p == s or s in p.parents:
            return True
    return False


def readable_path(path: str) -> Path:
    """A path the user may look at / open. Must exist; secrets are off-limits."""
    p = expand(path)
    if not p.exists():
        raise ValidationError(f"{p} does not exist")
    if is_sensitive(p):
        raise ValidationError(f"{p} is a protected location (credentials/keys)")
    if str(p).startswith(("/proc", "/sys", "/dev")):
        raise ValidationError("system pseudo-filesystems are off-limits")
    return p


def user_writable_path(path: str) -> Path:
    """A path NYXO may modify (e.g. move to trash): inside $HOME, not sensitive,
    not the home directory itself, not a top-level XDG folder."""
    p = readable_path(path)
    if p == _HOME or _HOME not in p.parents:
        raise ValidationError("only files inside your home folder can be modified")
    if str(p).startswith(SYSTEM_PREFIXES):
        raise ValidationError("system locations cannot be modified")
    if p.parent == _HOME and p.name.startswith("."):
        raise ValidationError("top-level hidden configuration cannot be modified")
    protected = {_HOME / d for d in ("Desktop", "Documents", "Downloads", "Music", "Pictures",
                                     "Videos", "Public", "Templates", ".config", ".local")}
    if p in protected:
        raise ValidationError(f"{p.name} is a protected top-level folder")
    return p


_BRANCH_RE = re.compile(r"^(?!-)(?!.*\.\.)(?!.*//)[A-Za-z0-9._/\-]{1,100}(?<![./])$")


def branch_name(name: str) -> str:
    name = (name or "").strip()
    if not _BRANCH_RE.match(name) or name.endswith(".lock") or "@{" in name:
        raise ValidationError(f"'{name}' is not a valid branch name")
    return name


def commit_message(msg: str) -> str:
    msg = (msg or "").strip()
    if not msg:
        raise ValidationError("commit message is empty")
    if len(msg) > 2000:
        raise ValidationError("commit message too long")
    return msg


def url(u: str) -> str:
    u = (u or "").strip()
    if not re.match(r"^https?://[^\s\"'<>]+$", u):
        raise ValidationError("only http(s) links can be opened")
    return u


def custom_argv(argv: list[str]) -> list[str]:
    """Validate a user-defined custom command. Runs without a shell."""
    if not argv or not all(isinstance(a, str) and a for a in argv):
        raise ValidationError("command is empty")
    exe = os.path.basename(argv[0])
    if exe in FORBIDDEN_BINARIES:
        raise ValidationError(f"'{exe}' is not allowed in custom commands")
    if any(ch in argv[0] for ch in ";|&$`<>"):
        raise ValidationError("shell syntax is not supported; give a plain program and arguments")
    if shutil.which(argv[0]) is None:
        raise ValidationError(f"program '{argv[0]}' was not found")
    return list(argv)


def check_binary(argv0: str) -> str:
    name = os.path.basename(argv0)
    if name in FORBIDDEN_BINARIES or name not in ALLOWED_BINARIES:
        raise ValidationError(f"'{name}' is not on NYXO's allowlist")
    path = shutil.which(argv0)
    if path is None:
        raise ValidationError(f"'{name}' is not installed")
    return path
