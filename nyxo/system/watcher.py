"""Proactive system monitoring (JARVIS-style alerts).

Polls cheap psutil counters once a minute and raises an alert only on state
*changes* (with cooldowns), so NYXO speaks up rarely and only when useful."""
from __future__ import annotations

import socket
import time

import psutil
from PySide6.QtCore import QObject, QTimer, Signal


def _online() -> bool:
    try:
        with socket.create_connection(("1.1.1.1", 53), timeout=1.5):
            return True
    except OSError:
        return False


class SystemWatcher(QObject):
    alert = Signal(str, str)      # text, mood

    def __init__(self, interval_ms: int = 60_000):
        super().__init__()
        self.timer = QTimer(self, interval=interval_ms, timeout=self.check)
        self._last: dict[str, float] = {}
        self._battery_level_warned: int | None = None
        self._plugged: bool | None = None
        self._online: bool | None = None
        self._cpu_high_since: float | None = None

    def start(self) -> None:
        psutil.cpu_percent(interval=None)    # prime the counter
        self.timer.start()

    def stop(self) -> None:
        self.timer.stop()

    def _cool(self, key: str, seconds: float) -> bool:
        now = time.monotonic()
        if now - self._last.get(key, -1e9) < seconds:
            return False
        self._last[key] = now
        return True

    def check(self, online_probe=_online) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []

        def emit(text: str, mood: str) -> None:
            out.append((text, mood))
            self.alert.emit(text, mood)

        b = psutil.sensors_battery()
        if b is not None:
            if self._plugged is not None and b.power_plugged != self._plugged:
                emit("Power connected. Charging." if b.power_plugged
                     else f"Running on battery now, at {b.percent:.0f} percent.", "working")
            self._plugged = b.power_plugged
            if not b.power_plugged:
                for level, mood in ((5, "error"), (10, "sad"), (20, "confused")):
                    if b.percent <= level and (self._battery_level_warned is None
                                               or self._battery_level_warned > level):
                        self._battery_level_warned = level
                        emit(f"Battery is at {b.percent:.0f} percent. I'd recommend plugging in soon."
                             if level > 5 else f"Battery critical at {b.percent:.0f} percent. Please plug in now.",
                             mood)
                        break
            else:
                self._battery_level_warned = None

        try:
            d = psutil.disk_usage("/")
            if d.percent >= 95 and self._cool("disk", 6 * 3600):
                emit(f"Your system drive is {d.percent:.0f} percent full, with "
                     f"{d.free / 1024**3:.1f} gigabytes left. Ask me what's using the space.", "confused")
        except OSError:
            pass

        cpu = psutil.cpu_percent(interval=None)
        now = time.monotonic()
        if cpu > 90:
            self._cpu_high_since = self._cpu_high_since or now
            if now - self._cpu_high_since > 120 and self._cool("cpu", 1800):
                top = max(psutil.process_iter(["name", "cpu_percent"]),
                          key=lambda p: p.info.get("cpu_percent") or 0, default=None)
                name = top.info["name"] if top else "something"
                emit(f"The processor has been above 90 percent for a couple of minutes. "
                     f"The busiest process is {name}.", "working")
        else:
            self._cpu_high_since = None

        on = online_probe()
        if self._online is not None and on != self._online:
            emit("Network connection restored." if on else "We've lost the internet connection.",
                 "happy" if on else "sad")
        self._online = on
        return out
