"""Chat window: history with timestamps, streaming replies, typing indicator,
inline confirmation cards, push-to-talk and conversation controls."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import theme
from ..core import brand


def _ts(t: float) -> str:
    return time.strftime("%H:%M" if time.time() - t < 86400 else "%d %b %H:%M", time.localtime(t))


class MessageBubble(QFrame):
    def __init__(self, role: str, text: str, ts: float, mood: str = ""):
        super().__init__()
        self.setObjectName("msgUser" if role == "user" else "msgBot")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 6)
        lay.setSpacing(2)
        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.MarkdownText)
        self.body.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        self.body.setOpenExternalLinks(True)
        self.body.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.meta = QLabel(("You" if role == "user" else f"{brand.NAME}") + " · " + _ts(ts))
        self.meta.setObjectName("status")
        self.meta.setAlignment(Qt.AlignRight if role == "user" else Qt.AlignLeft)
        lay.addWidget(self.body)
        lay.addWidget(self.meta)
        self.raw = ""
        self.set_text(text)

    def set_text(self, text: str) -> None:
        self.raw = text
        # keep newlines in plain answers when rendering markdown
        md = text if "```" in text else text.replace("\n", "  \n")
        self.body.setText(md)

    def append(self, tok: str) -> None:
        self.set_text(self.raw + tok)


class ConfirmCard(QFrame):
    decided = Signal(int, bool)

    def __init__(self, req_id: int, explanation: str):
        super().__init__()
        self.req_id = req_id
        self.setObjectName("confirm")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        head = QLabel("⚠️  Permission needed")
        head.setStyleSheet(f"color:{theme.ORANGE}; font-weight:700;")
        body = QLabel(explanation)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row = QHBoxLayout()
        self.yes = QPushButton("Allow once")
        self.yes.setObjectName("accent")
        self.no = QPushButton("Don't allow")
        row.addStretch(1)
        row.addWidget(self.no)
        row.addWidget(self.yes)
        lay.addWidget(head)
        lay.addWidget(body)
        lay.addLayout(row)
        self.yes.clicked.connect(lambda: self._decide(True))
        self.no.clicked.connect(lambda: self._decide(False))

    def _decide(self, ok: bool) -> None:
        self.yes.setEnabled(False)
        self.no.setEnabled(False)
        self.yes.setText("Allowed ✓" if ok else "Allow once")
        self.no.setText("Declined" if not ok else "Don't allow")
        self.decided.emit(self.req_id, ok)


class ChatWindow(QWidget):
    send = Signal(str)
    talk = Signal(bool)          # push-to-talk toggled
    stop = Signal()
    clear = Signal()
    open_settings = Signal()
    confirm = Signal(int, bool)

    def __init__(self, icon_path: str):
        super().__init__()
        self.setObjectName("root")
        self.setWindowTitle(f"{brand.NAME} — Chat")
        self.setWindowIcon(QIcon(icon_path))
        self.resize(440, 640)
        self.setMinimumSize(340, 420)
        self._stream: MessageBubble | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        # header
        head = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(QPixmap(icon_path).scaled(40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        titles = QVBoxLayout()
        t = QLabel(f"{brand.NAME}")
        t.setObjectName("title")
        self.status = QLabel("Checking AI…")
        self.status.setObjectName("status")
        titles.addWidget(t)
        titles.addWidget(self.status)
        head.addWidget(logo)
        head.addLayout(titles, 1)
        self.btn_clear = QPushButton("Clear")
        self.btn_clear.setToolTip("Clear conversation")
        self.btn_settings = QPushButton("⚙")
        self.btn_settings.setObjectName("icon")
        self.btn_settings.setToolTip("Settings")
        head.addWidget(self.btn_clear)
        head.addWidget(self.btn_settings)
        root.addLayout(head)

        # messages
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.inner = QWidget()
        self.msgs = QVBoxLayout(self.inner)
        self.msgs.setContentsMargins(2, 2, 6, 2)
        self.msgs.setSpacing(8)
        self.msgs.addStretch(1)
        self.scroll.setWidget(self.inner)
        root.addWidget(self.scroll, 1)

        self.typing = QLabel("")
        self.typing.setObjectName("status")
        root.addWidget(self.typing)
        self._dots = 0
        self._typing_timer = QTimer(self, interval=400, timeout=self._animate_typing)
        self._typing_text = ""

        # input row
        row = QHBoxLayout()
        self.mic = QPushButton("🎤")
        self.mic.setObjectName("icon")
        self.mic.setCheckable(True)
        self.mic.setToolTip("Push to talk (click, speak, pause)")
        self.input = QLineEdit()
        self.input.setPlaceholderText(f"Ask {brand.NAME} anything… (try 'help')")
        self.btn_stop = QPushButton("■")
        self.btn_stop.setObjectName("icon")
        self.btn_stop.setToolTip("Stop reply / speech")
        self.btn_send = QPushButton("Send")
        self.btn_send.setObjectName("primary")
        row.addWidget(self.mic)
        row.addWidget(self.input, 1)
        row.addWidget(self.btn_stop)
        row.addWidget(self.btn_send)
        root.addLayout(row)

        self.btn_send.clicked.connect(self._send)
        self.input.returnPressed.connect(self._send)
        self.mic.toggled.connect(self.talk)
        self.btn_stop.clicked.connect(self.stop)
        self.btn_clear.clicked.connect(self.clear)
        self.btn_settings.clicked.connect(self.open_settings)
        QShortcut(QKeySequence("Escape"), self, activated=self.hide)

    # ---------------------------------------------------------------- input
    def _send(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        self.add_message("user", text)
        self.send.emit(text)

    def set_listening(self, on: bool) -> None:
        self.mic.blockSignals(True)
        self.mic.setChecked(on)
        self.mic.blockSignals(False)
        if on:
            self.set_typing("Listening… speak now")
        elif self._typing_text.startswith("Listening"):
            self.set_typing("")

    def show_partial(self, text: str) -> None:
        self.input.setText(text)

    # ------------------------------------------------------------- messages
    def add_message(self, role: str, text: str, ts: float | None = None) -> MessageBubble:
        b = MessageBubble(role, text, ts or time.time())
        self._insert(b, right=(role == "user"))
        return b

    def _insert(self, w: QWidget, right: bool = False) -> None:
        wrap = QHBoxLayout()
        if right:
            wrap.addStretch(1)
        wrap.addWidget(w, 6)
        if not right:
            wrap.addStretch(1)
        holder = QWidget()
        holder.setLayout(wrap)
        wrap.setContentsMargins(0, 0, 0, 0)
        self.msgs.insertWidget(self.msgs.count() - 1, holder)
        QTimer.singleShot(30, self._scroll_bottom)

    def _scroll_bottom(self) -> None:
        sb = self.scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    def load_history(self, messages: list[dict]) -> None:
        self.clear_view()
        for m in messages:
            if m["role"] in ("user", "assistant"):
                self.add_message(m["role"], m["content"], m["ts"])

    def clear_view(self) -> None:
        while self.msgs.count() > 1:
            item = self.msgs.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._stream = None

    def begin_stream(self) -> None:
        self._stream = None
        self.set_typing(f"{brand.NAME} is thinking")

    def stream_token(self, tok: str) -> None:
        if self._stream is None:
            self._stream = self.add_message("assistant", "")
            self.set_typing(f"{brand.NAME} is typing")
        self._stream.append(tok)
        sb = self.scroll.verticalScrollBar()
        if sb.maximum() - sb.value() < 120:
            QTimer.singleShot(0, self._scroll_bottom)

    def end_stream(self, final_text: str) -> None:
        self.set_typing("")
        if self._stream is not None:
            if final_text:
                self._stream.set_text(final_text)
            elif not self._stream.raw.strip():
                self._stream.parentWidget().deleteLater()
        elif final_text:
            self.add_message("assistant", final_text)
        self._stream = None

    def add_confirm(self, req_id: int, explanation: str) -> None:
        card = ConfirmCard(req_id, explanation)
        card.decided.connect(self.confirm)
        self._insert(card)

    # --------------------------------------------------------------- status
    def set_typing(self, text: str) -> None:
        self._typing_text = text
        if text:
            self._typing_timer.start()
            self._animate_typing()
        else:
            self._typing_timer.stop()
            self.typing.setText("")

    def _animate_typing(self) -> None:
        self._dots = (self._dots + 1) % 4
        self.typing.setText(self._typing_text + "." * self._dots)

    def set_ai_status(self, ok: bool, detail: str) -> None:
        color = theme.GREEN if ok else theme.ORANGE
        self.status.setText(f"<span style='color:{color}'>●</span> {detail}")
        self.status.setToolTip(detail)

    def present(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()
