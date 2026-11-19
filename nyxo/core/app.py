"""Application orchestrator: builds every subsystem and wires them together."""
from __future__ import annotations

import logging
import os

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .. import __version__
from ..ai.manager import AIManager
from ..assistant import personality
from ..assistant.assistant import Assistant
from ..character.sprites import SpriteBank
from ..desktop.companion import CompanionWindow
from ..desktop.presence import PresenceMonitor
from ..memory.db import Database
from ..notifications.notifier import Notifier, ReminderScheduler
from ..notifications.watcher import NotificationWatcher
from ..system.actions import ActionRegistry
from ..system.watcher import SystemWatcher
from ..ui import theme
from ..voice.stt import Listener
from ..voice.tts import Speaker
from ..voice.wakeword import WakeListener
from . import paths
from .config import Settings
from .single_instance import CommandServer
from . import brand

log = logging.getLogger("nyxo.app")

RESTART_CODE = 75


class NyxoApp(QObject):
    def __init__(self, qapp: QApplication, db: Database, native_wayland: bool):
        super().__init__()
        self.qapp = qapp
        self.db = db
        self.native = native_wayland
        self.mode_label = "native Wayland" if native_wayland else "XWayland (on GNOME Wayland)" \
            if os.environ.get("WAYLAND_DISPLAY") else "X11"
        self.exit_code = 0
        self.settings = Settings(db)
        self.icon_path = paths.icon_path(self.settings["avatar"])
        qapp.setWindowIcon(QIcon(str(self.icon_path)))
        qapp.setStyleSheet(theme.QSS)

        anim = paths.animations_dir(self.settings["avatar"])
        self.sprites = SpriteBank(anim, int(self.settings["character_size"]))
        self.preview_bank = SpriteBank(anim, 170)
        self.companion = CompanionWindow(self.settings, self.sprites, native_wayland)
        self.registry = ActionRegistry(db, self.settings.get)
        self.ai = AIManager(self.settings.get)
        self.assistant = Assistant(self.settings, db, self.registry, self.ai)
        self.speaker = Speaker(self.settings.get)
        self.listener = Listener(self.settings.get)
        self.notifier = Notifier(self.settings.get, self.icon_path)
        self.reminders = ReminderScheduler(db, self.notifier)
        self.watcher = SystemWatcher()
        self.notif_watch = NotificationWatcher()
        self.presence = PresenceMonitor(self.settings.get)
        self.wake = WakeListener(self.settings.get, is_speaking=self.speaker.speaking)
        self._notif_batch: list[str] = []
        self._notif_said: dict[str, float] = {}
        self._dnd_cache = (0.0, False)
        self._notif_timer = QTimer(self, singleShot=True, interval=1500, timeout=self._announce_notifications)
        self.ipc = CommandServer(self)
        self._chat = None
        self._settings_win = None
        self._listen_cb = None
        self._stream_buf, self._stream_spoken = "", 0
        self._listen_from_voice = False
        self.tray = None
        self._wire()

    # ---------------------------------------------------------------- wiring
    def _wire(self) -> None:
        c, a = self.companion, self.assistant
        c.clicked.connect(self._on_click)
        c.double_clicked.connect(self.open_chat)
        c.menu_requested.connect(lambda pos: self._menu().exec(pos))
        c.woke_up.connect(lambda: self.say(personality.wake_line(self.settings["personality"])))
        c.wants_chatter.connect(self._chatter)

        a.busy.connect(lambda s: c.set_hold(s or None))
        a.reply_started.connect(self._on_reply_started)
        a.token.connect(self._on_token)
        a.reply_done.connect(self._on_reply)
        a.notice.connect(self._on_notice)
        a.confirm_needed.connect(self._on_confirm)
        a.ui_command.connect(self._on_ui_command)
        a.ai_status.connect(lambda ok, d: self._chat and self._chat.set_ai_status(ok, d))

        self.speaker.started.connect(lambda: c.set_hold("talking"))
        self.speaker.finished.connect(lambda: c.set_hold(None) if not a.is_busy() else None)
        self.speaker.level.connect(c.set_audio_level)
        self.watcher.alert.connect(self._on_alert)
        self.notif_watch.notified.connect(self._on_app_notification)
        self.presence.changed.connect(self._on_step_aside)
        self.wake.woke.connect(self._on_wake)
        self.wake.listening.connect(self._on_listening)
        self.wake.partial.connect(lambda t: self._chat and self._chat.show_partial(t))
        self.wake.final.connect(self._on_heard)
        self.wake.error.connect(lambda e: log.warning("wake word: %s", e))

        self.listener.listening.connect(self._on_listening)
        self.listener.partial.connect(lambda t: self._chat and self._chat.show_partial(t))
        self.listener.final.connect(self._on_heard)
        self.listener.error.connect(lambda e: (self.say(e, "confused"),
                                               self._chat and self._chat.set_listening(False)))

        self.reminders.fired.connect(self._on_reminder)
        self.ipc.command.connect(self.handle_command)
        self.settings.changed.connect(self._on_setting)

    def start(self) -> None:
        self.companion.start()
        self._make_tray()
        self.reminders.start()
        if self.settings["system_alerts"]:
            self.watcher.start()
        if self.settings["announce_notifications"]:
            self.notif_watch.start()
        self.presence.start()
        if self.settings["wake_word"] and self.settings["stt_enabled"]:
            self.wake.start()
        self.speaker.warm_up()
        if not self.settings["first_run_done"]:
            self.settings.set("first_run_done", True)
            QTimer.singleShot(1500, lambda: self.say(
                f"Hello. I'm {brand.NAME}, your desktop assistant. Double-click me to talk, right-click for "
                "options, or just drag me wherever you like.", "waving", 12000))
        else:
            self._greet_timer = QTimer(self, singleShot=True, interval=1500, timeout=self._startup_greeting)
            self._greet_timer.start()
        log.info(f"{brand.NAME} %s started (%s)", __version__, self.mode_label)

    # ------------------------------------------------------------------- UI
    def chat(self):
        if self._chat is None:
            from ..ui.chat_window import ChatWindow
            w = ChatWindow(str(self.icon_path))
            w.send.connect(lambda t: self.assistant.submit(t))
            w.talk.connect(lambda on: self.start_listening() if on else self.listener.stop())
            w.stop.connect(self._stop_all)
            w.clear.connect(self._confirm_clear)
            w.open_settings.connect(lambda: self.open_settings())
            w.confirm.connect(self.assistant.respond_confirmation)
            w.load_history(self.db.recent_messages(60))
            self._chat = w
            QTimer.singleShot(50, self._refresh_ai_status)
        return self._chat

    def _refresh_ai_status(self) -> None:
        import threading

        def work():
            ok, d = self.ai.status()
            self.assistant.ai_status.emit(ok, d)
        threading.Thread(target=work, daemon=True).start()

    def open_chat(self) -> None:
        self.companion.wake(greet=False)
        self.chat().present()
        self.companion.react("happy", 1.5)

    def open_settings(self, section: str | None = None) -> None:
        if self._settings_win is None:
            from ..settings.dialog import SettingsDialog
            self._settings_win = SettingsDialog(self)
        self._settings_win.show_section(section or "General")

    def say(self, text: str, mood: str | None = None, duration_ms: int | None = None,
            speak: bool = True) -> None:
        if mood:
            self.companion.react(mood, 3)
        self.companion.say(text, duration_ms)
        if speak and self.settings["tts_mode"] == "always":
            self.speaker.speak(text)

    def _startup_greeting(self) -> None:
        import time as _t
        from ..assistant import briefing
        last = float(self.db.get_setting("last_briefing", 0) or 0)
        # full briefing once per login session (not on quick restarts)
        if self.settings["briefing_on_start"] and _t.time() - last > 1800:
            self.db.set_setting("last_briefing", _t.time())
            self.say(briefing.build(self.db, self.settings["user_name"]), "waving", 16000)
        else:
            self.say(personality.greeting(self.settings["personality"], self.settings["user_name"]), "waving")

    # -------------------------------------------------- notification announcer
    def _dnd(self) -> bool:
        import time as _t
        ts, val = self._dnd_cache
        if _t.monotonic() - ts > 30:
            from ..system import runner
            r = runner.run(["gsettings", "get", "org.gnome.desktop.notifications", "show-banners"], timeout=3)
            val = r.ok and r.out.strip() == "false"
            self._dnd_cache = (_t.monotonic(), val)
        return val

    def _on_app_notification(self, app: str) -> None:
        if app.startswith("NYXO selftest"):
            return                      # sent by the test suite
        self._notif_batch.append(app)
        if not self._notif_timer.isActive():
            self._notif_timer.start()

    def _announce_notifications(self) -> None:
        import time as _t
        batch, self._notif_batch = self._notif_batch, []
        if not batch or not self.settings["announce_notifications"] or self.settings["paused"]:
            return
        now = _t.monotonic()
        fresh = [a for a in dict.fromkeys(batch) if now - self._notif_said.get(a, -1e9) > 20]
        if not fresh or not self.companion.isVisible():
            return
        for a in fresh:
            self._notif_said[a] = now
        if len(fresh) == 1:
            n = batch.count(fresh[0])
            text = (f"New notification from {fresh[0]}." if n == 1
                    else f"{n} new notifications from {fresh[0]}.")
        else:
            text = "New notifications from " + ", ".join(fresh[:-1]) + f" and {fresh[-1]}."
        if self.companion.auto_hidden:
            return          # watching something: stay silent (still counted for "any notifications?")
        quiet = self.settings["quiet_mode"] or self._dnd()
        self.companion.wake(greet=False)
        if not quiet:
            self.companion.react("excited", 1.8)
        log.info("announced notification(s) from: %s", ", ".join(fresh))
        self.companion.say("🔔 " + text, 6000)
        if (self.settings["speak_notifications"] and not quiet
                and self.settings["tts_mode"] != "never" and not self.speaker.speaking()):
            self.speaker.speak(text)

    def notifications_summary(self) -> str:
        recent = self.notif_watch.take_recent()
        if not recent:
            return "No new notifications since you last asked."
        parts = [f"{n} from {app}" if n > 1 else f"one from {app}" for app, n in recent.most_common()]
        total = sum(recent.values())
        return f"You've had {total} notification{'s' if total != 1 else ''}: " + ", ".join(parts) + "."

    def _on_step_aside(self, on: bool, reason: str) -> None:
        self.companion.step_aside(on)
        if not on:
            missed = sum(self.notif_watch.recent.values())
            if missed:
                QTimer.singleShot(900, lambda: self.say(
                    f"Welcome back. You had {missed} notification{'s' if missed != 1 else ''} meanwhile. "
                    "Ask me “any notifications?” for details.", "waving", 7000))

    def _on_wake(self) -> None:
        if self.companion.auto_hidden:
            self.companion.step_aside(False)
        elif self.companion.user_hidden:
            self.companion.show_all()
        self.speaker.stop()
        self.companion.wake(greet=False)
        self.notifier.sound()
        self.assistant.last_via_voice = True

    def _on_alert(self, text: str, mood: str) -> None:
        urgent = mood in ("error", "sad")
        if not urgent and (self.companion.auto_hidden or not self.companion.isVisible()):
            return
        if not self.companion.isVisible():
            self.companion.show_all()
        self.companion.wake(greet=False)
        self.say(text, mood, 10000)

    def _menu(self) -> QMenu:
        m = QMenu()
        self._fill_menu(m)
        return m

    def _fill_menu(self, m: QMenu) -> None:
        hidden = not self.companion.isVisible()
        m.addAction(f"💬  Chat with {brand.NAME}", self.open_chat)
        if self.settings["stt_enabled"]:
            m.addAction(f"🎙  Talk to {brand.NAME}", self.start_listening)
        m.addSeparator()
        fun = m.addMenu("✨  Do something")
        fun.addAction("Wave", lambda: self.companion.react("waving", 3))
        fun.addAction("Dance", lambda: self.companion.react("celebrating", 4))
        fun.addAction("Think hard", lambda: self.companion.react("thinking", 4))
        fun.addAction("Go to sleep", self.companion.sleep)
        fun.addAction("Wake up", lambda: self.companion.wake(True))
        m.addSeparator()
        q = QAction("🎯  Focus mode", m, checkable=True, checked=bool(self.settings["quiet_mode"]))
        q.toggled.connect(lambda v: self.settings.set("quiet_mode", v))
        m.addAction(q)
        p = QAction("⏸  Pause", m, checkable=True, checked=bool(self.settings["paused"]))
        p.toggled.connect(lambda v: self.settings.set("paused", v))
        m.addAction(p)
        m.addAction(f"👁  Show {brand.NAME}" if hidden else f"🙈  Hide {brand.NAME}",
                    self.show_companion if hidden else self.hide_companion)
        m.addAction("🏠  Reset position", self.companion.go_home)
        m.addSeparator()
        m.addAction("⚙  Settings", self.open_settings)
        m.addAction(f"⏻  Quit {brand.NAME}", self.quit)

    def _make_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            log.info("no system tray available; use the right-click menu or `nyxo --show`")
            return
        self.tray = QSystemTrayIcon(QIcon(str(self.icon_path)), self)
        self.tray.setToolTip(f"{brand.NAME} — Your desktop. Your companion. Your AI.")
        self.tray.activated.connect(lambda r: self.toggle_companion()
                                    if r == QSystemTrayIcon.Trigger else None)
        self._tray_menu = QMenu()
        self._tray_menu.aboutToShow.connect(self._rebuild_tray_menu)
        self.tray.setContextMenu(self._tray_menu)
        self._rebuild_tray_menu()
        self.tray.show()

    def _rebuild_tray_menu(self) -> None:
        self._tray_menu.clear()
        self._fill_menu(self._tray_menu)

    def hide_companion(self) -> None:
        self.companion.hide_all()

    def show_companion(self) -> None:
        self.companion.show_all()
        self.companion.react("waving", 2)

    def toggle_companion(self) -> None:
        (self.hide_companion if self.companion.isVisible() else self.show_companion)()

    # ------------------------------------------------------------- handlers
    def _on_click(self) -> None:
        if self.companion.activity == "sleep":
            self.companion.slept_from_idle = False
            self.companion.wake(greet=True)
            return
        self.companion.react(self.companion.rng.choice(["happy", "waving"]), 2)
        self.say(personality.click_quip(self.settings["personality"]))

    def _chatter(self) -> None:
        if self.assistant.is_busy() or (self._chat and self._chat.isActiveWindow()):
            return
        self.say(personality.chatter(self.settings["personality"]), "happy")

    def _speak_maybe(self, text: str) -> None:
        mode = self.settings["tts_mode"]
        if mode == "always" or (mode == "voice_only" and self.assistant.last_via_voice):
            self.speaker.speak(text)

    def _speech_on(self) -> bool:
        mode = self.settings["tts_mode"]
        return mode == "always" or (mode == "voice_only" and self.assistant.last_via_voice)

    def _on_reply_started(self) -> None:
        self._stream_buf, self._stream_spoken = "", 0
        if self._chat:
            self._chat.begin_stream()

    def _on_token(self, tok: str) -> None:
        if self._chat:
            self._chat.stream_token(tok)
        self._stream_buf += tok
        if not self._speech_on():
            return
        # speak each completed sentence right away (skip code blocks and ACTION lines)
        pending = self._stream_buf[self._stream_spoken:]
        if self._stream_buf.count("```") % 2 == 1:
            return
        import re as _re
        m = None
        for m in _re.finditer(r"[.!?:](?=\s)|\n\n", pending):
            pass
        if m is not None and m.end() >= 20:
            chunk = pending[:m.end()]
            self._stream_spoken += m.end()
            chunk = _re.sub(r"(?m)^\s*ACTION:.*$", "", chunk)
            if chunk.strip():
                self.speaker.say_more(chunk)

    def _on_reply(self, text: str, mood: str) -> None:
        if self._chat:
            self._chat.end_stream(text)
        if text:
            self.companion.react(mood, 2)
            if not (self._chat and self._chat.isActiveWindow()):
                self.companion.say(text)
            if self._speech_on():
                spoken = getattr(self, "_stream_spoken", 0)
                rest = self._stream_buf[spoken:] if spoken else ""
                import re as _re
                rest = _re.sub(r"(?m)^\s*ACTION:.*$", "", rest)
                if spoken and rest.strip():
                    self.speaker.say_more(rest)
                elif not spoken:
                    self.speaker.speak(text)

    def _on_notice(self, text: str, mood: str) -> None:
        if self._chat:
            self._chat.end_stream("")
            self._chat.add_message("assistant", text)
        self.companion.react(mood, 3 if mood == "celebrating" else 2)
        if not (self._chat and self._chat.isVisible()):
            self.companion.say(text)
        self._speak_maybe(text)

    def _on_confirm(self, req_id: int, explanation: str) -> None:
        self.companion.react("confused", 3)
        self.chat().present()
        self.chat().add_confirm(req_id, explanation)
        self.companion.say("I need your OK for that — check the chat window.")

    def _on_ui_command(self, name: str, args: dict) -> None:
        c = self.companion
        if name == "sleep":
            QTimer.singleShot(1500, c.sleep)
        elif name == "wake":
            c.wake(greet=False)
        elif name == "hide":
            QTimer.singleShot(1800, self.hide_companion)
        elif name == "quiet":
            self.settings.set("quiet_mode", bool(args.get("on")))
        elif name == "settings":
            self.open_settings()
        elif name == "chat":
            self.open_chat()
        elif name == "notifications":
            self.say(self.notifications_summary(), "working", 9000)
        elif name == "briefing":
            from ..assistant import briefing
            self.say(briefing.build(self.db, self.settings["user_name"]), "working", 16000)

    def _on_reminder(self, text: str) -> None:
        if not self.companion.isVisible():
            self.companion.show_all()
        self.companion.wake(greet=False)
        self.companion.react("excited", 5)
        self.companion.say(f"⏰ Reminder: {text}", 15000)
        if self.settings["tts_mode"] != "never":
            self.speaker.speak(f"Excuse me. A reminder: {text}")

    def _on_setting(self, key: str, value) -> None:
        if key.startswith(("ai_provider", "ollama_", "anthropic_", "openai_", "ai_temperature")):
            self.ai.rebuild()
            if self._chat:
                self._refresh_ai_status()
        if key == "quiet_mode":
            self.companion.react("working" if value else "happy", 2)
        if key in ("wake_word", "stt_enabled"):
            want = self.settings["wake_word"] and self.settings["stt_enabled"]
            self.wake.start() if want else self.wake.stop()
        if key in ("auto_hide_fullscreen", "auto_hide_media") and not value and self.companion.auto_hidden:
            self.companion.step_aside(False)
        if key == "announce_notifications":
            self.notif_watch.start() if value else self.notif_watch.stop()
        if key == "display_mode":
            log.info("display mode changed to %s (applies on restart)", value)

    # ----------------------------------------------------------------- voice
    def start_listening(self, from_settings=None) -> None:
        if not self.settings["stt_enabled"]:
            self.say("Push-to-talk is off. Enable it in Settings → Voice.", "confused")
            return
        self.speaker.stop()
        self._listen_cb = from_settings
        self.wake.suspended = True
        self.listener.start()

    def _on_listening(self, on: bool) -> None:
        if not on and not self.listener.active():
            self.wake.suspended = False
        self.companion.set_hold("listening" if on else None)
        if self._chat:
            self._chat.set_listening(on)
        if on and not self._listen_cb:
            self.companion.say("Yes? I'm listening… 👂", 4000)

    def _on_heard(self, text: str) -> None:
        self.wake.suspended = False
        cb, self._listen_cb = self._listen_cb, None
        if cb:
            cb(text)
            return
        if self._chat:
            self._chat.input.clear()
        if not text:
            self.say("I didn't catch that. Try again?", "confused")
            return
        if self._chat:
            self._chat.add_message("user", f"🎙 {text}")
        else:
            self.companion.say(f"🎙 “{text}”", 2500)
        self.assistant.submit(text, via_voice=True)

    def _stop_all(self) -> None:
        self.assistant.cancel()
        self.speaker.stop()
        self.listener.stop()
        self.companion.set_hold(None)

    def _confirm_clear(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        if QMessageBox.question(self.chat(), "Clear conversation",
                                "Erase the whole conversation history?") == QMessageBox.Yes:
            self.clear_chat()

    def clear_chat(self) -> None:
        self.db.clear_messages()
        self.db.log_action("clear_history", {}, True, True, "cleared from UI")
        if self._chat:
            self._chat.clear_view()

    # ------------------------------------------------------------- commands
    def handle_command(self, cmd: str) -> None:
        cmd = cmd.strip().lower()
        if cmd in ("show", "launch", ""):
            self.show_companion()
        elif cmd == "hide":
            self.hide_companion()
        elif cmd == "toggle":
            self.toggle_companion()
        elif cmd == "chat":
            self.show_companion() if not self.companion.isVisible() else None
            self.open_chat()
        elif cmd == "listen":
            self.start_listening()
        elif cmd == "settings":
            self.open_settings()
        elif cmd == "quit":
            self.quit()
        else:
            log.warning("unknown IPC command %r", cmd)

    def restart(self) -> None:
        self.exit_code = RESTART_CODE
        self.quit(code=RESTART_CODE)

    def shutdown(self) -> None:
        """Stop every timer, worker and child process. Safe to call twice."""
        self.companion.save_position()
        self.companion.stop_timers()
        self.reminders.stop()
        self.watcher.stop()
        self.notif_watch.stop()
        self._notif_timer.stop()
        self.presence.stop()
        self.wake.stop()
        if getattr(self, "_greet_timer", None):
            self._greet_timer.stop()
        self.assistant.cancel()
        if self.assistant._worker:
            self.assistant._worker.join(timeout=3)
        self.speaker.stop()
        self.listener.stop()
        self.ai.shutdown()
        self.ipc.close()
        if self.tray:
            self.tray.hide()
        self.companion.hide_all()

    def quit(self, code: int = 0) -> None:
        log.info("shutting down (code %s)", code)
        self.exit_code = code
        try:
            self.shutdown()
        finally:
            self.qapp.exit(code)


