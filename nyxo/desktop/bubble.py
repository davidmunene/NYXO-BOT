"""Comic-style speech bubble. Works either as its own transparent top-level
window (XWayland mode) or as a child widget of the companion window
(native Wayland mode, where windows can't position themselves)."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget
from ..core import brand

MAX_W = 300
PAD = 12
TAIL = 12


class SpeechBubble(QWidget):
    clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.toplevel = parent is None
        if self.toplevel:
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool |
                                Qt.WindowDoesNotAcceptFocus)
            self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle(f"{brand.NAME} bubble")
        self.text = ""
        self.tail_x = 0.5              # tail position as fraction of width
        self.font_ = QFont()
        self.font_.setPointSizeF(10.5)
        self._hide = QTimer(self, singleShot=True, timeout=self.hide)
        self.hide()

    def show_text(self, text: str, duration_ms: int | None = None) -> None:
        text = text.strip()
        if len(text) > 280:
            text = text[:277].rsplit(" ", 1)[0] + "…"
        self.text = text
        fm = QFontMetrics(self.font_)
        br = fm.boundingRect(0, 0, MAX_W - 2 * PAD, 2000, Qt.TextWordWrap, text)
        w = max(80, br.width() + 2 * PAD + 2)
        h = br.height() + 2 * PAD + TAIL
        self.resize(w, h)
        if duration_ms is None:
            duration_ms = min(14000, 2500 + 55 * len(text))
        self._hide.start(duration_ms)
        self.show()
        if not self.toplevel:
            self.raise_()          # child widget: stack above the robot
        self.update()

    def mousePressEvent(self, e):  # noqa: N802
        self.hide()
        self.clicked.emit()

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(1, 1, self.width() - 2, self.height() - TAIL - 2)
        path = QPainterPath()
        path.addRoundedRect(r, 14, 14)
        tx = max(22.0, min(self.width() - 22.0, self.width() * self.tail_x))
        tail = QPainterPath()
        tail.moveTo(tx - 9, r.bottom() - 1)
        tail.lineTo(tx + 2, self.height() - 2)
        tail.lineTo(tx + 9, r.bottom() - 1)
        tail.closeSubpath()
        path = path.united(tail)
        p.setPen(QPen(QColor("#ff8a1f"), 2))
        p.setBrush(QColor(13, 27, 46, 240))
        p.drawPath(path)
        p.setPen(QColor("#e8f7ff"))
        p.setFont(self.font_)
        p.drawText(r.adjusted(PAD, PAD - 2, -PAD, -PAD + 2), Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignVCenter,
                   self.text)
        p.end()

    def anchor_point(self) -> QPointF:
        return QPointF(self.width() * self.tail_x, self.height())
