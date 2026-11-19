"""Controlled action registry.

Every capability NYXO has on the computer is an Action with a fixed name,
typed/validated parameters and a risk level. Nothing else is executable: the
AI can only *propose* registry actions by name, and CONFIRM-level actions
always require an explicit user approval. Every execution is audit-logged.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ..memory.db import Database
from ..security import policy
from ..security.policy import NeedsConfirmation, Risk, ValidationError
from . import apps, cleanup, files, gitops, media, sysinfo
from ..core import brand

log = logging.getLogger("nyxo.actions")


@dataclass
class Param:
    name: str
    kind: type = str
    required: bool = True
    help: str = ""


@dataclass
class ActionResult:
    ok: bool
    message: str
    mood: str = "happy"          # character reaction: happy|celebrating|error|confused|working
    data: Any = None


@dataclass
class Action:
    name: str
    description: str
    handler: Callable[..., ActionResult]
    params: list[Param] = field(default_factory=list)
    risk: Risk = Risk.SAFE
    explain: Callable[[dict], str] | None = None
    risk_fn: Callable[[dict], Risk] | None = None    # per-call risk override

    def risk_for(self, args: dict) -> Risk:
        return self.risk_fn(args) if self.risk_fn else self.risk


class ActionRegistry:
    def __init__(self, db: Database, settings_get: Callable[[str], Any] | None = None):
        self.db = db
        self._get = settings_get or (lambda k: None)
        self.actions: dict[str, Action] = {}
        self._register_builtin()

    # -- registry API -----------------------------------------------------
    def register(self, action: Action) -> None:
        if action.name in self.actions:
            raise ValueError(f"duplicate action {action.name}")
        self.actions[action.name] = action

    def validate(self, name: str, args: dict | None) -> tuple[Action, dict]:
        action = self.actions.get(name)
        if action is None:
            raise ValidationError(f"unknown action '{name}'")
        args = dict(args or {})
        clean: dict[str, Any] = {}
        for prm in action.params:
            if prm.name not in args or args[prm.name] in (None, ""):
                if prm.required:
                    raise ValidationError(f"{name}: missing '{prm.name}'")
                continue
            val = args.pop(prm.name)
            try:
                clean[prm.name] = prm.kind(val)
            except (TypeError, ValueError):
                raise ValidationError(f"{name}: '{prm.name}' must be {prm.kind.__name__}") from None
            if isinstance(clean[prm.name], str) and len(clean[prm.name]) > 4000:
                raise ValidationError(f"{name}: '{prm.name}' is too long")
        if args:
            raise ValidationError(f"{name}: unexpected arguments {sorted(args)}")
        if action.risk_for(clean) >= Risk.BLOCKED:
            raise ValidationError(f"{name} is blocked")
        return action, clean

    def needs_confirmation(self, name: str, args: dict) -> bool:
        action, clean = self.validate(name, args)
        return action.risk_for(clean) >= Risk.CONFIRM

    def explain(self, name: str, args: dict) -> str:
        action, clean = self.validate(name, args)
        if action.explain:
            return action.explain(clean)
        return f"{action.description} ({', '.join(f'{k}={v!r}' for k, v in clean.items())})"

    def execute(self, name: str, args: dict | None = None, approved: bool = False) -> ActionResult:
        try:
            action, clean = self.validate(name, args)
        except ValidationError as e:
            self.db.log_action(name, dict(args or {}), approved, False, f"rejected: {e}")
            return ActionResult(False, str(e), mood="confused")
        if action.risk_for(clean) >= Risk.CONFIRM and not approved:
            raise NeedsConfirmation(name, clean, self.explain(name, clean))
        try:
            res = action.handler(**clean)
        except ValidationError as e:
            res = ActionResult(False, str(e), mood="confused")
        except Exception as e:  # noqa: BLE001 - surface any failure to the user
            log.exception("action %s failed", name)
            res = ActionResult(False, f"That didn't work: {e}", mood="error")
        self.db.log_action(name, clean, approved, res.ok, res.message)
        return res

    def describe_for_ai(self) -> str:
        """Compact list (keeps the prompt short so local models answer fast)."""
        out = []
        for a in self.actions.values():
            ps = ",".join(p.name + ("" if p.required else "?") for p in a.params)
            out.append(f"{a.name}({ps})" + ("*" if a.risk >= Risk.CONFIRM else ""))
        return ", ".join(out)

    # -- built-in actions -------------------------------------------------
    def _repo(self, path: str | None) -> str:
        p = path or self._get("dev_repo") or ""
        if not p:
            raise ValidationError("Which repository? Say e.g. 'git status in ~/code/myproject' "
                                  "or set a default repo in Settings → General.")
        return p

    def _register_builtin(self) -> None:
        R, P = self.register, Param
        db = self.db

        # applications
        def open_app(app: str) -> ActionResult:
            entry = apps.find_app(app)
            if not entry:
                return ActionResult(False, f"I couldn't find an app called '{app}'.", "confused")
            apps.launch(entry)
            db.record_app_use(entry.app_id, entry.name)
            return ActionResult(True, f"Opening {entry.name}.", "happy", entry.app_id)
        R(Action("open_app", "Launch an installed application by name", open_app, [P("app")]))

        def list_apps(query: str = "") -> ActionResult:
            q = query.lower()
            names = sorted({a.name for a in apps.list_apps().values() if q in a.name.lower()})
            top = [a["name"] for a in db.top_apps(5)]
            msg = f"{len(names)} apps" + (f" matching '{query}'" if q else "") + ": " + ", ".join(names[:40])
            if top and not q:
                msg += f"\nYour most used: {', '.join(top)}"
            return ActionResult(True, msg, "working", names)
        R(Action("list_apps", "List installed applications", list_apps, [P("query", required=False)]))

        # files
        def search_files(query: str, where: str = "") -> ActionResult:
            root = str(files.xdg_dir(where) or where) if where else None
            hits = files.search(query, root)
            if not hits:
                return ActionResult(False, f"No files matching '{query}' found.", "confused", [])
            home = str(Path.home())
            lst = "\n".join(f"  {str(h).replace(home, '~', 1)}" for h in hits)
            more = " (showing first 25)" if len(hits) >= 25 else ""
            return ActionResult(True, f"Found {len(hits)} match(es){more}:\n{lst}", "happy",
                                [str(h) for h in hits])
        R(Action("search_files", "Search your home folder for files/folders by name or glob",
                 search_files, [P("query"), P("where", required=False)]))

        def file_info(path: str) -> ActionResult:
            return ActionResult(True, files.info(path), "working")
        R(Action("file_info", "Show size, dates and permissions of a file or folder", file_info, [P("path")]))

        def _open_target(path: str) -> Path:
            d = files.xdg_dir(path)
            return d if d and d.exists() else policy.readable_path(path)

        def open_path(path: str) -> ActionResult:
            p = _open_target(path)
            files.open_path(p)
            return ActionResult(True, f"Opening {p}.", "happy")

        def open_path_risk(args: dict) -> Risk:
            try:
                return Risk.CONFIRM if files.is_risky_to_open(_open_target(args["path"])) else Risk.SAFE
            except ValidationError:
                return Risk.SAFE   # handler will report the error
        R(Action("open_path", "Open a file or folder (e.g. 'downloads') with its default app",
                 open_path, [P("path")], risk_fn=open_path_risk,
                 explain=lambda a: f"Open {a['path']}. It looks executable/installable, so it "
                                   f"could run code."))

        def storage(path: str = "") -> ActionResult:
            return ActionResult(True, sysinfo.disk() + "\n\n" + files.storage_breakdown(path or None), "working")
        R(Action("storage_usage", "Explain what's using disk space", storage, [P("path", required=False)]))

        def trash_file(path: str) -> ActionResult:
            p = policy.user_writable_path(path)
            return ActionResult(True, files.trash(p), "happy")
        R(Action("trash_file", "Move a file in your home folder to the Trash (recoverable)",
                 trash_file, [P("path")], risk=Risk.CONFIRM,
                 explain=lambda a: f"Move '{a['path']}' to the Trash. It can be restored from Files → Trash."))

        def open_url(url: str) -> ActionResult:
            files.open_url(policy.url(url))
            return ActionResult(True, f"Opening {url} in your browser.", "happy")
        R(Action("open_url", "Open a web link in the default browser", open_url, [P("url")]))

        # system info
        def system_info(kind: str = "all") -> ActionResult:
            k = kind.lower().strip()
            fn = sysinfo.KINDS.get(k)
            msg = fn() if fn else sysinfo.summary()
            return ActionResult(True, msg, "working")
        R(Action("system_info", "CPU, RAM, disk, battery, network or uptime (kind: cpu|ram|disk|"
                 "battery|network|uptime|all)", system_info, [P("kind", required=False)]))

        def now() -> ActionResult:
            n = datetime.now()
            return ActionResult(True, n.strftime("It's %H:%M on %A, %d %B %Y."), "happy")
        R(Action("datetime", "Current date and time", now))

        # media (MPRIS)
        R(Action("media_control", "Control the active media player (play|pause|toggle|stop|next|previous)",
                 lambda command: ActionResult(True, media.control(command), "happy"), [P("command")]))
        R(Action("now_playing", "Say what's currently playing",
                 lambda: ActionResult(True, media.now_playing(), "happy")))

        # disk cleanup
        R(Action("cleanup_scan", "Find reclaimable disk space (caches, Trash)",
                 lambda: ActionResult(True, cleanup.report(), "working")))

        def cleanup_explain(a: dict) -> str:
            t = cleanup.TARGETS.get(a["target"].lower())
            if not t:
                return f"Clean '{a['target']}'"
            size = files.human(cleanup.dir_size(t.path)) if t.path.exists() else "0 B"
            return f"Clear the {t.label} ({size}) at {t.path}. Note: {t.note}."
        R(Action("cleanup", "Clear one reclaimable location (see cleanup_scan)",
                 lambda target: ActionResult(True, cleanup.clean(target), "celebrating"),
                 [P("target")], risk=Risk.CONFIRM, explain=cleanup_explain))

        # git
        R(Action("git_status", "Show git status of a repository", lambda path="": ActionResult(
            True, gitops.status(self._repo(path)), "working"), [P("path", required=False)]))
        R(Action("git_log", "Show recent commits", lambda path="": ActionResult(
            True, gitops.log(self._repo(path)), "working"), [P("path", required=False)]))
        R(Action("git_branches", "List branches", lambda path="": ActionResult(
            True, gitops.branches(self._repo(path)), "working"), [P("path", required=False)]))
        R(Action("git_diff", "Summarise uncommitted changes", lambda path="": ActionResult(
            True, gitops.diff_stat(self._repo(path)), "working"), [P("path", required=False)]))
        R(Action("git_commit", "Commit the already-staged changes with a message",
                 lambda message, path="": ActionResult(True, gitops.commit(self._repo(path), message),
                                                       "celebrating"),
                 [P("message"), P("path", required=False)], risk=Risk.CONFIRM,
                 explain=lambda a: f"Run `git commit -m \"{a['message']}\"` in "
                                   f"{a.get('path') or self._get('dev_repo')}. Only staged files are committed."))
        R(Action("git_create_branch", "Create and switch to a new branch",
                 lambda name, path="": ActionResult(True, gitops.create_branch(self._repo(path), name), "happy"),
                 [P("name"), P("path", required=False)], risk=Risk.CONFIRM,
                 explain=lambda a: f"Run `git switch -c {a['name']}` in {a.get('path') or self._get('dev_repo')}."))
        R(Action("git_switch_branch", "Switch to an existing branch",
                 lambda name, path="": ActionResult(True, gitops.switch_branch(self._repo(path), name), "happy"),
                 [P("name"), P("path", required=False)], risk=Risk.CONFIRM,
                 explain=lambda a: f"Run `git switch {a['name']}` in {a.get('path') or self._get('dev_repo')}."))

        # tasks
        def add_task(title: str) -> ActionResult:
            tid = db.add_task(title)
            return ActionResult(True, f"Added task #{tid}: {title}", "happy", tid)
        R(Action("add_task", "Add an item to the to-do list", add_task, [P("title")]))

        def list_tasks() -> ActionResult:
            ts = db.list_tasks()
            if not ts:
                return ActionResult(True, "Your task list is empty. Suspiciously productive.", "happy", [])
            return ActionResult(True, "Your tasks:\n" + "\n".join(f"  #{t['id']} {t['title']}" for t in ts),
                                "working", ts)
        R(Action("list_tasks", "Show open to-do items", list_tasks))

        def complete_task(task_id: int) -> ActionResult:
            if db.complete_task(task_id):
                return ActionResult(True, f"Task #{task_id} done! 🎉", "celebrating")
            return ActionResult(False, f"No open task #{task_id}.", "confused")
        R(Action("complete_task", "Mark a to-do item as done", complete_task, [P("task_id", int)]))

        def delete_task(task_id: int) -> ActionResult:
            ok = db.delete_task(task_id)
            return ActionResult(ok, f"Deleted task #{task_id}." if ok else f"No task #{task_id}.",
                                "happy" if ok else "confused")
        R(Action("delete_task", "Delete a to-do item", delete_task, [P("task_id", int)], risk=Risk.CONFIRM,
                 explain=lambda a: f"Permanently delete task #{a['task_id']}."))

        # reminders
        def add_reminder(text: str, minutes: float) -> ActionResult:
            if minutes <= 0 or minutes > 60 * 24 * 365:
                raise ValidationError("reminder time must be between now and a year from now")
            due = time.time() + minutes * 60
            rid = db.add_reminder(text, due)
            when = datetime.fromtimestamp(due).strftime("%H:%M" if minutes < 1440 else "%a %d %b %H:%M")
            return ActionResult(True, f"Okay! I'll remind you to {text} at {when}.", "happy", rid)
        R(Action("add_reminder", "Set a reminder N minutes from now", add_reminder,
                 [P("text"), P("minutes", float)]))

        def list_reminders() -> ActionResult:
            rs = db.pending_reminders()
            if not rs:
                return ActionResult(True, "No pending reminders.", "happy", [])
            lines = [f"  #{r['id']} {datetime.fromtimestamp(r['due']).strftime('%a %H:%M')} — {r['text']}"
                     for r in rs]
            return ActionResult(True, "Upcoming reminders:\n" + "\n".join(lines), "working", rs)
        R(Action("list_reminders", "Show pending reminders", list_reminders))

        def cancel_reminder(reminder_id: int) -> ActionResult:
            ok = db.delete_reminder(reminder_id)
            return ActionResult(ok, f"Cancelled reminder #{reminder_id}." if ok else "No such reminder.",
                                "happy" if ok else "confused")
        R(Action("cancel_reminder", "Cancel a reminder", cancel_reminder, [P("reminder_id", int)]))

        # memory
        def remember(fact: str) -> ActionResult:
            mid = db.add_memory(fact)
            return ActionResult(True, f"Got it, I'll remember that (memory #{mid}).", "happy", mid)
        R(Action("remember", "Store a fact or preference about the user", remember, [P("fact")]))

        def list_memories() -> ActionResult:
            ms = db.list_memories()
            if not ms:
                return ActionResult(True, "I don't have any saved memories yet.", "happy", [])
            return ActionResult(True, "Here's what I remember:\n" +
                                "\n".join(f"  #{m['id']} {m['content']}" for m in ms), "thinking", ms)
        R(Action("list_memories", f"List what {brand.NAME} remembers", list_memories))

        def forget(memory_id: int) -> ActionResult:
            db.delete_memory(memory_id)
            return ActionResult(True, f"Forgotten memory #{memory_id}.", "happy")
        R(Action("forget", "Delete a saved memory", forget, [P("memory_id", int)], risk=Risk.CONFIRM,
                 explain=lambda a: f"Permanently delete memory #{a['memory_id']}."))

        def clear_history() -> ActionResult:
            db.clear_messages()
            return ActionResult(True, "Conversation history cleared.", "happy")
        R(Action("clear_history", "Erase the chat history", clear_history, risk=Risk.CONFIRM,
                 explain=lambda a: "Erase the entire saved conversation history."))

        # user-approved custom commands
        def run_custom(name: str) -> ActionResult:
            from . import runner
            cmd = db.get_custom_command(name)
            if not cmd:
                return ActionResult(False, f"No custom command named '{name}'.", "confused")
            r = runner.run(cmd["argv"], timeout=60, custom=True)
            out = (r.out + r.err).strip()[-1500:] or "(no output)"
            return ActionResult(r.ok, f"`{' '.join(cmd['argv'])}` exited {r.code}:\n{out}",
                                "celebrating" if r.ok else "error")

        def custom_risk(args: dict) -> Risk:
            cmd = db.get_custom_command(args.get("name", ""))
            return Risk.CONFIRM if (cmd is None or cmd["confirm"]) else Risk.SAFE

        def custom_explain(args: dict) -> str:
            cmd = db.get_custom_command(args["name"])
            return f"Run your custom command '{args['name']}': {' '.join(cmd['argv']) if cmd else '?'}"
        R(Action("run_custom", "Run one of the user's pre-approved custom commands", run_custom,
                 [P("name")], risk_fn=custom_risk, explain=custom_explain))
