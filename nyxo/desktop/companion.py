"""The floating NYXO character window.

Two display strategies (see docs/WAYLAND.md):

* XWayland mode (default): the window is exactly robot-sized and NYXO walks by
  moving the window itself. Mutter honours position + keep-above for X11
  clients. A separate bubble window follows the robot.
* Native Wayland mode: Wayland clients can't position themselves or stay on
  top, so the window is a fixed transparent "stage" (placed once by the user
  with a drag) and NYXO walks inside it. The bubble is a child widget and the
  input region is masked to the robot + bubble so clicks pass through the rest.
"""
from __future__ import annotations

import logging
import random
import time

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QPainter, QRegion
from PySide6.QtWidgets import QWidget

from ..character.sprites import SpriteBank
from ..character.states import State
from ..core.config import Settings
from . import x11hints
from .bubble import SpeechBubble
from .idle import IdleMonitor
from ..core import brand

log = logging.getLogger("nyxo.companion")

MOODS = {
    "happy": State.HAPPY, "excited": State.EXCITED, "celebrating": State.CELEBRATING,
    "confused": State.CONFUSED, "sad": State.SAD, "error": State.ERROR,
    "thinking": State.THINKING, "working": State.WORKING, "waving": State.WAVING,
    "talking": State.TALKING, "listening": State.LISTENING,
}


def choose_activity(rng: random.Random, roam: bool, quiet: bool) -> str:
    """Pick NYXO's next autonomous activity."""
    if quiet:
        return rng.choices(["idle", "think"], [92, 8])[0]
    if not roam:
        return rng.choices(["idle", "wave", "think", "happy"], [80, 6, 8, 6])[0]
    return rng.choices(["idle", "walk", "run", "wave", "think", "happy"],
                       [44, 36, 6, 4, 6, 4])[0]


class CompanionWindow(QWidget):
    clicked = Signal()
    double_clicked = Signal()
    menu_requested = Signal(QPoint)
    woke_up = Signal()
    wants_chatter = Signal()

    def __init__(self, settings: Settings, sprites: SpriteBank, native_wayland: bool):
        super().__init__(None)
        self.settings = settings
        self.sprites = sprites
        self.native = native_wayland
        self.rng = random.Random()
        self.size_px = int(settings["character_size"])
        self.setWindowTitle(f"{brand.NAME}")
        flags = Qt.FramelessWindowHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
        if settings["always_on_top"]:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(False)

        # state
        self.activity = "idle"            # idle|walk|run|sleep|fall|drag
        self.hold: State | None = None    # assistant-driven (thinking/talking/...)
        self.temp: State | None = None
        self.temp_until = 0.0
        self.flip = False
        self.frame = 0
        self.target_x = 0.0
        self.vy = 0.0
        self.next_decision = time.monotonic() + 4
        self.next_chatter = time.monotonic() + self.rng.uniform(300, 600)
        self.slept_from_idle = False
        self.audio_level: float | None = None     # lip-sync level while speaking
        self._mouth = 0.0
        self.next_blink = time.monotonic() + self.rng.uniform(2, 5)
        self.next_glance = time.monotonic() + self.rng.uniform(15, 30)
        self._walk_started = 0.0
        self._press: QPoint | None = None
        self._press_offset = QPoint()
        self._dragging = False
        self._last_tick = time.monotonic()
        self.user_hidden = False
        self.auto_hidden = False          # stepped aside for a full-screen app / video

        # geometry: rx, ry = global top-left of robot (X mode) / local (native)
        self.rx = 0.0
        self.ry = 0.0

        self.bubble = SpeechBubble(self if self.native else None)
        self.bubble.clicked.connect(self.clicked)
        self.idle = IdleMonitor()

        self.frame_timer = QTimer(self, timeout=self._next_frame)
        self.move_timer = QTimer(self, interval=33, timeout=self._move_tick)
        self.brain_timer = QTimer(self, interval=1000, timeout=self._brain)
        self.idle_timer = QTimer(self, interval=4000, timeout=self._check_idle)
        self.micro_timer = QTimer(self, interval=250, timeout=self._micro)
        self._layout()
        self.settings.changed.connect(self._on_setting)

    # ------------------------------------------------------------------ setup
    def _layout(self) -> None:
        s = self.size_px
        if self.native:
            self.resize(max(int(s * 2.6), 360), s + 150)
        else:
            self.resize(s, s)

    def start(self) -> None:
        self._place_initial()
        self.show()
        if not self.native:
            QTimer.singleShot(300, lambda: x11hints.make_companion(int(self.winId()), self.settings["desktop_layer"]))
        self._restart_frame_timer()
        self.brain_timer.start()
        self.micro_timer.start()
        if self.idle.available:
            self.idle_timer.start()
        log.info("companion shown (%s mode) at %s", "wayland" if self.native else "xwayland",
                 self.pos().toTuple())

    def _screen_area(self) -> QRect:
        scr = None
        if not self.native:
            scr = QGuiApplication.screenAt(QPoint(int(self.rx + self.size_px / 2),
                                                  int(self.ry + self.size_px / 2)))
        scr = scr or self.screen() or QGuiApplication.primaryScreen()
        return scr.availableGeometry()

    def _floor_y(self) -> float:
        if self.native:
            return float(self.height() - self.size_px)
        a = self._screen_area()
        return float(a.bottom() + 1 - self.size_px + int(self.size_px * 5 / 128))

    def _x_bounds(self) -> tuple[float, float]:
        if self.native:
            return 0.0, float(self.width() - self.size_px)
        a = self._screen_area()
        return float(a.left()), float(a.right() + 1 - self.size_px)

    def _place_initial(self) -> None:
        pos = self.settings["position"]
        if self.native:
            self.rx = (self.width() - self.size_px) / 2
            self.ry = self._floor_y()
            self._apply_pos()
            return
        if pos and QGuiApplication.screenAt(QPoint(int(pos[0]) + 10, int(pos[1]) + 10)):
            self.rx, self.ry = float(pos[0]), float(pos[1])
        else:
            a = (QGuiApplication.primaryScreen()).availableGeometry()
            self.rx = float(a.right() - self.size_px - 120)
            self.ry = 0.0
            self.ry = self._floor_y()
        if self.settings["roam_area"] == "bottom":
            self.ry = self._floor_y()
        self._apply_pos()

    # --------------------------------------------------------------- position
    def _apply_pos(self) -> None:
        if self.native:
            self._update_mask()
            self._place_bubble()
            self.update()
        else:
            self.move(int(self.rx), int(self.ry))
            self._place_bubble()

    def robot_rect_global(self) -> QRect:
        if self.native:
            return QRect(self.mapToGlobal(QPoint(int(self.rx), int(self.ry))),
                         self.size()).adjusted(0, 0, 0, 0)
        return QRect(int(self.rx), int(self.ry), self.size_px, self.size_px)

    def _place_bubble(self) -> None:
        if not self.bubble.isVisible():
            return
        s = self.size_px
        bw, bh = self.bubble.width(), self.bubble.height()
        cx = self.rx + s * 0.5
        top = self.ry + s * 0.12
        if self.native:
            bx = max(0.0, min(self.width() - bw, cx - bw / 2))
            by = max(0.0, top - bh)
        else:
            a = self._screen_area()
            bx = max(a.left(), min(a.right() - bw, cx - bw / 2))
            by = max(a.top(), top - bh)
        self.bubble.tail_x = (cx - bx) / max(1, bw)
        self.bubble.move(int(bx), int(by))
        self.bubble.update()
        if self.native:
            self._update_mask()

    def _update_mask(self) -> None:
        if not self.native:
            return
        reg = QRegion(QRect(int(self.rx + self.size_px * 0.15), int(self.ry + self.size_px * 0.1),
                            int(self.size_px * 0.7), int(self.size_px * 0.9)))
        if self.bubble.isVisible():
            reg = reg.united(QRegion(self.bubble.geometry()))
        self.setMask(reg)

    def save_position(self) -> None:
        if not self.native:
            self.settings.set("position", [int(self.rx), int(self.ry)])

    # ---------------------------------------------------------------- states
    def current_state(self) -> State:
        if self.activity in ("drag", "fall"):
            return State.DRAGGED
        if self.temp and time.monotonic() < self.temp_until:
            return self.temp
        if self.hold:
            return self.hold
        return {"walk": State.WALKING, "run": State.RUNNING,
                "sleep": State.SLEEPING}.get(self.activity, State.IDLE)

    def _restart_frame_timer(self) -> None:
        if self.settings["animations_enabled"] and not self.settings["paused"]:
            fps = self.sprites.fps(self.current_state())
            self.frame_timer.start(int(1000 / max(1.0, fps)))
        else:
            self.frame_timer.stop()
            self.frame = 0
            self.update()

    def _state_changed(self) -> None:
        self.frame = 0
        self._restart_frame_timer()
        self.update()

    def react(self, mood: str, seconds: float = 2.5) -> None:
        st = MOODS.get(mood)
        if st is None:
            return
        if self.activity == "sleep" and st not in (State.ERROR,):
            self.wake(greet=False)
        self.temp = st
        self.temp_until = time.monotonic() + seconds
        self._state_changed()

    def play_state(self, state: State, seconds: float = 3.0) -> None:
        if self.activity == "sleep":
            self.activity = "idle"
        self.temp = state
        self.temp_until = time.monotonic() + seconds
        self._state_changed()

    def set_hold(self, mood: str | None) -> None:
        """Assistant-driven persistent state (None to release)."""
        st = MOODS.get(mood) if mood else None
        if st and self.activity in ("walk", "run"):
            self._stop_moving()
        if st and self.activity == "sleep":
            self.wake(greet=False)
        if st:
            self.temp = None
        if st != self.hold:
            self.hold = st
            self._state_changed()

    def sleep(self) -> None:
        self._stop_moving()
        self.activity = "sleep"
        self.temp = None
        self._state_changed()

    def wake(self, greet: bool = True) -> None:
        if self.activity == "sleep":
            self.activity = "idle"
            self.next_decision = time.monotonic() + 3
            if greet:
                self.react("waving", 2.4)
                self.woke_up.emit()
            else:
                self._state_changed()

    def say(self, text: str, duration_ms: int | None = None) -> None:
        if not text.strip() or not self.isVisible():
            return
        self.bubble.show_text(text, duration_ms)
        self._place_bubble()
        if not self.native:
            QTimer.singleShot(150, lambda: x11hints.make_companion(int(self.bubble.winId()), self.settings["desktop_layer"]))

    # -------------------------------------------------------------- movement
    def walk_to(self, x: float, run: bool = False) -> None:
        if self.native and not self.settings["roam"]:
            return
        lo, hi = self._x_bounds()
        self.target_x = max(lo, min(hi, x))
        if abs(self.target_x - self.rx) < 4:
            return
        self.flip = self.target_x < self.rx
        self.activity = "run" if run else "walk"
        self.sprites.drop_cache(keep={"idle", "walking", "running", "talking", "thinking"})
        self._state_changed()
        self._last_tick = time.monotonic()
        self._walk_started = self._last_tick
        self.move_timer.start()

    def _stop_moving(self) -> None:
        self.move_timer.stop()
        if self.activity in ("walk", "run", "fall"):
            self.activity = "idle"
        self.flip = False

    def _move_tick(self) -> None:
        now = time.monotonic()
        dt = min(0.1, now - self._last_tick)
        self._last_tick = now
        if self.activity == "fall":
            self.vy += 2200 * dt
            self.ry += self.vy * dt
            floor = self._floor_y()
            if self.ry >= floor:
                self.ry = floor
                self.activity = "idle"
                self.move_timer.stop()
                self.react("confused" if self.vy > 900 else "happy", 1.4)
                self.save_position()
            self._apply_pos()
            return
        if self.activity not in ("walk", "run"):
            self.move_timer.stop()
            return
        speed = (190.0 if self.activity == "run" else 62.0) * self.size_px / 140
        d = self.target_x - self.rx
        ease_in = min(1.0, (now - self._walk_started) / 0.45)          # accelerate
        ease_out = min(1.0, abs(d) / (40.0 * self.size_px / 140) + 0.25)  # decelerate
        step = speed * dt * min(ease_in, ease_out) * (0.6 + 0.4 * ease_in)
        if abs(d) <= step:
            self.rx = self.target_x
            self._stop_moving()
            self.next_decision = now + self.rng.uniform(4, 12)
            self._state_changed()
            self.save_position()
        else:
            self.rx += step if d > 0 else -step
        self._apply_pos()

    # ------------------------------------------------------------- behaviour
    def _brain(self) -> None:
        now = time.monotonic()
        if not self.user_hidden and not self.auto_hidden and (not self.isVisible() or self.isMinimized()):
            self._restore_visible()
        if self.settings["paused"] or not self.isVisible():
            return
        if self.temp and now >= self.temp_until:
            self.temp = None
            self._state_changed()
        if self.activity != "idle" or self.hold or self.temp or self._dragging:
            return
        quiet = bool(self.settings["quiet_mode"])
        if (self.settings["chatter"] and not quiet and now >= self.next_chatter):
            self.next_chatter = now + self.rng.uniform(420, 900)
            self.wants_chatter.emit()
            return
        if now < self.next_decision:
            return
        act = choose_activity(self.rng, bool(self.settings["roam"]), quiet)
        self.next_decision = now + self.rng.uniform(5, 14)
        lo, hi = self._x_bounds()
        if act in ("walk", "run") and hi > lo:
            span = (hi - lo) * (0.5 if act == "walk" else 0.8)
            tx = self.rx + self.rng.uniform(-span, span)
            self.walk_to(tx, run=act == "run")
        elif act == "wave":
            self.react("waving", 2.0)
        elif act == "think":
            self.react("thinking", 3.0)
        elif act == "happy":
            self.react("happy", 2.0)

    def _micro(self) -> None:
        """Small lifelike behaviours: random blinks and glances while idle."""
        if self.settings["paused"] or not self.isVisible() or not self.settings["animations_enabled"]:
            return
        now = time.monotonic()
        if self.current_state() not in (State.IDLE,) or self._dragging:
            return
        if now >= self.next_blink:
            # occasional double-blink, like people do
            self.next_blink = now + (0.35 if self.rng.random() < 0.18 else self.rng.uniform(2.2, 6.5))
            self.play_state(State.BLINK, 3 / 14)
        elif now >= self.next_glance:
            self.next_glance = now + self.rng.uniform(18, 45)
            self.play_state(State.GLANCE, 2.0)

    def set_audio_level(self, level: float) -> None:
        """Live speech loudness (0..1) for lip-sync; negative = speech ended."""
        self.audio_level = None if level < 0 else level
        if self.current_state() == State.TALKING:
            self._next_frame()

    def _check_idle(self) -> None:
        ms = self.idle.idle_ms()
        if ms is None or self.settings["paused"]:
            return
        limit = float(self.settings["sleep_after_min"]) * 60_000
        if self.settings["quiet_mode"]:
            limit /= 2
        if self.activity == "idle" and not self.hold and ms > limit:
            self.slept_from_idle = True
            self.sleep()
        elif self.activity == "sleep" and self.slept_from_idle and ms < 3000:
            self.slept_from_idle = False
            self.wake(greet=True)

    def _next_frame(self) -> None:
        st = self.current_state()
        n = len(self.sprites.frames(st, self.flip))
        if st == State.TALKING:
            if self.audio_level is not None:
                # fast attack, slower release looks like real speech
                target = self.audio_level
                self._mouth = target if target > self._mouth else self._mouth * 0.55 + target * 0.45
            else:
                self._mouth = self.rng.choice((0.2, 0.5, 0.8, 1.0, 0.35, 0.65))
            self.frame = max(0, min(n - 1, round(self._mouth * (n - 1))))
            self.update()
            return
        self.frame = (self.frame + 1) % max(1, n)
        expected = int(1000 / max(1.0, self.sprites.fps(self.current_state())))
        if self.frame_timer.interval() != expected:
            self.frame_timer.setInterval(expected)
        self.update()

    # ---------------------------------------------------------------- input
    def _robot_hit(self, local: QPoint) -> bool:
        if not self.native:
            return True
        return QRect(int(self.rx), int(self.ry), self.size_px, self.size_px).contains(local)

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.LeftButton and self._robot_hit(e.position().toPoint()):
            self._press = e.globalPosition().toPoint()
            self._press_offset = self._press - QPoint(int(self.rx), int(self.ry))
            self._dragging = False
        elif e.button() == Qt.RightButton:
            self.menu_requested.emit(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e):  # noqa: N802
        if self._press is None:
            return
        g = e.globalPosition().toPoint()
        if not self._dragging and (g - self._press).manhattanLength() > 6:
            self._dragging = True
            self._stop_moving()
            if self.activity == "sleep":
                self.activity = "idle"
            if self.native:
                self.activity = "drag"
                self._state_changed()
                # Wayland: only the compositor may move windows
                handle = self.windowHandle()
                self._press = None
                self._dragging = False
                if handle:
                    handle.startSystemMove()
                QTimer.singleShot(400, self._end_native_drag)
                return
            self.activity = "drag"
            self._state_changed()
        if self._dragging and not self.native:
            p = g - self._press_offset
            self.rx, self.ry = float(p.x()), float(p.y())
            self._apply_pos()

    def _end_native_drag(self) -> None:
        if self.activity == "drag":
            self.activity = "idle"
            self._state_changed()

    def mouseReleaseEvent(self, e):  # noqa: N802
        if e.button() != Qt.LeftButton or self._press is None:
            return
        was_drag = self._dragging
        self._press = None
        self._dragging = False
        if was_drag:
            self.activity = "idle"
            if self.settings["roam_area"] == "bottom" and self.ry < self._floor_y() - 2:
                self.activity = "fall"
                self.vy = 0.0
                self._last_tick = time.monotonic()
                self.move_timer.start()
            else:
                self.save_position()
            self._state_changed()
        else:
            self.clicked.emit()

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        if e.button() == Qt.LeftButton:
            self.double_clicked.emit()

    def contextMenuEvent(self, e):  # noqa: N802
        self.menu_requested.emit(e.globalPos())

    # -------------------------------------------------------------- painting
    def paintEvent(self, _):  # noqa: N802
        frames = self.sprites.frames(self.current_state(), self.flip)
        if not frames:
            return
        p = QPainter(self)
        pm = frames[self.frame % len(frames)]
        if self.native:
            p.drawPixmap(int(self.rx), int(self.ry), pm)
        else:
            p.drawPixmap(0, 0, pm)
        p.end()

    # ------------------------------------------------------------- settings
    def _on_setting(self, key: str, value) -> None:
        if key == "character_size":
            self.size_px = int(value)
            self.sprites.set_size(self.size_px)
            self._layout()
            self.ry = self._floor_y() if self.settings["roam_area"] == "bottom" else self.ry
            self._apply_pos()
            self.update()
        elif key in ("animations_enabled", "paused"):
            if key == "paused" and value:
                self._stop_moving()
            self._state_changed()
        elif key == "roam_area" and value == "bottom":
            self.ry = self._floor_y()
            self._apply_pos()
        elif key == "desktop_layer" and not self.native and self.isVisible():
            if value:
                x11hints.make_companion(int(self.winId()), True)
            else:
                # re-create the window so Qt applies its normal window type again
                self.hide()
                self.show()
                QTimer.singleShot(200, lambda: x11hints.make_companion(int(self.winId()), False))
        elif key == "always_on_top":
            vis = self.isVisible()
            self.setWindowFlag(Qt.WindowStaysOnTopHint, bool(value))
            if vis:
                self.show()
                if not self.native and value:
                    QTimer.singleShot(200, lambda: x11hints.make_companion(int(self.winId()), self.settings["desktop_layer"]))

    def go_home(self) -> None:
        """Move back to the default spot (bottom-right) — handy after monitor changes."""
        if self.native:
            return
        a = QGuiApplication.primaryScreen().availableGeometry()
        self.rx = float(a.right() - self.size_px - 120)
        self.ry = self._floor_y()
        self._apply_pos()
        self.save_position()

    def stop_timers(self) -> None:
        for t in (self.frame_timer, self.move_timer, self.brain_timer, self.idle_timer, self.micro_timer):
            t.stop()

    def changeEvent(self, e):  # noqa: N802
        # Something (e.g. "Show Desktop" / minimise-all) minimised us: come back.
        from PySide6.QtCore import QEvent
        if e.type() == QEvent.WindowStateChange and self.windowState() & Qt.WindowMinimized \
                and not self.user_hidden and not self.auto_hidden:
            QTimer.singleShot(150, self._restore_visible)
        super().changeEvent(e)

    def step_aside(self, on: bool) -> None:
        """Fade out for full-screen apps / video; fade back in afterwards."""
        from PySide6.QtCore import QPropertyAnimation
        if on == self.auto_hidden:
            return
        self.auto_hidden = on
        if self.user_hidden:
            return
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(450)
        if on:
            self.bubble.hide()
            self._stop_moving()
            self._fade.setStartValue(1.0)
            self._fade.setEndValue(0.0)
            self._fade.finished.connect(lambda: self.hide() if self.auto_hidden else None)
        else:
            self.setWindowOpacity(0.0)
            self.show()
            if not self.native:
                QTimer.singleShot(150, lambda: x11hints.make_companion(int(self.winId()), self.settings["desktop_layer"]))
            self._apply_pos()
            self._fade.setStartValue(0.0)
            self._fade.setEndValue(1.0)
        self._fade.start()

    def _restore_visible(self) -> None:
        if self.user_hidden or self.auto_hidden:
            return
        self.setWindowState(Qt.WindowNoState)
        self.show()
        if not self.native:
            x11hints.make_companion(int(self.winId()), self.settings["desktop_layer"])
        self._apply_pos()

    def hide_all(self) -> None:
        self.user_hidden = True
        self.bubble.hide()
        self._stop_moving()
        self.hide()

    def show_all(self) -> None:
        self.user_hidden = False
        self.auto_hidden = False
        self.setWindowOpacity(1.0)
        self.show()
        if not self.native:
            QTimer.singleShot(200, lambda: x11hints.make_companion(int(self.winId()), self.settings["desktop_layer"]))
        self._apply_pos()
        self._state_changed()

    def cursor_near(self) -> bool:
        return self.robot_rect_global().adjusted(-40, -40, 40, 40).contains(QCursor.pos())
