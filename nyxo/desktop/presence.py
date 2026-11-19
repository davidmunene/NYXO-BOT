"""Detect when NYXO should step aside: a full-screen app (video, game,
presentation) or media that blocks the screen from sleeping.

Sources (both legitimate on GNOME Wayland, no window introspection):
  1. NYXO Helper GNOME Shell extension (io.nyxo.ShellHelper) — exact
     full-screen state; active after the extension is loaded (next login).
  2. GNOME SessionManager idle inhibition (flag 8) — set by video players,
     browsers playing video and presentation tools."""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtDBus import QDBusConnection, QDBusInterface

log = logging.getLogger("nyxo.presence")

INHIBIT_IDLE = 8


class PresenceMonitor(QObject):
    changed = Signal(bool, str)          # step_aside, reason

    def __init__(self, get, interval_ms: int = 3000):
        super().__init__()
        self._get = get
        self.state = False
        self.reason = ""
        self._streak = 0
        self.timer = QTimer(self, interval=interval_ms, timeout=self.poll)
        bus = QDBusConnection.sessionBus()
        self._helper = QDBusInterface("io.nyxo.ShellHelper", "/io/nyxo/ShellHelper",
                                      "io.nyxo.ShellHelper", bus)
        self._session = QDBusInterface("org.gnome.SessionManager", "/org/gnome/SessionManager",
                                       "org.gnome.SessionManager", bus)

    def start(self) -> None:
        self.timer.start()

    def stop(self) -> None:
        self.timer.stop()

    def helper_available(self) -> bool:
        return QDBusConnection.sessionBus().interface().isServiceRegistered("io.nyxo.ShellHelper").value()

    def _fullscreen(self) -> bool | None:
        if not self.helper_available():
            return None
        args = self._helper.call("InFullscreen").arguments()
        return bool(args[0]) if args else None

    def _inhibited(self) -> bool:
        args = self._session.call("IsInhibited", INHIBIT_IDLE).arguments()
        return bool(args[0]) if args else False

    def evaluate(self, fullscreen: bool | None, inhibited: bool) -> tuple[bool, str]:
        if self._get("auto_hide_fullscreen") and fullscreen:
            return True, "fullscreen"
        if self._get("auto_hide_media") and inhibited:
            return True, "media"
        return False, ""

    def poll(self) -> None:
        want, reason = self.evaluate(self._fullscreen(), self._inhibited())
        # require two consecutive readings to avoid flicker
        self._streak = self._streak + 1 if want != self.state else 0
        if self._streak >= 2 or (want and reason == "fullscreen" and self._streak >= 1):
            self._streak = 0
            self.state, self.reason = want, reason
            log.info("step aside: %s (%s)", want, reason or "-")
            self.changed.emit(want, reason)
