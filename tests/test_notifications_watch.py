import os
import shutil
import subprocess

import pytest

from nyxo.assistant import intents
from nyxo.notifications.watcher import NotificationParser, NotificationWatcher, friendly_name

SAMPLE = """\
signal time=1.0 sender=org.freedesktop.DBus -> destination=:1.4 serial=2 path=/org/freedesktop/DBus; interface=org.freedesktop.DBus; member=NameAcquired
   string ":1.4"
method call time=2.0 sender=:1.434 -> destination=:1.33 serial=9 path=/org/freedesktop/Notifications; interface=org.freedesktop.Notifications; member=Notify
   string "Slack"
   uint32 0
   string ""
   string "SECRET TITLE"
   string "SECRET BODY"
   array [
   ]
   int32 -1
method call time=3.0 sender=:1.435 -> destination=org.gtk.Notifications serial=3 path=/org/gtk/Notifications; interface=org.gtk.Notifications; member=AddNotification
   string "org.gnome.Calendar"
   string "event-1"
   array [
      dict entry(
         string "title"
         variant             string "SECRET MEETING"
      )
   ]
"""


def test_parser_reads_only_app_names():
    p = NotificationParser()
    apps = [a for a in (p.feed(line) for line in SAMPLE.splitlines()) if a is not None]
    assert apps == ["Slack", "org.gnome.Calendar"]
    assert not any("SECRET" in a for a in apps)


def test_friendly_names():
    assert friendly_name("Slack") == "Slack"
    assert friendly_name("org.gnome.Calendar") == "Calendar"
    assert friendly_name("com.example.my-app") == "My app"


def test_dedupe_ignore_and_summary(qapp):
    w = NotificationWatcher(dedupe_s=2.0)
    assert w.handle("Slack", now=10.0) == "Slack"
    assert w.handle("Slack", now=10.5) is None          # relayed duplicate
    assert w.handle("Slack", now=13.0) == "Slack"
    assert w.handle("NYXO", now=14.0) is None           # our own notifications
    assert w.handle("Firefox", now=14.0) == "Firefox"
    assert w.take_recent() == {"Slack": 2, "Firefox": 1}
    assert w.take_recent() == {}


def test_intent():
    for t in ("any notifications?", "who messaged me", "what notifications did I miss", "check notifications"):
        assert intents.parse(t).action == "ui:notifications", t


def test_app_announces(qapp, qtbot, tmp_path, monkeypatch):
    from nyxo.core.app import NyxoApp
    from nyxo.memory.db import Database
    db = Database(tmp_path / "n.db")
    for k, v in (("ai_provider", "offline"), ("first_run_done", True), ("tts_mode", "never"),
                 ("announce_notifications", False)):
        db.set_setting(k, v)
    app = NyxoApp(qapp, db, native_wayland=False)
    app.start()
    monkeypatch.setattr(app, "_dnd", lambda: False)
    app.settings.set("announce_notifications", True)
    said = []
    monkeypatch.setattr(app.companion, "say",
                        lambda text, *a, **k: said.append(text) if text.startswith("🔔") else None)
    for name in ("Slack", "Slack", "Firefox"):
        app._on_app_notification(name)
    qtbot.waitUntil(lambda: bool(said), timeout=4000)
    assert said[0] == "🔔 New notifications from Slack and Firefox."
    app._on_app_notification("Slack")                    # within cooldown: no repeat
    qtbot.wait(1800)
    assert len(said) == 1
    app.notif_watch.recent.update({"Slack": 2})
    assert "2 from Slack" in app.notifications_summary()
    app.shutdown()
    db.close()


@pytest.mark.skipif(not (os.environ.get("DBUS_SESSION_BUS_ADDRESS") and shutil.which("dbus-monitor")
                         and shutil.which("notify-send")), reason="needs a real session bus")
def test_live_session_bus(qapp, qtbot):
    w = NotificationWatcher()
    got = []
    w.notified.connect(got.append)
    w.start()
    qtbot.wait(1500)
    subprocess.run(["notify-send", "-a", "NYXO selftest", "-t", "1", "private title", "private body"], check=False)
    qtbot.waitUntil(lambda: bool(got), timeout=6000)
    w.stop()
    assert got[0] == "NYXO selftest"
