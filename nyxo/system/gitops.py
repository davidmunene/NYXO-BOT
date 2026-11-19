"""Git helpers. Read-only commands are SAFE; anything that writes to the
repository is registered as CONFIRM in the action registry."""
from __future__ import annotations

from pathlib import Path

from ..security import policy
from . import runner


def repo_root(path: str) -> Path:
    p = policy.readable_path(path)
    if p.is_file():
        p = p.parent
    r = runner.run(["git", "-C", str(p), "rev-parse", "--show-toplevel"], timeout=5)
    if not r.ok:
        raise policy.ValidationError(f"{p} is not inside a git repository")
    return Path(r.out.strip())


def _git(repo: Path, *args: str, timeout: float = 15) -> str:
    r = runner.run(["git", "-C", str(repo), *args], timeout=timeout)
    out = (r.out + ("\n" + r.err if r.err.strip() else "")).strip()
    if not r.ok:
        raise RuntimeError(out or f"git {' '.join(args)} failed")
    return out


def status(path: str) -> str:
    repo = repo_root(path)
    out = _git(repo, "status", "--short", "--branch")
    lines = out.splitlines()
    head = lines[0] if lines else ""
    changes = lines[1:]
    if not changes:
        return f"{repo.name}: {head.lstrip('# ')} — working tree clean."
    staged = sum(1 for line in changes if line[:1] not in (" ", "?"))
    untracked = sum(1 for line in changes if line.startswith("??"))
    modified = len(changes) - untracked
    shown = "\n".join(changes[:20]) + ("\n…" if len(changes) > 20 else "")
    return (f"{repo.name}: {head.lstrip('# ')}\n{modified} changed ({staged} staged), "
            f"{untracked} untracked:\n{shown}")


def log(path: str, n: int = 10) -> str:
    repo = repo_root(path)
    return _git(repo, "log", f"-{max(1, min(n, 50))}", "--oneline", "--decorate")


def branches(path: str) -> str:
    repo = repo_root(path)
    return _git(repo, "branch", "--list", "-vv")


def diff_stat(path: str) -> str:
    repo = repo_root(path)
    out = _git(repo, "diff", "--stat", "HEAD")
    return out or "No uncommitted changes to tracked files."


def commit(path: str, message: str) -> str:
    repo = repo_root(path)
    msg = policy.commit_message(message)
    staged = _git(repo, "diff", "--cached", "--name-only")
    if not staged:
        raise RuntimeError("Nothing is staged. Stage files with `git add` first — "
                           "I only commit what you've staged.")
    return _git(repo, "commit", "-m", msg)


def create_branch(path: str, name: str) -> str:
    repo = repo_root(path)
    return _git(repo, "switch", "-c", policy.branch_name(name)) or f"Created and switched to {name}"


def switch_branch(path: str, name: str) -> str:
    repo = repo_root(path)
    return _git(repo, "switch", policy.branch_name(name)) or f"Switched to {name}"
