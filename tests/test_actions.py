import subprocess
from pathlib import Path

import pytest

from nyxo.security.policy import NeedsConfirmation
from nyxo.system import apps, files
from nyxo.system.actions import ActionRegistry


@pytest.fixture
def reg(db):
    return ActionRegistry(db, lambda k: None)


def test_unknown_and_bad_args_rejected(reg, db):
    r = reg.execute("rm_rf_everything", {})
    assert not r.ok and "unknown action" in r.message
    r = reg.execute("open_app", {"app": "x", "evil": "1"})
    assert not r.ok and "unexpected" in r.message
    r = reg.execute("complete_task", {"task_id": "abc"})
    assert not r.ok
    assert all(not a["ok"] for a in db.recent_actions())


def test_confirmation_required(reg, tmp_path, monkeypatch):
    with pytest.raises(NeedsConfirmation) as ei:
        reg.execute("delete_task", {"task_id": 1})
    assert "delete task #1" in ei.value.explanation
    with pytest.raises(NeedsConfirmation):
        reg.execute("clear_history")
    with pytest.raises(NeedsConfirmation):
        reg.execute("git_commit", {"message": "x", "path": str(tmp_path)})
    with pytest.raises(NeedsConfirmation):
        reg.execute("trash_file", {"path": "~/whatever.txt"})
    # executable files need confirmation to open
    script = tmp_path / "run.sh"
    script.write_text("echo hi")
    assert reg.needs_confirmation("open_path", {"path": str(script)})
    assert not reg.needs_confirmation("open_path", {"path": str(tmp_path)})


def test_tasks_reminders_memory(reg, db):
    assert reg.execute("add_task", {"title": "write tests"}).ok
    assert "write tests" in reg.execute("list_tasks").message
    tid = db.list_tasks()[0]["id"]
    r = reg.execute("complete_task", {"task_id": tid})
    assert r.ok and r.mood == "celebrating"
    assert reg.execute("add_reminder", {"text": "stretch", "minutes": 5}).ok
    assert "stretch" in reg.execute("list_reminders").message
    assert not reg.execute("add_reminder", {"text": "x", "minutes": -1}).ok
    assert reg.execute("remember", {"fact": "I like Python"}).ok
    assert "I like Python" in reg.execute("list_memories").message
    mid = db.list_memories()[0]["id"]
    assert reg.execute("forget", {"memory_id": mid}, approved=True).ok
    assert db.list_memories() == []


def test_system_info(reg):
    for kind in ("cpu", "ram", "disk", "battery", "uptime", "all"):
        r = reg.execute("system_info", {"kind": kind})
        assert r.ok and len(r.message) > 5, kind
    assert "It's" in reg.execute("datetime").message


def test_file_search_and_info(reg, tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "Quarterly-Report.pdf").write_text("x")
    (tmp_path / "docs" / "notes.py").write_text("x")
    hits = files.search("report", str(tmp_path))
    assert [h.name for h in hits] == ["Quarterly-Report.pdf"]
    assert [h.name for h in files.search("*.py", str(tmp_path))] == ["notes.py"]
    r = reg.execute("file_info", {"path": str(tmp_path / "docs")})
    assert r.ok and "Items: 2" in r.message
    assert not reg.execute("file_info", {"path": "~/.ssh"}).ok


def test_git_actions(reg, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    g = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)  # noqa: E731
    g("init", "-q", "-b", "main")
    g("config", "user.email", "t@example.com")
    g("config", "user.name", "T")
    (repo / "a.txt").write_text("1")
    st = reg.execute("git_status", {"path": str(repo)})
    assert st.ok and "untracked" in st.message
    r = reg.execute("git_commit", {"message": "init", "path": str(repo)}, approved=True)
    assert not r.ok and "Nothing is staged" in r.message
    g("add", "a.txt")
    r = reg.execute("git_commit", {"message": "init", "path": str(repo)}, approved=True)
    assert r.ok, r.message
    assert "init" in reg.execute("git_log", {"path": str(repo)}).message
    assert reg.execute("git_create_branch", {"name": "feature/x", "path": str(repo)}, approved=True).ok
    assert "feature/x" in reg.execute("git_branches", {"path": str(repo)}).message
    bad = reg.execute("git_create_branch", {"name": "--force", "path": str(repo)}, approved=True)
    assert not bad.ok
    assert not reg.execute("git_status", {"path": str(tmp_path)}).ok   # not a repo


def test_trash_file(reg, tmp_path, monkeypatch):
    # trash only inside $HOME: point HOME at tmp for this test
    import nyxo.security.policy as pol
    monkeypatch.setattr(pol, "_HOME", tmp_path)
    f = tmp_path / "proj" / "junk.txt"
    f.parent.mkdir()
    f.write_text("bye")
    called = {}
    monkeypatch.setattr(files, "trash", lambda p: called.setdefault("p", p) and "moved")
    r = reg.execute("trash_file", {"path": str(f)}, approved=True)
    assert r.ok and called["p"] == f
    r = reg.execute("trash_file", {"path": "/etc/hostname"}, approved=True)
    assert not r.ok


def test_custom_command(reg, db):
    db.add_custom_command("hello", ["echo", "hi there"], confirm=True)
    with pytest.raises(NeedsConfirmation):
        reg.execute("run_custom", {"name": "hello"})
    r = reg.execute("run_custom", {"name": "hello"}, approved=True)
    assert r.ok and "hi there" in r.message
    db.add_custom_command("quick", ["echo", "fast"], confirm=False)
    assert reg.execute("run_custom", {"name": "quick"}).ok


def test_app_discovery():
    found = apps.list_apps(refresh=True)
    assert len(found) > 5
    for name in ("firefox", "terminal", "files", "settings"):
        assert apps.find_app(name) is not None, name
    assert apps.find_app("zzzz-not-an-app") is None


def test_audit_log(reg, db):
    reg.execute("datetime")
    log = db.recent_actions(1)[0]
    assert log["action"] == "datetime" and log["ok"] == 1
    _ = Path
