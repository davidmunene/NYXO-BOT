"""SQLite persistence for settings, chat history, memories, tasks, reminders,
app usage, user-approved custom commands and the action audit log.

The connection is opened with check_same_thread=False and guarded by a lock so
background workers can log actions safely.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from ..security.redact import redact

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
    content TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'fact',
    created REAL NOT NULL,
    updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    created REAL NOT NULL,
    completed REAL
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    due REAL NOT NULL,
    fired INTEGER NOT NULL DEFAULT 0,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS app_usage (
    app_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    last REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS custom_commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    argv TEXT NOT NULL,
    confirm INTEGER NOT NULL DEFAULT 1,
    approved REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS action_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    action TEXT NOT NULL,
    args TEXT NOT NULL,
    approved INTEGER NOT NULL,
    ok INTEGER NOT NULL,
    result TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path) if str(path) != ":memory:" else path
        if isinstance(self.path, Path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self.conn.executescript(_SCHEMA)
            self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self.conn.commit()
        if isinstance(self.path, Path):
            try:
                self.path.chmod(0o600)
            except OSError:
                pass

    # -- low level --------------------------------------------------------
    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    # -- settings ---------------------------------------------------------
    def get_setting(self, key: str, default: Any = None) -> Any:
        rows = self.query("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set_setting(self, key: str, value: Any) -> None:
        self.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, json.dumps(value)))

    def all_settings(self) -> dict[str, Any]:
        return {r["key"]: json.loads(r["value"]) for r in self.query("SELECT key,value FROM settings")}

    # -- chat history -----------------------------------------------------
    def add_message(self, role: str, content: str) -> int:
        cur = self.execute("INSERT INTO messages(role,content,ts) VALUES(?,?,?)",
                           (role, redact(content), time.time()))
        return int(cur.lastrowid)

    def recent_messages(self, limit: int = 50) -> list[dict]:
        rows = self.query("SELECT * FROM (SELECT * FROM messages ORDER BY id DESC LIMIT ?) "
                          "ORDER BY id ASC", (limit,))
        return [dict(r) for r in rows]

    def clear_messages(self) -> None:
        self.execute("DELETE FROM messages")

    # -- long-term memories ----------------------------------------------
    def add_memory(self, content: str, category: str = "fact") -> int:
        now = time.time()
        cur = self.execute("INSERT INTO memories(content,category,created,updated) VALUES(?,?,?,?)",
                           (redact(content), category, now, now))
        return int(cur.lastrowid)

    def list_memories(self) -> list[dict]:
        return [dict(r) for r in self.query("SELECT * FROM memories ORDER BY id")]

    def update_memory(self, mem_id: int, content: str) -> None:
        self.execute("UPDATE memories SET content=?, updated=? WHERE id=?",
                     (redact(content), time.time(), mem_id))

    def delete_memory(self, mem_id: int) -> None:
        self.execute("DELETE FROM memories WHERE id=?", (mem_id,))

    def clear_memories(self) -> None:
        self.execute("DELETE FROM memories")

    # -- tasks ------------------------------------------------------------
    def add_task(self, title: str) -> int:
        cur = self.execute("INSERT INTO tasks(title,created) VALUES(?,?)", (title.strip(), time.time()))
        return int(cur.lastrowid)

    def list_tasks(self, include_done: bool = False) -> list[dict]:
        sql = "SELECT * FROM tasks" + ("" if include_done else " WHERE done=0") + " ORDER BY id"
        return [dict(r) for r in self.query(sql)]

    def complete_task(self, task_id: int) -> bool:
        cur = self.execute("UPDATE tasks SET done=1, completed=? WHERE id=? AND done=0",
                           (time.time(), task_id))
        return cur.rowcount > 0

    def delete_task(self, task_id: int) -> bool:
        return self.execute("DELETE FROM tasks WHERE id=?", (task_id,)).rowcount > 0

    # -- reminders --------------------------------------------------------
    def add_reminder(self, text: str, due: float) -> int:
        cur = self.execute("INSERT INTO reminders(text,due,created) VALUES(?,?,?)",
                           (text.strip(), due, time.time()))
        return int(cur.lastrowid)

    def pending_reminders(self) -> list[dict]:
        return [dict(r) for r in self.query("SELECT * FROM reminders WHERE fired=0 ORDER BY due")]

    def due_reminders(self, now: float | None = None) -> list[dict]:
        now = now or time.time()
        return [dict(r) for r in self.query(
            "SELECT * FROM reminders WHERE fired=0 AND due<=? ORDER BY due", (now,))]

    def mark_reminder_fired(self, rid: int) -> None:
        self.execute("UPDATE reminders SET fired=1 WHERE id=?", (rid,))

    def delete_reminder(self, rid: int) -> bool:
        return self.execute("DELETE FROM reminders WHERE id=?", (rid,)).rowcount > 0

    # -- app usage --------------------------------------------------------
    def record_app_use(self, app_id: str, name: str) -> None:
        self.execute("INSERT INTO app_usage(app_id,name,count,last) VALUES(?,?,1,?) "
                     "ON CONFLICT(app_id) DO UPDATE SET count=count+1, last=excluded.last",
                     (app_id, name, time.time()))

    def top_apps(self, n: int = 5) -> list[dict]:
        return [dict(r) for r in self.query(
            "SELECT * FROM app_usage ORDER BY count DESC, last DESC LIMIT ?", (n,))]

    # -- custom commands --------------------------------------------------
    def add_custom_command(self, name: str, argv: list[str], confirm: bool = True) -> int:
        cur = self.execute(
            "INSERT INTO custom_commands(name,argv,confirm,approved) VALUES(?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET argv=excluded.argv, confirm=excluded.confirm, "
            "approved=excluded.approved",
            (name.strip().lower(), json.dumps(argv), int(confirm), time.time()))
        return int(cur.lastrowid)

    def list_custom_commands(self) -> list[dict]:
        out = []
        for r in self.query("SELECT * FROM custom_commands ORDER BY name"):
            d = dict(r)
            d["argv"] = json.loads(d["argv"])
            d["confirm"] = bool(d["confirm"])
            out.append(d)
        return out

    def get_custom_command(self, name: str) -> dict | None:
        for c in self.list_custom_commands():
            if c["name"] == name.strip().lower():
                return c
        return None

    def delete_custom_command(self, name: str) -> bool:
        return self.execute("DELETE FROM custom_commands WHERE name=?",
                            (name.strip().lower(),)).rowcount > 0

    # -- audit log --------------------------------------------------------
    def log_action(self, action: str, args: dict, approved: bool, ok: bool, result: str) -> None:
        self.execute("INSERT INTO action_log(ts,action,args,approved,ok,result) VALUES(?,?,?,?,?,?)",
                     (time.time(), action, redact(json.dumps(args)), int(approved), int(ok),
                      redact(result[:2000])))

    def recent_actions(self, n: int = 50) -> list[dict]:
        return [dict(r) for r in self.query("SELECT * FROM action_log ORDER BY id DESC LIMIT ?", (n,))]

    # -- export / backup --------------------------------------------------
    def export_json(self, path: Path) -> Path:
        data = {
            "exported": time.time(),
            "settings": self.all_settings(),
            "messages": self.recent_messages(100000),
            "memories": self.list_memories(),
            "tasks": self.list_tasks(include_done=True),
            "reminders": [dict(r) for r in self.query("SELECT * FROM reminders")],
            "app_usage": self.top_apps(1000),
            "custom_commands": self.list_custom_commands(),
        }
        path.write_text(json.dumps(data, indent=2))
        path.chmod(0o600)
        return path

    def backup(self, dest_dir: Path) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / time.strftime("nyxo-%Y%m%d-%H%M%S.db")
        with self._lock:
            target = sqlite3.connect(str(dest))
            self.conn.backup(target)
            target.close()
        dest.chmod(0o600)
        return dest
