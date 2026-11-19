"""Integration tests: the full NyxoApp on the offscreen Qt platform."""
import time

import pytest

from nyxo.character.states import ANIMATIONS, State


@pytest.fixture
def app(qapp, tmp_path):
    from nyxo.core.app import NyxoApp
    from nyxo.memory.db import Database
    db = Database(tmp_path / "t.db")
    db.set_setting("ai_provider", "offline")
    db.set_setting("first_run_done", True)
    db.set_setting("tts_mode", "never")          # keep the test run silent
    a = NyxoApp(qapp, db, native_wayland=False)
    a.start()
    yield a
    a.shutdown()
    db.close()


def wait_for(qtbot, cond, timeout=8000):
    qtbot.waitUntil(lambda: bool(cond()), timeout=timeout)


def test_sprites_all_states(qapp):
    from nyxo.character.sprites import SpriteBank
    from nyxo.core import paths
    bank = SpriteBank(paths.assets_dir() / "animations", 120)
    for st in State:
        frames = bank.frames(st)
        assert len(frames) == ANIMATIONS[st].frames
        assert frames[0].width() == 120
        # not blank: some pixels are opaque
        img = frames[0].toImage()
        assert any(img.pixelColor(x, y).alpha() > 200 for x in range(0, 120, 6) for y in range(0, 120, 6))
    assert len(bank.frames(State.WALKING, flip=True)) == ANIMATIONS[State.WALKING].frames


def test_companion_states_and_movement(app, qtbot):
    c = app.companion
    assert c.isVisible()
    assert c.current_state() == State.IDLE
    c.react("happy", 0.3)
    assert c.current_state() == State.HAPPY
    wait_for(qtbot, lambda: c.current_state() == State.IDLE, 3000)
    x0 = c.rx
    lo, hi = c._x_bounds()
    c.walk_to(lo if x0 - lo > hi - x0 else hi)
    assert c.current_state() == State.WALKING
    qtbot.wait(700)
    assert c.rx != x0
    c.sleep()
    assert c.current_state() == State.SLEEPING
    c.wake(greet=True)
    assert c.current_state() == State.WAVING
    c.set_hold("thinking")
    assert c.current_state() == State.THINKING
    c.set_hold(None)


def test_hide_show(app):
    app.hide_companion()
    assert not app.companion.isVisible()
    app.handle_command("show")
    assert app.companion.isVisible()
    app.handle_command("toggle")
    assert not app.companion.isVisible()
    app.handle_command("toggle")
    assert app.companion.isVisible()


def test_chat_command_without_ai(app, qtbot):
    chat = app.chat()
    chat.input.setText("add task integration test")
    chat._send()
    wait_for(qtbot, lambda: app.db.list_tasks())
    assert app.db.list_tasks()[0]["title"] == "integration test"
    wait_for(qtbot, lambda: any("Added task" in m["content"] for m in app.db.recent_messages()))


def test_ai_unavailable_is_clear(app, qtbot):
    got = []
    app.assistant.notice.connect(lambda t, m: got.append(t))
    app.assistant.submit("tell me a joke about penguins")
    wait_for(qtbot, lambda: got)
    assert "AI unavailable" in got[0]


def test_confirmation_flow(app, qtbot):
    tid = app.db.add_task("to delete")
    asks = []
    app.assistant.confirm_needed.connect(lambda rid, ex: asks.append(rid))
    app.assistant.submit(f"delete task {tid}")
    wait_for(qtbot, lambda: asks)
    assert app.db.list_tasks()                     # not deleted yet
    app.assistant.respond_confirmation(asks[0], False)
    qtbot.wait(300)
    assert app.db.list_tasks()                     # declined
    app.assistant.submit(f"delete task {tid}")
    wait_for(qtbot, lambda: len(asks) == 2)
    app.assistant.respond_confirmation(asks[1], True)
    wait_for(qtbot, lambda: not app.db.list_tasks())


def test_reminder_fires(app, qtbot, monkeypatch):
    fired = []
    monkeypatch.setattr(app.notifier, "notify", lambda *a, **k: fired.append(a))
    app.db.add_reminder("stand up", time.time() - 1)
    app.reminders.check()
    assert fired and "stand up" in fired[0][1]
    assert app.companion.current_state() == State.EXCITED


def test_settings_persist_and_dialog(app, qtbot):
    app.settings.set("character_size", 150)
    assert app.companion.width() == 150
    assert app.db.get_setting("character_size") == 150
    app.open_settings("Memory")
    dlg = app._settings_win
    assert dlg.isVisible()
    for i in range(dlg.nav.count()):
        dlg.nav.setCurrentRow(i)          # every page builds & refreshes without error
    qtbot.wait(200)
    dlg.close()


def test_ipc_single_instance(app, qtbot):
    from nyxo.core.single_instance import InstanceLock, send_command
    lock1, lock2 = InstanceLock(), InstanceLock()
    assert lock1.acquire()
    assert not lock2.acquire()
    lock1.release()
    got = []
    app.ipc.command.connect(got.append)
    assert send_command("hide")
    wait_for(qtbot, lambda: got == ["hide"])
    assert not app.companion.isVisible()
