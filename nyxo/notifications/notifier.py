"""Desktop notifications (notify-send) and the reminder scheduler."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from ..memory.db import Database
from ..system import runner
from ..core import brand

log = logging.getLogger("nyxo.notify")

_SOUNDS = ("/usr/share/sounds/freedesktop/stereo/message-new-instant.oga",
           "/usr/share/sounds/freedesktop/stereo/complete.oga",
           "/usr/share/sounds/Yaru/stereo/message-new-instant.oga")


class Notifier:
    def __init__(self, get, icon: Path):
        self._get = get
        self.icon = icon

    def notify(self, title: str, body: str, urgent: bool = False) -> bool:
        if not self._get("notifications"):
            return False
        ok = False
        if shutil.which("notify-send"):
            argv = ["notify-send", "-a", f"{brand.NAME}", "-i", str(self.icon)]
            if urgent:
                argv += ["-u", "critical"]
            r = runner.run(argv + ["--", title, body], timeout=5)
            ok = r.ok
        if self._get("notify_sound"):
            self.sound()
        return ok

    def sound(self) -> None:
        try:
            if shutil.which("canberra-gtk-play"):
                runner.spawn(["canberra-gtk-play", "-i", "message-new-instant"])
                return
            for s in _SOUNDS:
                if Path(s).exists() and shutil.which("paplay"):
                    runner.spawn(["paplay", s])
                    return
        except Exception as e:  # noqa: BLE001
            log.debug("sound failed: %s", e)


class ReminderScheduler(QObject):
    fired = Signal(str)

    def __init__(self, db: Database, notifier: Notifier, interval_ms: int = 10_000):
        super().__init__()
        self.db = db
        self.notifier = notifier
        self.timer = QTimer(self, interval=interval_ms, timeout=self.check)
        self._first = QTimer(self, singleShot=True, interval=3000, timeout=self.check)

    def start(self) -> None:
        self.timer.start()
        self._first.start()                     # catch reminders missed while logged out

    def stop(self) -> None:
        self.timer.stop()
        self._first.stop()

    def check(self) -> list[dict]:
        due = self.db.due_reminders()
        for r in due:
            self.db.mark_reminder_fired(r["id"])
            self.notifier.notify(f"⏰ {brand.NAME} reminder", r["text"], urgent=True)
            self.fired.emit(r["text"])
        return due
