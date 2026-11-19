from datetime import datetime

import pytest

from nyxo.assistant.intents import parse, parse_clock_minutes, parse_duration_minutes


@pytest.mark.parametrize("text,action,args", [
    ("open firefox", "open_app", {"app": "firefox"}),
    ("Hey NYXO, launch VS Code please", "open_app", {"app": "VS Code"}),
    ("open the terminal", "open_app", {"app": "terminal"}),
    ("open settings", "ui:settings", {}),
    ("open system settings", "open_app", {"app": "system settings"}),
    ("open downloads", "open_path", {"path": "downloads"}),
    ("open ~/Documents/notes.txt", "open_path", {"path": "~/Documents/notes.txt"}),
    ("open https://ubuntu.com", "open_url", {"url": "https://ubuntu.com"}),
    ("go to my documents folder", "open_path", {"path": "documents"}),
    ("find report.pdf", "search_files", {"query": "report.pdf"}),
    ("search for *.py in documents", "search_files", {"query": "*.py", "where": "documents"}),
    ("where is my resume", "search_files", {"query": "resume"}),
    ("info ~/.bashrc", "file_info", {"path": "~/.bashrc"}),
    ("cpu usage", "system_info", {"kind": "cpu"}),
    ("how much ram am I using", "system_info", {"kind": "ram"}),
    ("disk space", "system_info", {"kind": "disk"}),
    ("what's using my disk space", "storage_usage", {}),
    ("battery", "system_info", {"kind": "battery"}),
    ("am I online?", "system_info", {"kind": "network"}),
    ("uptime", "system_info", {"kind": "uptime"}),
    ("system status", "system_info", {"kind": "all"}),
    ("what time is it?", "datetime", {}),
    ("git status", "git_status", {}),
    ("git status in ~/code/app", "git_status", {"path": "~/code/app"}),
    ("show git log", "git_log", {}),
    ("git branches", "git_branches", {}),
    ("commit 'fix login bug'", "git_commit", {"message": "fix login bug"}),
    ("create branch feature/login", "git_create_branch", {"name": "feature/login"}),
    ("switch to branch main", "git_switch_branch", {"name": "main"}),
    ("add task buy milk", "add_task", {"title": "buy milk"}),
    ("add call the bank to my todo list", "add_task", {"title": "call the bank"}),
    ("show my tasks", "list_tasks", {}),
    ("done task 3", "complete_task", {"task_id": 3}),
    ("delete task #4", "delete_task", {"task_id": 4}),
    ("list reminders", "list_reminders", {}),
    ("remember that I prefer dark themes", "remember", {"fact": "I prefer dark themes"}),
    ("what do you remember about me", "list_memories", {}),
    ("forget memory 2", "forget", {"memory_id": 2}),
    ("clear chat history", "clear_history", {}),
    ("move ~/old.txt to trash", "trash_file", {"path": "~/old.txt"}),
    ("run backup", "run_custom", {"name": "backup"}),
    ("hello", "ui:greet", {}),
    ("help", "ui:help", {}),
    ("go to sleep", "ui:sleep", {}),
    ("focus mode on", "ui:quiet", {"on": True}),
    ("turn off focus mode", "ui:quiet", {"on": False}),
    ("dance", "ui:celebrate", {}),
])
def test_parse(text, action, args):
    it = parse(text)
    assert it is not None, text
    assert it.action == action
    assert it.args == args


def test_reminders():
    it = parse("remind me to stretch in 20 minutes")
    assert it.action == "add_reminder"
    assert it.args == {"text": "stretch", "minutes": 20.0}
    it = parse("remind me in 2 hours to call mum")
    assert it.args == {"text": "call mum", "minutes": 120.0}
    it = parse("remind me to drink water in an hour")
    assert it.args["minutes"] == 60.0 and it.args["text"] == "drink water"
    it = parse("remind me at 15:30 to join the meeting")
    assert it.action == "add_reminder" and it.args["text"] == "join the meeting"
    assert parse("remind me to do stuff").action == "ui:ask"


def test_durations_and_clock():
    assert parse_duration_minutes("in 30 sec") == pytest.approx(0.5)
    assert parse_duration_minutes("in 1 hour 30 minutes") == 90
    now = datetime(2026, 1, 1, 14, 0)
    assert parse_clock_minutes("at 3pm", now) == 60
    assert parse_clock_minutes("at 13:00", now) == 23 * 60     # tomorrow
    assert parse_clock_minutes("tomorrow at 9", now) == 19 * 60


@pytest.mark.parametrize("text", [
    "explain this python error: KeyError 'x'",
    "how do I rebase in git?",
    "tell me a joke",
    "why is my laptop slow",
])
def test_free_text_goes_to_ai(text):
    assert parse(text) is None
