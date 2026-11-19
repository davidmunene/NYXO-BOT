"""User idle time from GNOME's Mutter IdleMonitor (works on Wayland; it only
reports *how long* since the last input, never the input itself)."""
from __future__ import annotations

from PySide6.QtDBus import QDBusConnection, QDBusInterface


class IdleMonitor:
    def __init__(self):
        self._iface = QDBusInterface("org.gnome.Mutter.IdleMonitor",
                                     "/org/gnome/Mutter/IdleMonitor/Core",
                                     "org.gnome.Mutter.IdleMonitor",
                                     QDBusConnection.sessionBus())
        self.available = self._iface.isValid()

    def idle_ms(self) -> int | None:
        if not self.available:
            return None
        args = self._iface.call("GetIdletime").arguments()
        try:
            return int(args[0]) if args else None
        except (TypeError, ValueError):
            return None
