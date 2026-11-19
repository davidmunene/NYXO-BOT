"""Builds the configured provider from settings and exposes status."""
from __future__ import annotations

from typing import Callable

from .base import Provider
from .cloud import AnthropicProvider, OpenAICompatProvider
from .ollama import OllamaProvider

PROVIDERS = {"ollama": "Ollama (local, private)", "anthropic": "Anthropic Claude (cloud)",
             "openai": "OpenAI-compatible (cloud)", "offline": "Off (no AI)"}


class AIManager:
    def __init__(self, get: Callable[[str], object]):
        self._get = get
        self._ollama: OllamaProvider | None = None
        self.provider: Provider | None = None
        self.rebuild()

    def rebuild(self) -> None:
        g = self._get
        kind = g("ai_provider")
        if kind == "ollama":
            if self._ollama is None:
                self._ollama = OllamaProvider(str(g("ollama_url")), str(g("ollama_model")),
                                              float(g("ai_temperature")), bool(g("ollama_autostart")))
            else:
                self._ollama.url = str(g("ollama_url")).rstrip("/")
                self._ollama.model = str(g("ollama_model"))
                self._ollama.temperature = float(g("ai_temperature"))
                self._ollama.autostart = bool(g("ollama_autostart"))
            self.provider = self._ollama
        elif kind == "anthropic":
            self.provider = AnthropicProvider(str(g("anthropic_model")))
        elif kind == "openai":
            self.provider = OpenAICompatProvider(str(g("openai_base_url")), str(g("openai_model")),
                                                 float(g("ai_temperature")))
        else:
            self.provider = None

    def status(self) -> tuple[bool, str]:
        if self.provider is None:
            return False, "AI is turned off (Settings → AI providers). Commands still work."
        return self.provider.status()

    @property
    def ollama(self) -> OllamaProvider:
        if self._ollama is None:
            g = self._get
            self._ollama = OllamaProvider(str(g("ollama_url")), str(g("ollama_model")),
                                          float(g("ai_temperature")), bool(g("ollama_autostart")))
        return self._ollama

    def shutdown(self) -> None:
        if self._ollama:
            self._ollama.stop_server()
