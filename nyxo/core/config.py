"""Typed settings with defaults, persisted in the SQLite settings table."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal

from ..memory.db import Database

DEFAULTS: dict[str, Any] = {
    # general / desktop
    "display_mode": "xwayland",          # xwayland | wayland
    "character_size": 180,               # px
    "avatar": "kings",                   # kings (portrait built from your image) | robot
    "position": None,                    # [x, y] last position
    "roam": True,                        # wander around
    "roam_area": "bottom",               # bottom | screen
    "animations_enabled": True,
    "quiet_mode": False,                 # focus mode: no roaming / chatter
    "paused": False,
    "chatter": True,                     # occasional idle quips
    "sleep_after_min": 8,
    "always_on_top": True,
    "desktop_layer": True,               # stay visible on the empty desktop / Show Desktop
    "announce_notifications": True,      # say which app sent a notification (never its content)
    "speak_notifications": True,
    "first_run_done": False,
    # personality
    "assistant_name": "Kings",           # display + wake name (restart to apply)
    "personality": "jarvis",             # jarvis | playful | professional | sarcastic | encouraging
    "verbosity": "short",                # short | normal | detailed
    "user_name": "",
    "dev_repo": "",                      # default git repository for git commands
    # AI
    "ai_provider": "ollama",             # ollama | anthropic | openai | offline
    "ollama_url": "http://127.0.0.1:11434",
    "ollama_model": "qwen2.5:3b",
    "ollama_autostart": True,
    "anthropic_model": "claude-haiku-5-5",
    "openai_base_url": "https://api.openai.com/v1",
    "openai_model": "gpt-4o-mini",
    "ai_temperature": 0.7,
    "context_messages": 12,
    "ai_actions": True,                  # let the AI propose registry actions
    # voice
    "tts_mode": "always",                # always | voice_only | never
    "tts_engine": "auto",                # auto | piper | espeak | spd-say
    "tts_voice": "en-us",
    "tts_rate": 165,
    "briefing_on_start": True,           # spoken status briefing at login
    "system_alerts": True,               # proactive battery/disk/network/CPU alerts
    "piper_model": "",
    "stt_enabled": True,
    "wake_word": True,                   # hands-free: call the assistant by name (mic analysed locally)
    "wake_engine": "name",               # name ("Kings, …") | hey_jarvis
    "stt_model": "auto",                 # Whisper size: auto | small.en | base.en | tiny.en | off
    "wake_sensitivity": 0.5,             # detection threshold 0.3 (eager) .. 0.8 (strict)
    "auto_hide_fullscreen": True,        # step aside for full-screen apps
    "auto_hide_media": False,            # …also whenever video keeps the screen awake (coarse: off by default)
    "vosk_model": "",                    # empty = auto-detect in models dir
    "stt_max_seconds": 12,
    # memory / privacy
    "remember_history": True,
    "web_lookup": True,                  # Wikipedia facts for "who/what is…" questions
    "learn_preferences": True,
    # notifications
    "notifications": True,
    "notify_sound": True,
}


class Settings(QObject):
    changed = Signal(str, object)

    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        self._cache = {**DEFAULTS, **db.all_settings()}

    def get(self, key: str) -> Any:
        if key not in DEFAULTS and key not in self._cache:
            raise KeyError(key)
        return self._cache.get(key, DEFAULTS.get(key))

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def set(self, key: str, value: Any) -> None:
        if key not in DEFAULTS:
            raise KeyError(f"unknown setting {key}")
        if self._cache.get(key) == value:
            return
        self._cache[key] = value
        self.db.set_setting(key, value)
        self.changed.emit(key, value)

    def reset(self) -> None:
        for k, v in DEFAULTS.items():
            self.set(k, v)
