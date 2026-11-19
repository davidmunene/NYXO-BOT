"""The assistant pipeline: user text -> (rule intent | AI) -> actions -> replies.

Runs work on background threads and talks to the UI only through Qt signals
(which are delivered on the GUI thread)."""
from __future__ import annotations

import itertools
import json
import logging
import re
import threading

from PySide6.QtCore import QObject, Signal

from ..ai.base import AIError, AIUnavailable, Cancelled, ChatMessage
from ..ai.manager import AIManager
from ..core.config import Settings
from ..memory.db import Database
from ..security.policy import NeedsConfirmation, ValidationError
from ..system.actions import ActionRegistry, ActionResult
from ..system import knowledge
from . import intents, personality, recipes


def friendly_ai_error(msg: str) -> str:
    m = msg.lower()
    if "llama-server" in m or "binary not found" in m:
        return ("my local AI engine (Ollama) is missing some of its files, so I can't think right now. "
                "Re-running the installer fixes it; everything else still works.")
    if "model" in m and "not found" in m:
        return "the AI model isn't downloaded yet (Settings → AI providers → Download a model)."
    if "connection refused" in m or "timed out" in m:
        return "the AI service didn't respond. I'll try again next time."
    if "401" in m or "403" in m or "api key" in m:
        return "the cloud AI rejected the API key (Settings → AI providers)."
    short = re.sub(r"\{.*", "", msg).strip()[:140]
    return short or "an unexpected AI error."
from ..core import brand

log = logging.getLogger("nyxo.assistant")

_ACTION_RE = re.compile(r"^\s*ACTION:\s*(\{.*\})\s*$", re.M)


def extract_action(text: str) -> tuple[str, dict | None]:
    """Split an AI reply into (visible text, proposed action or None)."""
    m = _ACTION_RE.search(text)
    if not m:
        return text.strip(), None
    visible = (text[:m.start()] + text[m.end():]).strip()
    try:
        data = json.loads(m.group(1))
        if isinstance(data, dict) and isinstance(data.get("name"), str):
            args = data.get("args") or {}
            return visible, {"name": data["name"], "args": args if isinstance(args, dict) else {}}
    except json.JSONDecodeError:
        pass
    return visible, None


class Assistant(QObject):
    reply_started = Signal()
    token = Signal(str)
    reply_done = Signal(str, str)          # text, mood
    notice = Signal(str, str)              # action result / info, mood
    confirm_needed = Signal(int, str)      # request id, explanation
    busy = Signal(str)                     # hold state name, "" to release
    ui_command = Signal(str, dict)
    ai_status = Signal(bool, str)

    def __init__(self, settings: Settings, db: Database, registry: ActionRegistry, ai: AIManager):
        super().__init__()
        self.settings = settings
        self.db = db
        self.registry = registry
        self.ai = ai
        self._cancel = threading.Event()
        self._worker: threading.Thread | None = None
        self._pending: dict[int, tuple[str, dict]] = {}
        self._ids = itertools.count(1)
        self.last_via_voice = False

    # ------------------------------------------------------------ public API
    def submit(self, text: str, via_voice: bool = False) -> None:
        text = text.strip()
        if not text:
            return
        self.last_via_voice = via_voice
        if self.settings["remember_history"]:
            self.db.add_message("user", text)
        intent = intents.parse(text)
        if intent and intent.action.startswith("ui:"):
            self._ui(intent)
            return
        if intent:
            self._run_bg(self._do_action, intent.action, intent.args)
            return
        self._run_bg(self._do_ai, text)

    def respond_confirmation(self, req_id: int, approved: bool) -> None:
        req = self._pending.pop(req_id, None)
        if req is None:
            return
        name, args = req
        if not approved:
            self.db.log_action(name, args, False, False, "declined by user")
            self._say_notice("Okay, I won't do that.", "happy")
            return
        self._run_bg(self._do_action, name, args, True)

    def cancel(self) -> None:
        self._cancel.set()

    def is_busy(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    # -------------------------------------------------------------- helpers
    def _run_bg(self, fn, *args) -> None:
        if self.is_busy():
            self._cancel.set()
            self._worker.join(timeout=2)
        self._cancel = threading.Event()
        self._worker = threading.Thread(target=self._guard, args=(fn, *args), daemon=True)
        self._worker.start()

    def _guard(self, fn, *args) -> None:
        try:
            fn(*args)
        except Exception as e:  # noqa: BLE001
            log.exception("assistant worker crashed")
            self.busy.emit("")
            self._say_notice(f"Oops, something went wrong inside me: {e}", "error")

    def _say_notice(self, text: str, mood: str) -> None:
        if self.settings["remember_history"]:
            self.db.add_message("assistant", text)
        self.notice.emit(text, mood)

    def _ui(self, intent: intents.Intent) -> None:
        name = intent.action[3:]
        p = self.settings["personality"]
        replies = {
            "greet": (personality.greeting(p, self.settings["user_name"]), "waving"),
            "briefing": ("", "working"),
            "notifications": ("", "working"),
            "help": (personality.HELP_TEXT, "happy"),
            "wave": ("👋 Hiii!", "waving"),
            "celebrate": ("Party time! 🎉", "celebrating"),
            "sleep": ("Okay… powering down to nap mode. Click me to wake me. 💤", "sleeping"),
            "wake": ("I'm up! I'm up!", "excited"),
            "hide": (f"Hiding! Click the tray icon or launch {brand.NAME} again to bring me back.", "waving"),
            "quiet": (("Focus mode on — I'll stay still and quiet." if intent.args.get("on")
                       else "Focus mode off — back to normal shenanigans!"), "happy"),
            "settings": ("Opening my settings.", "working"),
            "chat": ("I'm right here!", "happy"),
            "ask": (intent.args.get("text", ""), "confused"),
        }
        text, mood = replies.get(name, ("", "happy"))
        self.ui_command.emit(name, intent.args)
        if text:
            self._say_notice(text, mood)

    # -------------------------------------------------------------- actions
    def _do_action(self, name: str, args: dict, approved: bool = False) -> None:
        self.busy.emit("working")
        try:
            res = self.registry.execute(name, args, approved=approved)
        except NeedsConfirmation as nc:
            self.busy.emit("")
            rid = next(self._ids)
            self._pending[rid] = (nc.action, nc.params)
            self.confirm_needed.emit(rid, nc.explanation)
            return
        self.busy.emit("")
        self._report(res)

    def _report(self, res: ActionResult) -> None:
        mood = res.mood if res.ok else (res.mood if res.mood in ("confused", "error") else "confused")
        self._say_notice(res.message, mood)

    # ------------------------------------------------------------------- AI
    def _context(self) -> list[ChatMessage]:
        n = int(self.settings["context_messages"])
        msgs = self.db.recent_messages(n) if self.settings["remember_history"] else []
        return [ChatMessage(m["role"], m["content"]) for m in msgs if m["role"] in ("user", "assistant")]

    def _do_ai(self, text: str) -> None:
        verified = recipes.match(text)
        if verified:
            self._say_notice(verified, "happy")
            return
        ok, detail = self.ai.status()
        provider = self.ai.provider
        ref = None
        topic = knowledge.topic_of(text) if self.settings["web_lookup"] else None
        if topic:
            self.busy.emit("thinking")
            ref = knowledge.wikipedia(topic)
        if not ok or provider is None:
            self.ai_status.emit(False, detail)
            if ref:
                self._say_notice(f"According to Wikipedia ({ref['title']}): "
                                 f"{knowledge.first_sentences(ref['extract'])}", "happy")
                return
            self._say_notice(f"{personality.OFFLINE_REPLIES[0]}\n\n(AI unavailable: {detail})", "confused")
            return
        context = self._context()
        if not self.settings["remember_history"] or not context or context[-1].content != text:
            context.append(ChatMessage("user", text))
        memories = [m["content"] for m in self.db.list_memories()]
        system = personality.system_prompt(
            self.settings["personality"], self.settings["verbosity"], self.settings["user_name"],
            memories, self.registry.describe_for_ai() if self.settings["ai_actions"] else None,
            bool(self.settings["quiet_mode"]), spoken=self.settings["tts_mode"] != "never")
        note = personality.context_note(self.settings["user_name"], memories,
                                        bool(self.settings["quiet_mode"]), ref)
        if getattr(provider, "name", "") == "ollama":
            # keep the cached prefix stable: dynamic facts go just before the last message
            context.insert(len(context) - 1, ChatMessage("system", note))
        else:
            system += "\n\n" + note
        self.busy.emit("thinking")
        self.reply_started.emit()
        started = [False]

        def on_token(tok: str) -> None:
            if not started[0]:
                started[0] = True
                self.busy.emit("talking")
            self.token.emit(tok)

        try:
            full = provider.chat(context, system, on_token=on_token, cancel=self._cancel)
        except Cancelled:
            self.busy.emit("")
            self.reply_done.emit("(stopped)", "happy")
            return
        except (AIUnavailable, AIError) as e:
            self.busy.emit("")
            self.ai_status.emit(False, friendly_ai_error(str(e)))
            self.reply_done.emit("", "confused")
            if ref:
                self._say_notice(f"According to Wikipedia ({ref['title']}): "
                                 f"{knowledge.first_sentences(ref['extract'])}", "happy")
                return
            log.warning("AI error: %s", e)
            self._say_notice(f"Sorry — {friendly_ai_error(str(e))}", "confused")
            return
        self.busy.emit("")
        self.ai_status.emit(True, detail)
        visible, action = extract_action(full)
        if not visible.strip() and not action:
            visible = ("Sorry, I couldn't come up with a good answer to that. Could you rephrase it, "
                       "or ask me something more specific?")
        if self.settings["remember_history"] and visible:
            self.db.add_message("assistant", visible)
        self.reply_done.emit(visible, "happy")
        if action and self.settings["ai_actions"]:
            self._ai_action(action)

    def _ai_action(self, action: dict) -> None:
        name, args = action["name"], action["args"]
        try:
            self.registry.validate(name, args)
        except ValidationError as e:
            log.info("AI proposed invalid action %s %s: %s", name, args, e)
            return
        # Every AI-proposed action needs approval unless it is read-only SAFE.
        if self.registry.needs_confirmation(name, args):
            rid = next(self._ids)
            _, clean = self.registry.validate(name, args)
            self._pending[rid] = (name, clean)
            self.confirm_needed.emit(rid, f"{brand.NAME}'s AI wants to: " + self.registry.explain(name, clean))
            return
        self._do_action(name, args)
