"""Verified answers for common developer / Linux how-to questions.

Small local models often get exact commands subtly (and sometimes
dangerously) wrong, so the most common questions are answered from this
hand-checked list instead — instantly and correctly. Matching is by required
keywords; the AI handles everything else."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Recipe:
    keywords: tuple[tuple[str, ...], ...]     # every group must match (any word in a group)
    answer: str


R = Recipe
RECIPES: list[Recipe] = [
    R((("undo", "revert", "cancel", "uncommit"), ("commit",), ("keep", "save", "without losing", "preserve")),
      "Run `git reset --soft HEAD~1`. That removes the last commit but keeps all its changes staged, "
      "ready to edit or recommit.\n```bash\ngit reset --soft HEAD~1\n```"),
    R((("undo", "revert", "remove", "delete"), ("last commit", "latest commit", "previous commit"),
       ("discard", "throw away", "completely", "lose", "destroy")),
      "To delete the last commit and its changes completely, run `git reset --hard HEAD~1`. "
      "Careful: the changes are gone for good (unless you recover them via `git reflog`).\n"
      "```bash\ngit reset --hard HEAD~1\n```"),
    R((("undo", "revert"), ("pushed", "already pushed", "public")),
      "For a commit that's already pushed, don't rewrite history. Create an opposite commit instead "
      "with `git revert HEAD`, then push.\n```bash\ngit revert HEAD\ngit push\n```"),
    R((("discard", "throw away", "undo", "reset"), ("changes", "edits", "modifications"), ("file", "files", "all", "local", "uncommitted")),
      "To discard uncommitted changes to a file, run `git restore <file>`; for every file, "
      "`git restore .`. This can't be undone, so stash first if you're unsure (`git stash`).\n"
      "```bash\ngit restore path/to/file\ngit restore .\n```"),
    R((("unstage",), ("file", "files", "changes")),
      "Use `git restore --staged <file>` to unstage a file while keeping your edits.\n"
      "```bash\ngit restore --staged path/to/file\n```"),
    R((("rename",), ("branch",)),
      "Rename the current branch with `git branch -m new-name`. If it was pushed, push the new name and "
      "delete the old one.\n```bash\ngit branch -m new-name\ngit push origin -u new-name\n"
      "git push origin --delete old-name\n```"),
    R((("delete", "remove"), ("branch",)),
      "Delete a local branch with `git branch -d name` (use `-D` to force if it isn't merged). "
      "For the remote one, `git push origin --delete name`.\n```bash\ngit branch -d name\n"
      "git push origin --delete name\n```"),
    R((("change", "edit", "fix", "amend"), ("commit message", "last message")),
      "Run `git commit --amend -m \"new message\"` to change the last commit's message. "
      "Only do this before pushing.\n```bash\ngit commit --amend -m \"new message\"\n```"),
    R((("merge conflict", "conflicts", "conflict"),),
      "Open the conflicted files, choose the right code between the <<<<<<< and >>>>>>> markers, then "
      "`git add` the files and `git commit` (or `git rebase --continue` during a rebase). "
      "`git merge --abort` cancels the merge.\n```bash\ngit status\ngit add resolved_file\ngit commit\n```"),
    R((("create", "make", "set up", "setup", "new"), ("virtual environment", "venv", "virtualenv")),
      "Create and activate a virtual environment in your project folder:\n```bash\n"
      "python3 -m venv .venv\nsource .venv/bin/activate\npip install -r requirements.txt\n```\n"
      "Type `deactivate` to leave it."),
    R((("kill", "stop", "end"), ("process", "program", "app"), ("port",)),
      "Find what's using the port with `ss -ltnp | grep :PORT` (or `lsof -i :PORT`), then stop it "
      "with `kill PID` (use `kill -9 PID` only if it refuses).\n```bash\nss -ltnp | grep :8000\nkill 12345\n```"),
    R((("which", "what", "find", "see"), ("process", "app", "program"), ("cpu", "memory", "ram", "slow")),
      "Run `top` (or `htop` if installed) and press M for memory or P for CPU sorting. In the GUI, open "
      "System Monitor. You can also just ask me “cpu usage” or “ram usage”."),
    R((("permission denied",),),
      "“Permission denied” means your user can't access that file or run it. For a script you own, "
      "`chmod +x script.sh` makes it executable. For system files, think twice before using `sudo`, and "
      "never run `chmod 777`. Tell me the exact command and error and I'll explain."),
    R((("update", "upgrade"), ("ubuntu", "system", "packages", "software")),
      "Update Ubuntu with:\n```bash\nsudo apt update && sudo apt upgrade\n```\nSnap apps update "
      "automatically. Run `snap refresh` to force it."),
    R((("check", "see", "how much", "free"), ("disk", "storage", "space")),
      "Ask me “storage usage” or “free up disk space” and I'll show what's using it. In a terminal: "
      "`df -h` for drives, `du -sh ~/* | sort -h` for your folders."),
]


def _norm(t: str) -> str:
    return " " + re.sub(r"[^a-z0-9 ]+", " ", t.lower()) + " "


def match(question: str) -> str | None:
    q = _norm(question)
    if not re.search(r"\b(how|what|why|can i|should i|help|way to|command)\b", q):
        return None
    for r in RECIPES:
        if all(any(f" {w} " in q or (len(w.split()) > 1 and w in q) for w in group) for group in r.keywords):
            return r.answer
    return None
