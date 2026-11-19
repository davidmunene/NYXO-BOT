"""The only place NYXO spawns processes. argv lists only, never shell=True."""
from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass

from ..security import policy

log = logging.getLogger("nyxo.runner")


@dataclass
class RunResult:
    code: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.code == 0


def _env() -> dict:
    env = dict(os.environ)
    env.pop("QT_QPA_PLATFORM", None)     # children should use their native backend
    env.setdefault("LC_ALL", "C.UTF-8")
    return env


def run(argv: list[str], timeout: float = 15, cwd: str | None = None,
        custom: bool = False) -> RunResult:
    """Run to completion. `custom=True` is for user-approved custom commands,
    which are validated by policy.custom_argv instead of the allowlist."""
    if custom:
        argv = policy.custom_argv(argv)
    else:
        policy.check_binary(argv[0])
    log.debug("run: %s", argv)
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           cwd=cwd, env=_env(), shell=False, stdin=subprocess.DEVNULL)
        return RunResult(p.returncode, p.stdout, p.stderr)
    except subprocess.TimeoutExpired:
        return RunResult(124, "", f"timed out after {timeout:.0f}s")
    except OSError as e:
        return RunResult(127, "", str(e))


def spawn(argv: list[str]) -> subprocess.Popen:
    """Launch a detached GUI program (allowlisted launcher binaries only)."""
    policy.check_binary(argv[0])
    log.info("spawn: %s", argv)
    return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True,
                            env=_env(), shell=False)
