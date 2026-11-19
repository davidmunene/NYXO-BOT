"""Settings window: sidebar navigation + one page per section. Every control
is live-bound to the Settings store (changes apply immediately)."""
from __future__ import annotations

import shlex
import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..ai import credentials
from ..ai.cloud import ANTHROPIC_MODELS
from ..ai.manager import PROVIDERS
from ..ai.ollama import RECOMMENDED
from ..character.states import ANIMATIONS, State
from ..core import paths
from ..desktop import shortcut
from ..security import policy
from ..startup import manager as startup
from ..voice import stt, tts, wakeword, whisper_stt
from ..ui import theme
from ..core import brand

SECTIONS = ["General", "Character", "Personality", "Animation", "AI providers", "Voice",
            "Memory", "Notifications", "Startup", "Privacy & security", f"About {brand.NAME}"]
ICONS = ["⚙", "🤖", "💬", "✨", "🧠", "🎙", "🗂", "🔔", "🚀", "🔒", "ℹ"]


class _Bridge(QObject):
    """Deliver results from worker threads back to the GUI thread."""
    done = Signal(object, object)


def _card(title: str, subtitle: str = "") -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setObjectName("card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(16, 12, 16, 14)
    lay.setSpacing(8)
    h = QLabel(title)
    h.setObjectName("h2")
    lay.addWidget(h)
    if subtitle:
        s = QLabel(subtitle)
        s.setObjectName("muted")
        s.setWordWrap(True)
        lay.addWidget(s)
    return f, lay


def _muted(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("muted")
    lab.setWordWrap(True)
    lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lab


class SettingsDialog(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.s = app.settings
        self.setObjectName("root")
        self.setWindowTitle(f"{brand.NAME} — Settings")
        self.setWindowIcon(QIcon(str(app.icon_path)))
        self.resize(860, 640)
        self.setMinimumSize(640, 480)
        self._bridge = _Bridge()
        self._bridge.done.connect(lambda cb, val: cb(val))

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(205)
        self.stack = QStackedWidget()
        lay.addWidget(self.nav)
        lay.addWidget(self.stack, 1)
        builders = [self._general, self._character, self._personality, self._animation, self._ai,
                    self._voice, self._memory, self._notifications, self._startup, self._privacy,
                    self._about]
        for name, icon, build in zip(SECTIONS, ICONS, builders):
            self.nav.addItem(QListWidgetItem(f"{icon}   {name}"))
            page = QWidget()
            pl = QVBoxLayout(page)
            pl.setContentsMargins(22, 18, 22, 18)
            pl.setSpacing(14)
            t = QLabel(name)
            t.setObjectName("title")
            pl.addWidget(t)
            build(pl)
            pl.addStretch(1)
            sc = QScrollArea()
            sc.setWidgetResizable(True)
            sc.setWidget(page)
            self.stack.addWidget(sc)
        self.nav.currentRowChanged.connect(self._on_page)
        self.nav.setCurrentRow(0)

    def _on_page(self, i: int) -> None:
        self.stack.setCurrentIndex(i)
        refresh = {6: self._refresh_memories, 7: self._refresh_reminders, 8: self._refresh_startup,
                   9: self._refresh_privacy, 4: self._refresh_ai}
        if i in refresh:
            refresh[i]()

    def show_section(self, name: str) -> None:
        if name in SECTIONS:
            self.nav.setCurrentRow(SECTIONS.index(name))
        self.show()
        self.raise_()
        self.activateWindow()

    def _bg(self, fn, cb) -> None:
        def run():
            try:
                val = fn()
            except Exception as e:  # noqa: BLE001
                val = e
            self._bridge.done.emit(cb, val)
        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------ binders
    def _check(self, key: str, label: str, tip: str = "") -> QCheckBox:
        cb = QCheckBox(label)
        cb.setChecked(bool(self.s[key]))
        cb.setToolTip(tip)
        cb.toggled.connect(lambda v: self.s.set(key, bool(v)))
        self.s.changed.connect(lambda k, v: cb.setChecked(bool(v)) if k == key and cb.isChecked() != bool(v) else None)
        return cb

    def _combo(self, key: str, options: dict[str, str]) -> QComboBox:
        c = QComboBox()
        for val, text in options.items():
            c.addItem(text, val)
        idx = c.findData(self.s[key])
        c.setCurrentIndex(max(0, idx))
        c.currentIndexChanged.connect(lambda _: self.s.set(key, c.currentData()))
        return c

    def _line(self, key: str, placeholder: str = "") -> QLineEdit:
        e = QLineEdit(str(self.s[key] or ""))
        e.setPlaceholderText(placeholder)
        e.editingFinished.connect(lambda: self.s.set(key, e.text().strip()))
        return e

    def _spin(self, key: str, lo: int, hi: int, suffix: str = "") -> QSpinBox:
        sp = QSpinBox()
        sp.setRange(lo, hi)
        sp.setValue(int(self.s[key]))
        sp.setSuffix(suffix)
        sp.valueChanged.connect(lambda v: self.s.set(key, int(v)))
        return sp

    # -------------------------------------------------------------- pages
    def _general(self, pl: QVBoxLayout) -> None:
        card, c = _card("You")
        form = QFormLayout()
        form.addRow("Your name", self._line("user_name", f"What should {brand.NAME} call you?"))
        form.addRow("Assistant's name", self._line("assistant_name", "Kings"))
        repo_row = QHBoxLayout()
        repo = self._line("dev_repo", "~/code/my-project")
        browse = QPushButton("Browse…")

        def pick():
            d = QFileDialog.getExistingDirectory(self, "Default git repository", str(Path.home()))
            if d:
                repo.setText(d)
                self.s.set("dev_repo", d)
        browse.clicked.connect(pick)
        repo_row.addWidget(repo, 1)
        repo_row.addWidget(browse)
        form.addRow("Default git repo", repo_row)
        c.addLayout(form)
        pl.addWidget(card)

        card, c = _card("Companion", f"Pause or hide {brand.NAME}, or switch on focus mode while you work.")
        c.addWidget(self._check("paused", f"Pause {brand.NAME} (freeze animations and wandering)"))
        c.addWidget(self._check("quiet_mode", "Focus mode — stay still, no idle chatter, brief answers"))
        c.addWidget(self._check("always_on_top", f"Keep {brand.NAME} above other windows"))
        c.addWidget(self._check("desktop_layer", "Stay visible on the empty desktop, during “Show Desktop” "
                                "and on every workspace"))
        row = QHBoxLayout()
        hide = QPushButton(f"Hide {brand.NAME}")
        hide.clicked.connect(self.app.hide_companion)
        home = QPushButton("Reset position")
        home.clicked.connect(self.app.companion.go_home)
        row.addWidget(hide)
        row.addWidget(home)
        row.addStretch(1)
        c.addLayout(row)
        pl.addWidget(card)

        card, c = _card("Display mode", "GNOME on Wayland doesn't let normal Wayland windows choose "
                        f"their position or stay on top. XWayland mode runs only {brand.NAME}'s windows through "
                        f"GNOME's X11 compatibility layer so {brand.NAME} can walk across the screen. Native "
                        f"Wayland mode keeps {brand.NAME} in a fixed transparent area you place by dragging.")
        c.addWidget(self._combo("display_mode", {"xwayland": "XWayland — free roaming (recommended)",
                                                 "wayland": "Native Wayland — fixed area"}))
        c.addWidget(_muted(f"Currently running in: {self.app.mode_label}. Changing this restarts NYXO."))
        restart = QPushButton(f"Restart {brand.NAME} now")
        restart.clicked.connect(self.app.restart)
        c.addWidget(restart, 0, Qt.AlignLeft)
        pl.addWidget(card)

    def _character(self, pl: QVBoxLayout) -> None:
        card, c = _card("Avatar", "Choose how your assistant looks. The portrait avatar is built from "
                        "your character sheet image and kept privately in your data folder.")
        c.addWidget(self._combo("avatar", {"kings": "Kings (portrait from your image)",
                                           "robot": "Robot (built-in)"}))
        arow = QHBoxLayout()
        rebuild = QPushButton("Rebuild portrait from image…")
        rebuild.clicked.connect(self._rebuild_avatar)
        restart = QPushButton("Restart to apply")
        restart.clicked.connect(self.app.restart)
        arow.addWidget(rebuild)
        arow.addWidget(restart)
        arow.addStretch(1)
        c.addLayout(arow)
        self.avatar_status = _muted("Changes apply after a restart.")
        c.addWidget(self.avatar_status)
        pl.addWidget(card)

        card, c = _card("Size", f"How big {brand.NAME} is on your desktop.")
        row = QHBoxLayout()
        sl = QSlider(Qt.Horizontal)
        sl.setRange(80, 260)
        sl.setValue(int(self.s["character_size"]))
        val = QLabel(f"{sl.value()} px")
        sl.valueChanged.connect(lambda v: val.setText(f"{v} px"))
        sl.sliderReleased.connect(lambda: self.s.set("character_size", sl.value()))
        sl.valueChanged.connect(lambda v: None if sl.isSliderDown() else self.s.set("character_size", v))
        row.addWidget(sl, 1)
        row.addWidget(val)
        c.addLayout(row)
        pl.addWidget(card)

        card, c = _card("Preview", f"Play any of {brand.NAME}'s animation states.")
        prev = QLabel()
        prev.setFixedSize(180, 180)
        prev.setAlignment(Qt.AlignCenter)
        prev.setStyleSheet(f"background:{theme.NAVY_3}; border-radius:14px;")
        combo = QComboBox()
        for st in ANIMATIONS:
            combo.addItem(st.value.capitalize(), st.value)
        bank = self.app.preview_bank
        state = {"i": 0}

        def tick():
            frames = bank.frames(State(combo.currentData()))
            if frames:
                state["i"] = (state["i"] + 1) % len(frames)
                prev.setPixmap(frames[state["i"]])
        self._prev_timer = QTimer(self, timeout=tick)
        combo.currentIndexChanged.connect(lambda _: (state.update(i=0),
                                                     self._prev_timer.setInterval(int(1000 / bank.fps(State(combo.currentData()))))))
        self._prev_timer.start(125)
        play = QPushButton("Show on desktop")
        play.clicked.connect(lambda: self.app.companion.play_state(State(combo.currentData()), 4))
        h = QHBoxLayout()
        h.addWidget(prev)
        v = QVBoxLayout()
        v.addWidget(combo)
        v.addWidget(play)
        v.addWidget(_muted(f"{brand.NAME}: compact white robot, orange accents, cyan LED eyes. "
                           "16 hand-built animation states rendered to transparent PNG spritesheets."))
        v.addStretch(1)
        h.addLayout(v, 1)
        c.addLayout(h)
        pl.addWidget(card)

    def _rebuild_avatar(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Character sheet image", str(Path.home() / "Downloads"),
                                           "Images (*.png *.jpg *.jpeg *.webp)")
        if not f:
            return
        self.avatar_status.setText("Building avatar… (about 15 seconds)")

        def work():
            from ..character import portrait
            portrait.build(Path(f), paths.avatar_dir("kings"), log=lambda *_: None)
            return True

        def done(res):
            if isinstance(res, Exception):
                self.avatar_status.setText(f"Couldn't build the avatar: {res}. The image must use the "
                                           "same layout as the original sheet.")
            else:
                self.s.set("avatar", "kings")
                self.avatar_status.setText("Done — press “Restart to apply”.")
        self._bg(work, done)

    def _personality(self, pl: QVBoxLayout) -> None:
        card, c = _card("Personality", f"How {brand.NAME} talks to you.")
        form = QFormLayout()
        form.addRow("Style", self._combo("personality", {
            "jarvis": "JARVIS — composed, witty AI butler",
            "playful": "Playful & funny", "sarcastic": "Slightly sarcastic",
            "encouraging": "Encouraging buddy", "professional": "Professional"}))
        form.addRow("Answer length", self._combo("verbosity", {
            "short": "Short", "normal": "Normal", "detailed": "Detailed"}))
        c.addLayout(form)
        c.addWidget(self._check("chatter", "Occasional idle chatter (hydration reminders, jokes)"))
        c.addWidget(self._check("briefing_on_start", "Spoken status briefing when I log in"))
        c.addWidget(self._check("system_alerts", "Proactive alerts (battery, disk space, network, CPU)"))
        test = QPushButton("Say hello")
        from ..assistant import personality
        test.clicked.connect(lambda: self.app.say(personality.greeting(self.s["personality"], self.s["user_name"]), "waving"))
        c.addWidget(test, 0, Qt.AlignLeft)
        pl.addWidget(card)

    def _animation(self, pl: QVBoxLayout) -> None:
        card, c = _card("Animation & movement")
        c.addWidget(self._check("animations_enabled", f"Animate {brand.NAME} (turn off to save a little CPU)"))
        c.addWidget(self._check("roam", f"Let {brand.NAME} wander around"))
        c.addWidget(self._check("auto_hide_fullscreen", "Step aside while a full-screen app is active (videos, games)"))
        c.addWidget(self._check("auto_hide_media", "Step aside while video or a presentation keeps the screen awake"))
        helper = "active" if self.app.presence.helper_available() else \
            "installed — becomes active after your next login" if (Path.home() / ".local/share/gnome-shell/extensions/nyxo-helper@nyxo.io").exists() \
            else "not installed (run scripts/install.sh)"
        c.addWidget(_muted(f"Full-screen detection uses the small “NYXO Helper” GNOME extension ({helper}); "
                           "it shares only a yes/no flag."))
        form = QFormLayout()
        form.addRow(f"Where {brand.NAME} lives", self._combo("roam_area", {
            "bottom": "Walks along the bottom of the screen", "screen": "Stays wherever I drop it"}))
        form.addRow("Nap after I'm idle for", self._spin("sleep_after_min", 1, 120, " min"))
        c.addLayout(form)
        c.addWidget(_muted(f"Idle time comes from GNOME's idle monitor — {brand.NAME} never reads your keyboard or mouse."))
        pl.addWidget(card)

    # ------------------------------------------------------------------ AI
    def _ai(self, pl: QVBoxLayout) -> None:
        card, c = _card("Provider", f"{brand.NAME} works without AI (commands, files, system info, tasks). "
                        "Add a model for conversation and coding help.")
        self.ai_combo = self._combo("ai_provider", PROVIDERS)
        c.addWidget(self.ai_combo)
        self.ai_status = _muted("…")
        c.addWidget(self.ai_status)
        row = QHBoxLayout()
        test = QPushButton("Test AI")
        test.clicked.connect(self._test_ai)
        row.addWidget(test)
        row.addStretch(1)
        c.addLayout(row)
        form = QFormLayout()
        temp = QDoubleSpinBox()
        temp.setRange(0, 1.5)
        temp.setSingleStep(0.1)
        temp.setValue(float(self.s["ai_temperature"]))
        temp.valueChanged.connect(lambda v: self.s.set("ai_temperature", round(v, 2)))
        form.addRow("Creativity (temperature)", temp)
        form.addRow("Messages of context", self._spin("context_messages", 2, 40))
        c.addLayout(form)
        c.addWidget(self._check("ai_actions", "Let the AI propose actions (open apps, search files…). "
                                "Anything that changes something still needs your approval."))
        pl.addWidget(card)

        card, c = _card("Ollama — local & private", f"Runs on your CPU. {brand.NAME} starts `ollama serve` only "
                        f"when you first ask something and stops it when {brand.NAME} quits.")
        form = QFormLayout()
        form.addRow("Server URL", self._line("ollama_url"))
        self.ollama_model = QComboBox()
        self.ollama_model.setEditable(True)
        self.ollama_model.setCurrentText(self.s["ollama_model"])
        self.ollama_model.currentTextChanged.connect(lambda t: self.s.set("ollama_model", t.strip()) if t.strip() else None)
        mrow = QHBoxLayout()
        mrow.addWidget(self.ollama_model, 1)
        ref = QPushButton("Refresh")
        ref.clicked.connect(self._refresh_ai)
        mrow.addWidget(ref)
        form.addRow("Model", mrow)
        c.addLayout(form)
        c.addWidget(self._check("ollama_autostart", "Start Ollama automatically when needed"))
        rec = "\n".join(f"• {m} — {d}" for m, d in RECOMMENDED)
        c.addWidget(_muted("Recommended for this laptop (CPU, 14 GB RAM):\n" + rec +
                           "\n\nInstall a model in a terminal:  ollama pull qwen2.5:1.5b\n"
                           "Install Ollama itself:  https://ollama.com/download"))
        pull_row = QHBoxLayout()
        self.pull_btn = QPushButton("Download a model…")
        self.pull_btn.clicked.connect(self._pull_model)
        pull_row.addWidget(self.pull_btn)
        pull_row.addStretch(1)
        c.addLayout(pull_row)
        pl.addWidget(card)

        card, c = _card("Cloud providers", "API keys are stored in ~/.config/nyxo/credentials.env "
                        "(readable only by you) or read from environment variables. They are never "
                        "saved in chat history, settings or logs.")
        form = QFormLayout()
        form.addRow("Anthropic model", self._combo("anthropic_model", {m: m for m in ANTHROPIC_MODELS}))
        form.addRow("Anthropic API key", self._key_row("ANTHROPIC_API_KEY"))
        form.addRow("OpenAI-compatible URL", self._line("openai_base_url"))
        form.addRow("Model", self._line("openai_model"))
        form.addRow("API key", self._key_row("OPENAI_API_KEY"))
        c.addLayout(form)
        pl.addWidget(card)
        self.s.changed.connect(lambda k, v: self._refresh_ai() if k.startswith(("ai_", "ollama", "anthropic", "openai")) else None)

    def _key_row(self, name: str) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        e = QLineEdit()
        e.setEchoMode(QLineEdit.Password)
        cur = credentials.get(name)
        e.setPlaceholderText(f"{credentials.mask(cur)} ({credentials.source(name)})" if cur else "paste key")
        save = QPushButton("Save")

        def do_save():
            try:
                credentials.set_key(name, e.text())
            except ValueError as ex:
                QMessageBox.warning(self, f"{brand.NAME}", str(ex))
                return
            v = credentials.get(name)
            e.clear()
            e.setPlaceholderText(f"{credentials.mask(v)} ({credentials.source(name)})" if v else "paste key")
            self._refresh_ai()
        save.clicked.connect(do_save)
        h.addWidget(e, 1)
        h.addWidget(save)
        return w

    def _refresh_ai(self) -> None:
        self.app.ai.rebuild()

        def work():
            ok, detail = self.app.ai.status()
            models = self.app.ai.ollama.models()
            return ok, detail, models

        def done(res):
            if isinstance(res, Exception):
                self.ai_status.setText(f"Error: {res}")
                return
            ok, detail, models = res
            color = theme.GREEN if ok else theme.ORANGE
            self.ai_status.setText(f"<span style='color:{color}'>●</span> {detail}")
            cur = self.s["ollama_model"]
            self.ollama_model.blockSignals(True)
            self.ollama_model.clear()
            self.ollama_model.addItems(models or [cur])
            self.ollama_model.setCurrentText(cur)
            self.ollama_model.blockSignals(False)
        self._bg(work, done)

    def _test_ai(self) -> None:
        self.ai_status.setText("Asking the AI to say hi…")
        from ..ai.base import ChatMessage

        def work():
            self.app.ai.rebuild()
            p = self.app.ai.provider
            if p is None:
                return "AI is turned off."
            t = time.time()
            reply = p.chat([ChatMessage("user", "Say hi in five words.")], f"You are {brand.NAME}, a friendly robot.")
            return f"✓ {reply.strip()[:120]}  ({time.time() - t:.1f}s)"
        self._bg(work, lambda r: self.ai_status.setText(str(r)))

    def _pull_model(self) -> None:
        names = [m for m, _ in RECOMMENDED]
        name, ok = QInputDialog.getItem(self, "Download model", "Model to download with Ollama "
                                        "(1–2 GB download):", names, 0, True)
        if not ok or not name.strip():
            return
        name = name.strip()
        if QMessageBox.question(self, "Download model",
                                f"Download '{name}' with Ollama? This can be 1–2 GB.") != QMessageBox.Yes:
            return
        self.pull_btn.setEnabled(False)
        self.pull_btn.setText(f"Downloading {name}…")

        def work():
            from ..ai.base import http_json
            if not self.app.ai.ollama.ensure_server():
                raise RuntimeError("Ollama isn't installed or couldn't start.")
            http_json(f"{self.app.ai.ollama.url}/api/pull", {"model": name, "stream": False}, timeout=3600)
            return name

        def done(res):
            self.pull_btn.setEnabled(True)
            self.pull_btn.setText("Download a model…")
            if isinstance(res, Exception):
                QMessageBox.warning(self, f"{brand.NAME}", f"Download failed: {res}")
            else:
                self.s.set("ollama_model", res)
                self._refresh_ai()
                self.app.say(f"Downloaded {res}! My brain feels bigger.", "celebrating")
        self._bg(work, done)

    # --------------------------------------------------------------- voice
    def _voice(self, pl: QVBoxLayout) -> None:
        card, c = _card("Speech output", f"{brand.NAME} speaks with local text-to-speech (no internet). "
                        "“Automatic” picks the most natural voice installed — the Piper neural "
                        "British voice when available, otherwise espeak.")
        form = QFormLayout()
        form.addRow("Speak", self._combo("tts_mode", {
            "always": f"Everything {brand.NAME} says", "voice_only": f"Only when I talk to {brand.NAME}",
            "never": "Never (muted)"}))
        engines = {"auto": "Automatic (best available)"}
        engines.update({e: ("Piper neural voice" if e == "piper" else e) for e in tts.available_engines()})
        form.addRow("Engine", self._combo("tts_engine", engines))
        pv = QComboBox()
        pv.addItem("Automatic (best installed)", "")
        for v in tts.piper_voices():
            pv.addItem(v.stem, str(v))
        pv.setCurrentIndex(max(0, pv.findData(self.s["piper_model"])))
        pv.currentIndexChanged.connect(lambda _: (self.s.set("piper_model", pv.currentData()), self.app.speaker.warm_up()))
        form.addRow("Neural voice", pv)
        voice = QComboBox()
        voice.setEditable(True)
        voice.addItems(["en-gb", "en-us", "en-us+m3", "en-gb+f2", "en-sc"])
        voice.setCurrentText(self.s["tts_voice"])
        voice.currentTextChanged.connect(lambda t: self.s.set("tts_voice", t.strip() or "en-gb"))
        form.addRow("espeak voice", voice)
        rate = QSlider(Qt.Horizontal)
        rate.setRange(90, 280)
        rate.setValue(int(self.s["tts_rate"]))
        rate.valueChanged.connect(lambda v: self.s.set("tts_rate", int(v)))
        form.addRow("Speed", rate)
        c.addLayout(form)
        row = QHBoxLayout()
        b = QPushButton("Test voice")
        b.clicked.connect(lambda: self.app.speaker.speak(f"Good day. {brand.NAME} online. All systems are fully operational."))
        self.voice_label = _muted(f"Current voice: {self.app.speaker.voice_label()}")
        c.addWidget(self.voice_label)
        s = QPushButton("Stop")
        s.clicked.connect(self.app.speaker.stop)
        row.addWidget(b)
        row.addWidget(s)
        row.addStretch(1)
        c.addLayout(row)
        if not tts.piper_voices() or not tts.piper_installed():
            dlv = QPushButton("Download natural British voice (63 MB)")
            dlv.clicked.connect(lambda: self._download_piper(dlv))
            c.addWidget(dlv, 0, Qt.AlignLeft)
        c.addWidget(_muted("More Piper voices: put any .onnx + .onnx.json pair from "
                           "huggingface.co/rhasspy/piper-voices in " f"{paths.models_dir() / 'piper'}."))
        pl.addWidget(card)

        card, c = _card("Speech recognition (push-to-talk)",
                        "Offline recognition with Vosk. The microphone is used only while you hold "
                        f"a conversation turn: click 🎤 in chat, the tray's 'Talk to {brand.NAME}', or the "
                        "shortcut. Audio is never saved.")
        c.addWidget(self._check("stt_enabled", "Enable push-to-talk"))
        ok, detail = stt.status(str(self.s["vosk_model"]))
        self.stt_status = _muted(("✓ " if ok else "⚠ ") + detail)
        c.addWidget(self.stt_status)
        form = QFormLayout()
        form.addRow("Max listening time", self._spin("stt_max_seconds", 3, 60, " s"))
        c.addLayout(form)
        row = QHBoxLayout()
        dl = QPushButton("Download Vosk model (40 MB)")
        dl.setVisible(stt.find_model() is None)
        dl.clicked.connect(lambda: self._download_vosk(dl))
        mic = QPushButton("Test microphone")
        mic.clicked.connect(self._test_mic)
        row.addWidget(dl)
        row.addWidget(mic)
        row.addStretch(1)
        c.addLayout(row)
        pl.addWidget(card)

        card, c = _card(f"Hands-free: just say “{brand.NAME}”",
                        f"Call {brand.NAME} by name, e.g. “{brand.NAME}, what's playing?” or "
                        f"“Hey {brand.NAME}” then your request. While this is on, the microphone is analysed "
                        "continuously on this computer to spot the name; nothing is recorded, stored or "
                        "sent anywhere, and only what you say after the name is transcribed (offline).")
        c.addWidget(self._check("wake_word", f"Listen for my name"))
        form = QFormLayout()
        form.addRow("Wake word", self._combo("wake_engine", {
            "name": f"My name — “{brand.NAME}”", "hey_jarvis": "“Hey Jarvis” (neural model)"}))
        sizes = {"auto": "Automatic (most accurate installed)"}
        sizes.update({sz: f"Whisper {sz}" for sz in whisper_stt.available_sizes()})
        sizes["off"] = "Vosk only (fastest, least accurate)"
        form.addRow("Speech recognition", self._combo("stt_model", sizes))
        c.addLayout(form)
        ok, detail = whisper_stt.status(str(self.s["stt_model"]))
        c.addWidget(_muted(("✓ " if ok else "⚠ ") + detail + " — Vosk spots the name and the end of "
                           "your sentence; Whisper writes down what you said."))
        c.addWidget(_muted("Changes to the wake word apply after toggling the switch off and on."))
        srow = QHBoxLayout()
        sens = QSlider(Qt.Horizontal)
        sens.setRange(30, 80)
        sens.setValue(int(float(self.s["wake_sensitivity"]) * 100))
        sens.setToolTip("Lower = triggers more easily; higher = fewer false triggers")
        sens.valueChanged.connect(lambda v: self.s.set("wake_sensitivity", round(v / 100, 2)))
        srow.addWidget(QLabel("Eager"))
        srow.addWidget(sens, 1)
        srow.addWidget(QLabel("Strict"))
        c.addLayout(srow)
        c.addWidget(_muted("Sensitivity changes apply after toggling the switch off and on."))
        pl.addWidget(card)

        card, c = _card("Global shortcut", f"Wayland apps can't grab keys globally, so {brand.NAME} adds a GNOME "
                        "custom shortcut (your existing shortcuts are backed up first).")
        self.sc_label = _muted("")
        c.addWidget(self.sc_label)
        row = QHBoxLayout()
        inst = QPushButton("Install Super+Alt+N → chat")
        rem = QPushButton("Remove shortcut")
        inst.clicked.connect(self._install_shortcut)
        rem.clicked.connect(self._remove_shortcut)
        row.addWidget(inst)
        row.addWidget(rem)
        row.addStretch(1)
        c.addLayout(row)
        pl.addWidget(card)
        self._refresh_shortcut()

    def _download_piper(self, btn: QPushButton) -> None:
        if not tts.piper_installed():
            QMessageBox.information(self, f"{brand.NAME}", f"The Piper engine isn't installed in {brand.NAME}'s environment. "
                                    "Re-run scripts/install.sh to add it.")
            return
        btn.setEnabled(False)
        btn.setText("Downloading voice…")

        def work():
            import urllib.request
            d = paths.models_dir() / "piper"
            d.mkdir(parents=True, exist_ok=True)
            for suffix in (".json", ""):
                url = tts.DEFAULT_VOICE_URL + suffix
                dest = d / (Path(tts.DEFAULT_VOICE_URL).name + suffix)
                with urllib.request.urlopen(url, timeout=300) as r:
                    dest.write_bytes(r.read())
            return True

        def done(res):
            btn.setEnabled(True)
            if isinstance(res, Exception):
                btn.setText("Download natural British voice (63 MB)")
                QMessageBox.warning(self, f"{brand.NAME}", f"Download failed: {res}")
            else:
                btn.hide()
                self.voice_label.setText(f"Current voice: {self.app.speaker.voice_label()}")
                self.app.speaker.speak("Much better. This is my new voice.")
        self._bg(work, done)

    def _download_vosk(self, btn: QPushButton) -> None:
        if QMessageBox.question(self, "Download", "Download the 40 MB English Vosk model from "
                                "alphacephei.com?") != QMessageBox.Yes:
            return
        btn.setEnabled(False)
        btn.setText("Downloading…")

        def work():
            import io
            import urllib.request
            import zipfile
            url = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
            with urllib.request.urlopen(url, timeout=120) as r:
                data = r.read()
            paths.models_dir().mkdir(parents=True, exist_ok=True)
            zipfile.ZipFile(io.BytesIO(data)).extractall(paths.models_dir())
            return True

        def done(res):
            btn.setEnabled(True)
            if isinstance(res, Exception):
                btn.setText("Download Vosk model (40 MB)")
                QMessageBox.warning(self, f"{brand.NAME}", f"Download failed: {res}")
            else:
                btn.hide()
                ok, d = stt.status()
                self.stt_status.setText(("✓ " if ok else "⚠ ") + d)
        self._bg(work, done)

    def _test_mic(self) -> None:
        self.stt_status.setText("🎙 Listening… say something")
        self.app.start_listening(from_settings=lambda t: self.stt_status.setText(
            f"Heard: “{t}”" if t else "Didn't catch anything — check the microphone in GNOME Settings → Sound."))

    def _refresh_shortcut(self) -> None:
        b = shortcut.installed()
        self.sc_label.setText(f"Installed: {b}" if b else "Not installed.")

    def _install_shortcut(self) -> None:
        try:
            shortcut.install(f"{startup.LAUNCHER if startup.is_installed() else 'nyxo'} --chat")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, f"{brand.NAME}", f"Couldn't install shortcut: {e}")
        self._refresh_shortcut()

    def _remove_shortcut(self) -> None:
        try:
            shortcut.remove()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, f"{brand.NAME}", f"Couldn't remove shortcut: {e}")
        self._refresh_shortcut()

    # -------------------------------------------------------------- memory
    def _memory(self, pl: QVBoxLayout) -> None:
        card, c = _card(f"What {brand.NAME} remembers", "Facts and preferences. Say “remember that …” in chat, "
                        "or manage them here.")
        self.mem_list = QListWidget()
        self.mem_list.setMinimumHeight(160)
        c.addWidget(self.mem_list)
        row = QHBoxLayout()
        for text, fn in (("Add", self._mem_add), ("Edit", self._mem_edit), ("Delete", self._mem_delete)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        c.addLayout(row)
        pl.addWidget(card)

        card, c = _card("Tasks")
        self.task_list = QListWidget()
        self.task_list.setMaximumHeight(140)
        c.addWidget(self.task_list)
        row = QHBoxLayout()
        b1 = QPushButton("Mark done")
        b1.clicked.connect(self._task_done)
        b2 = QPushButton("Delete")
        b2.clicked.connect(self._task_delete)
        row.addWidget(b1)
        row.addWidget(b2)
        row.addStretch(1)
        c.addLayout(row)
        pl.addWidget(card)

        card, c = _card("Your data", f"Stored locally in {paths.data_dir()}.")
        row = QHBoxLayout()
        clear = QPushButton("Clear chat history")
        clear.setObjectName("danger")
        clear.clicked.connect(self._clear_history)
        exp = QPushButton("Export JSON…")
        exp.clicked.connect(self._export)
        bak = QPushButton("Back up database")
        bak.clicked.connect(self._backup)
        opn = QPushButton("Open data folder")
        opn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.data_dir()))))
        for b in (clear, exp, bak, opn):
            row.addWidget(b)
        row.addStretch(1)
        c.addLayout(row)
        self.data_status = _muted("")
        c.addWidget(self.data_status)
        pl.addWidget(card)

    def _refresh_memories(self) -> None:
        self.mem_list.clear()
        for m in self.app.db.list_memories():
            it = QListWidgetItem(m["content"])
            it.setData(Qt.UserRole, m["id"])
            self.mem_list.addItem(it)
        self.task_list.clear()
        for t in self.app.db.list_tasks():
            it = QListWidgetItem(f"#{t['id']}  {t['title']}")
            it.setData(Qt.UserRole, t["id"])
            self.task_list.addItem(it)

    def _mem_add(self) -> None:
        t, ok = QInputDialog.getText(self, "Add memory", f"Something {brand.NAME} should remember:")
        if ok and t.strip():
            self.app.db.add_memory(t.strip())
            self._refresh_memories()

    def _mem_edit(self) -> None:
        it = self.mem_list.currentItem()
        if not it:
            return
        t, ok = QInputDialog.getText(self, "Edit memory", "Memory:", text=it.text())
        if ok and t.strip():
            self.app.db.update_memory(it.data(Qt.UserRole), t.strip())
            self._refresh_memories()

    def _mem_delete(self) -> None:
        it = self.mem_list.currentItem()
        if it and QMessageBox.question(self, "Delete memory", f"Forget “{it.text()}”?") == QMessageBox.Yes:
            self.app.db.delete_memory(it.data(Qt.UserRole))
            self._refresh_memories()

    def _task_done(self) -> None:
        it = self.task_list.currentItem()
        if it:
            self.app.db.complete_task(it.data(Qt.UserRole))
            self.app.companion.react("celebrating", 3)
            self._refresh_memories()

    def _task_delete(self) -> None:
        it = self.task_list.currentItem()
        if it and QMessageBox.question(self, "Delete task", f"Delete {it.text()}?") == QMessageBox.Yes:
            self.app.db.delete_task(it.data(Qt.UserRole))
            self._refresh_memories()

    def _clear_history(self) -> None:
        if QMessageBox.question(self, "Clear history", "Erase the whole conversation history?") == QMessageBox.Yes:
            self.app.clear_chat()
            self.data_status.setText("Chat history cleared.")

    def _export(self) -> None:
        default = str(Path.home() / time.strftime("nyxo-export-%Y%m%d.json"))
        f, _ = QFileDialog.getSaveFileName(self, f"Export {brand.NAME} data", default, "JSON (*.json)")
        if f:
            self.app.db.export_json(Path(f))
            self.data_status.setText(f"Exported to {f}")

    def _backup(self) -> None:
        p = self.app.db.backup(paths.backups_dir())
        self.data_status.setText(f"Backup saved: {p}")

    # ------------------------------------------------------- notifications
    def _notifications(self, pl: QVBoxLayout) -> None:
        card, c = _card("Announce other apps' notifications",
                        f"{brand.NAME} tells you which app sent a notification — e.g. “New notification from "
                        "Slack”. It reads only the app's name; titles and messages are never read, "
                        "stored or sent anywhere. Respects GNOME's Do Not Disturb and focus mode.")
        c.addWidget(self._check("announce_notifications", "Announce which app sent a notification"))
        c.addWidget(self._check("speak_notifications", "Say it out loud (not just the speech bubble)"))
        c.addWidget(_muted("Ask “any notifications?” for a summary of who notified you."))
        pl.addWidget(card)
        card, c = _card(f"{brand.NAME}'s own notifications")
        c.addWidget(self._check("notifications", "Show desktop notifications for reminders"))
        c.addWidget(self._check("notify_sound", "Play a sound"))
        t = QPushButton("Send test notification")
        t.clicked.connect(lambda: self.app.notifier.notify(f"{brand.NAME}", "Notifications are working! 🤖"))
        c.addWidget(t, 0, Qt.AlignLeft)
        pl.addWidget(card)
        card, c = _card("Upcoming reminders", "Say “remind me to … in 20 minutes” in chat.")
        self.rem_list = QListWidget()
        self.rem_list.setMinimumHeight(140)
        c.addWidget(self.rem_list)
        b = QPushButton("Cancel selected")
        b.clicked.connect(self._cancel_reminder)
        c.addWidget(b, 0, Qt.AlignLeft)
        pl.addWidget(card)

    def _refresh_reminders(self) -> None:
        self.rem_list.clear()
        for r in self.app.db.pending_reminders():
            it = QListWidgetItem(f"{datetime.fromtimestamp(r['due']).strftime('%a %d %b %H:%M')} — {r['text']}")
            it.setData(Qt.UserRole, r["id"])
            self.rem_list.addItem(it)

    def _cancel_reminder(self) -> None:
        it = self.rem_list.currentItem()
        if it:
            self.app.db.delete_reminder(it.data(Qt.UserRole))
            self._refresh_reminders()

    # -------------------------------------------------------------- startup
    def _startup(self, pl: QVBoxLayout) -> None:
        card, c = _card(f"Start {brand.NAME} when I log in", "Uses a systemd user service tied to GNOME's "
                        f"graphical session: {brand.NAME} starts after the desktop is ready, runs as you (never "
                        "root), restarts if it crashes, and stays stopped when you choose Quit.")
        self.st_label = _muted("")
        c.addWidget(self.st_label)
        row = QHBoxLayout()
        en = QPushButton("Enable autostart")
        en.setObjectName("primary")
        en.clicked.connect(lambda: self._st(startup.enable))
        dis = QPushButton("Disable autostart")
        dis.clicked.connect(lambda: self._st(startup.disable))
        row.addWidget(en)
        row.addWidget(dis)
        row.addStretch(1)
        c.addLayout(row)
        pl.addWidget(card)
        card, c = _card("Logs & control")
        c.addWidget(_muted(f"Log file: {paths.log_dir() / 'nyxo.log'}\n"
                           "Service log:  journalctl --user -u nyxo.service -e\n"
                           "Manual control:  nyxo · nyxo --chat · nyxo --quit · systemctl --user restart nyxo"))
        row = QHBoxLayout()
        lg = QPushButton("Open log file")
        lg.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.log_dir() / "nyxo.log"))))
        rs = QPushButton(f"Restart {brand.NAME}")
        rs.clicked.connect(self.app.restart)
        q = QPushButton(f"Quit {brand.NAME}")
        q.setObjectName("danger")
        q.clicked.connect(self.app.quit)
        for b in (lg, rs, q):
            row.addWidget(b)
        row.addStretch(1)
        c.addLayout(row)
        pl.addWidget(card)

    def _st(self, fn) -> None:
        ok, msg = fn()
        if not ok:
            QMessageBox.warning(self, f"{brand.NAME}", msg)
        self._refresh_startup()

    def _refresh_startup(self) -> None:
        st = startup.status()
        yes = lambda b: "✓" if b else "✗"  # noqa: E731
        self.st_label.setText(
            f"{yes(st['installed'])} Installed in {st['install_home']}\n"
            f"{yes(st['unit'])} Service file  {startup.unit_path()}\n"
            f"{yes(st['enabled'])} Starts at login\n"
            f"{yes(st['active'])} Running under systemd now")

    # ---------------------------------------------------- privacy & security
    def _privacy(self, pl: QVBoxLayout) -> None:
        card, c = _card("Privacy")
        c.addWidget(self._check("remember_history", "Save chat history (needed for conversation context)"))
        c.addWidget(self._check("web_lookup", "Look up facts on Wikipedia for “who/what is…” questions "
                                "(sends only the topic to wikipedia.org)"))
        c.addWidget(_muted("Passwords, API keys and tokens are automatically redacted before anything is "
                           "saved. With Ollama, nothing leaves your computer."))
        pl.addWidget(card)

        card, c = _card("Custom commands", f"Programs you approve for {brand.NAME} to run (“run <name>”). "
                        "No shell, no sudo; each run asks first unless you untick “confirm”.")
        self.cmd_table = QTableWidget(0, 3)
        self.cmd_table.setHorizontalHeaderLabels(["Name", "Command", "Confirm"])
        self.cmd_table.horizontalHeader().setStretchLastSection(True)
        self.cmd_table.setMinimumHeight(130)
        self.cmd_table.setEditTriggers(QTableWidget.NoEditTriggers)
        c.addWidget(self.cmd_table)
        row = QHBoxLayout()
        a = QPushButton("Add…")
        a.clicked.connect(self._cmd_add)
        d = QPushButton("Remove")
        d.clicked.connect(self._cmd_remove)
        row.addWidget(a)
        row.addWidget(d)
        row.addStretch(1)
        c.addLayout(row)
        pl.addWidget(card)

        card, c = _card("Action log", f"Everything {brand.NAME} did on your computer.")
        self.log_table = QTableWidget(0, 4)
        self.log_table.setHorizontalHeaderLabels(["Time", "Action", "Approved", "Result"])
        self.log_table.horizontalHeader().setStretchLastSection(True)
        self.log_table.setMinimumHeight(180)
        self.log_table.setEditTriggers(QTableWidget.NoEditTriggers)
        c.addWidget(self.log_table)
        pl.addWidget(card)

    def _refresh_privacy(self) -> None:
        cmds = self.app.db.list_custom_commands()
        self.cmd_table.setRowCount(len(cmds))
        for i, cmd in enumerate(cmds):
            self.cmd_table.setItem(i, 0, QTableWidgetItem(cmd["name"]))
            self.cmd_table.setItem(i, 1, QTableWidgetItem(shlex.join(cmd["argv"])))
            self.cmd_table.setItem(i, 2, QTableWidgetItem("yes" if cmd["confirm"] else "no"))
        acts = self.app.db.recent_actions(100)
        self.log_table.setRowCount(len(acts))
        for i, a in enumerate(acts):
            self.log_table.setItem(i, 0, QTableWidgetItem(time.strftime("%d %b %H:%M", time.localtime(a["ts"]))))
            self.log_table.setItem(i, 1, QTableWidgetItem(f"{a['action']} {a['args']}"))
            self.log_table.setItem(i, 2, QTableWidgetItem("yes" if a["approved"] else "-"))
            self.log_table.setItem(i, 3, QTableWidgetItem(("✓ " if a["ok"] else "✗ ") + a["result"][:200]))
        self.log_table.resizeColumnsToContents()

    def _cmd_add(self) -> None:
        name, ok = QInputDialog.getText(self, "Custom command", "Name (what you'll say after 'run'):")
        if not ok or not name.strip():
            return
        line, ok = QInputDialog.getText(self, "Custom command",
                                        "Program and arguments (no shell syntax), e.g.\n  apt list --upgradable")
        if not ok or not line.strip():
            return
        try:
            argv = policy.custom_argv(shlex.split(line))
        except (ValueError, policy.ValidationError) as e:
            QMessageBox.warning(self, f"{brand.NAME}", str(e))
            return
        confirm = QMessageBox.question(self, "Custom command",
                                       "Ask for confirmation every time it runs?") == QMessageBox.Yes
        self.app.db.add_custom_command(name, argv, confirm)
        self._refresh_privacy()

    def _cmd_remove(self) -> None:
        r = self.cmd_table.currentRow()
        if r >= 0:
            self.app.db.delete_custom_command(self.cmd_table.item(r, 0).text())
            self._refresh_privacy()

    # ---------------------------------------------------------------- about
    def _about(self, pl: QVBoxLayout) -> None:
        card, c = _card(f"NYXO {__version__}")
        h = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(QPixmap(str(self.app.icon_path)).scaled(96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        h.addWidget(logo)
        v = QVBoxLayout()
        tag = QLabel("Your desktop. Your companion. Your AI.")
        tag.setStyleSheet(f"color:{theme.ORANGE}; font-weight:700; font-size:12pt;")
        v.addWidget(tag)
        v.addWidget(_muted("An animated AI desktop companion for Ubuntu (GNOME · Wayland). "
                           "Built with Python, PySide6, SQLite, Ollama, Vosk and espeak. MIT licence."))
        h.addLayout(v, 1)
        c.addLayout(h)
        c.addWidget(_muted(f"Display: {self.app.mode_label}\n"
                           f"Config: {paths.config_dir()}\nData: {paths.data_dir()}\n"
                           f"Logs: {paths.log_dir()}\nApp: {Path(__file__).resolve().parents[2]}"))
        pl.addWidget(card)
