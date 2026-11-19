"""Announce *which app* sent a desktop notification — never what it says.

GNOME apps notify through two D-Bus calls on the session bus:
  * org.freedesktop.Notifications.Notify(app_name, …)        (most apps, notify-send, Electron, Firefox)
  * org.gtk.Notifications.AddNotification(app_id, …)          (GApplication / portal / Flatpak apps)
NYXO runs `dbus-monitor` (the session bus lets a user observe their own
session) filtered to exactly those two methods and reads ONLY the first
argument of each call — the sender's name/ID. Titles, bodies, icons and
actions are skipped line-by-line without being parsed or stored."""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading
import time
from collections import Counter

from PySide6.QtCore import QObject, Signal

from ..security import policy

log = logging.getLogger("nyxo.notifywatch")

MATCH_RULES = [
    "type='method_call',interface='org.freedesktop.Notifications',member='Notify'",
    "type='method_call',interface='org.gtk.Notifications',member='AddNotification'",
]
_HEADER = re.compile(r"^method call .*member=(Notify|AddNotification)\s*$")
_FIRST_STRING = re.compile(r'^\s+string "(.*)"\s*$')
IGNORE = {"nyxo", "notify-send", ""}


def friendly_name(app: str) -> str:
    """'org.gnome.Calendar' -> 'Calendar', 'firefox_firefox' -> 'Firefox'."""
    a = app.strip()
    try:
        from ..system import apps
        all_apps = apps.list_apps()
        for key in (a, a.removesuffix(".desktop"), f"{a}_{a}", a.lower()):
            if key in all_apps:
                return all_apps[key].name
        for entry in all_apps.values():
            if entry.name.lower() == a.lower():
                return entry.name
    except Exception:  # noqa: BLE001
        pass
    if "." in a:
        a = a.rsplit(".", 1)[-1]
    a = a.split("_")[0].replace("-", " ")
    return a[:1].upper() + a[1:] if a else "an app"


class NotificationParser:
    """Feed dbus-monitor lines; returns an app name when a call header is
    followed by its first string argument. Everything else is ignored."""

    def __init__(self):
        self._want_first_arg = False

    def feed(self, line: str) -> str | None:
        if _HEADER.match(line):
            self._want_first_arg = True
            return None
        if self._want_first_arg:
            self._want_first_arg = False      # only ever look at the very next line
            m = _FIRST_STRING.match(line)
            if m:
                return m.group(1)
        return None


class NotificationWatcher(QObject):
    notified = Signal(str)          # friendly app name

    def __init__(self, dedupe_s: float = 2.0):
        super().__init__()
        self._proc: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last: dict[str, float] = {}
        self.dedupe_s = dedupe_s
        self.recent: Counter[str] = Counter()     # in memory only, cleared on read

    @staticmethod
    def available() -> bool:
        return shutil.which("dbus-monitor") is not None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not self.available():
            log.info("dbus-monitor not found; notification announcements disabled")
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    def handle(self, raw_app: str, now: float | None = None) -> str | None:
        """Dedupe + filter one notification; returns the friendly name if new."""
        now = time.monotonic() if now is None else now
        from ..core import brand
        if raw_app.strip().lower() in IGNORE or raw_app.strip().lower() == brand.NAME.lower():
            return None
        name = friendly_name(raw_app)
        if name.lower() in IGNORE:
            return None
        if now - self._last.get(name, -1e9) < self.dedupe_s:   # relayed duplicate
            return None
        self._last[name] = now
        self.recent[name] += 1
        return name

    def take_recent(self) -> Counter:
        r, self.recent = self.recent, Counter()
        return r

    def _run(self) -> None:
        backoff = 2.0
        while not self._stop.is_set():
            parser = NotificationParser()
            try:
                policy.check_binary("dbus-monitor")
                self._proc = subprocess.Popen(["dbus-monitor", "--session", *MATCH_RULES],
                                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                              stdin=subprocess.DEVNULL, text=True, bufsize=1)
                log.info("watching notifications (app names only)")
                for line in self._proc.stdout:
                    if self._stop.is_set():
                        break
                    app = parser.feed(line)
                    if app is not None:
                        name = self.handle(app)
                        if name:
                            self.notified.emit(name)
            except Exception as e:  # noqa: BLE001
                log.warning("notification watcher error: %s", e)
            finally:
                if self._proc and self._proc.poll() is None:
                    self._proc.terminate()
            if self._stop.wait(backoff):
                break
            backoff = min(60.0, backoff * 2)
