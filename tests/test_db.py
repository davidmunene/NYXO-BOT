import json
import sqlite3
import time


def test_settings_persist(tmp_path):
    from nyxo.memory.db import Database
    p = tmp_path / "n.db"
    d = Database(p)
    d.set_setting("character_size", 180)
    d.set_setting("position", [10, 20])
    d.close()
    d2 = Database(p)
    assert d2.get_setting("character_size") == 180
    assert d2.get_setting("position") == [10, 20]
    assert (p.stat().st_mode & 0o777) == 0o600


def test_settings_object(settings):
    assert settings["character_size"] == 180
    got = []
    settings.changed.connect(lambda k, v: got.append((k, v)))
    settings.set("character_size", 210)
    settings.set("character_size", 210)          # no duplicate signal
    assert got == [("character_size", 210)]
    assert settings.db.get_setting("character_size") == 210


def test_messages_redacted(db):
    db.add_message("user", "my password is hunter2 and key sk-ant-api03-zzzzzzzzzzzzzzzzzzzz")
    stored = db.recent_messages()[0]["content"]
    assert "hunter2" not in stored and "sk-ant" not in stored


def test_history_and_clear(db):
    for i in range(5):
        db.add_message("user", f"m{i}")
    assert [m["content"] for m in db.recent_messages(3)] == ["m2", "m3", "m4"]
    db.clear_messages()
    assert db.recent_messages() == []


def test_reminders_due(db):
    db.add_reminder("past", time.time() - 5)
    db.add_reminder("future", time.time() + 3600)
    due = db.due_reminders()
    assert [r["text"] for r in due] == ["past"]
    db.mark_reminder_fired(due[0]["id"])
    assert db.due_reminders() == []
    assert len(db.pending_reminders()) == 1


def test_app_usage(db):
    db.record_app_use("firefox", "Firefox")
    db.record_app_use("firefox", "Firefox")
    db.record_app_use("code", "VS Code")
    assert db.top_apps()[0]["app_id"] == "firefox"


def test_export_and_backup(db, tmp_path):
    db.add_memory("likes tea")
    db.add_task("x")
    out = db.export_json(tmp_path / "e.json")
    data = json.loads(out.read_text())
    assert data["memories"][0]["content"] == "likes tea"
    b = db.backup(tmp_path / "bk")
    con = sqlite3.connect(b)
    assert con.execute("select count(*) from memories").fetchone()[0] == 1


def test_memory_edit(db):
    mid = db.add_memory("a")
    db.update_memory(mid, "b")
    assert db.list_memories()[0]["content"] == "b"
    db.delete_memory(mid)
    assert db.list_memories() == []
