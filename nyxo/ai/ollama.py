"""Local inference through Ollama (CPU friendly with small models)."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time

from .base import AIError, AIUnavailable, ChatMessage, Provider, TokenCallback, http_json, http_stream_lines

log = logging.getLogger("nyxo.ai.ollama")

# Models that run acceptably on a 4-core laptop CPU with ~14 GB RAM.
RECOMMENDED = [
    ("qwen2.5:3b", "~1.9 GB, smarter (default)"),
    ("qwen2.5:1.5b", "~1 GB, faster, lighter on RAM"),
    ("llama3.2:1b", "~1.3 GB, fastest, simpler answers"),
    ("qwen2.5-coder:1.5b", "~1 GB, tuned for code"),
    ("llama3.2:3b", "~2 GB, smarter, slower"),
]


class OllamaProvider(Provider):
    name = "ollama"
    label = "Ollama (local)"

    def __init__(self, url: str, model: str, temperature: float = 0.7, autostart: bool = True):
        self.url = url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.autostart = autostart
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    USER_BIN = os.path.expanduser("~/.local/opt/ollama/bin/ollama")

    @classmethod
    def binary(cls) -> str | None:
        """Prefer the user-level runtime installed by the assistant's installer."""
        if os.access(cls.USER_BIN, os.X_OK):
            return cls.USER_BIN
        return shutil.which("ollama")

    @classmethod
    def installed(cls) -> bool:
        return cls.binary() is not None

    @classmethod
    def runtime_ok(cls) -> bool:
        """Ollama >= 0.30 needs lib/ollama/llama-server next to the binary."""
        b = cls.binary()
        if not b:
            return False
        lib = os.path.join(os.path.dirname(os.path.realpath(b)), "..", "lib", "ollama")
        return any(os.path.exists(os.path.join(lib, n)) for n in ("llama-server", "libggml-base.so")) \
            or not os.path.isdir(lib)

    def reachable(self, timeout: float = 1.5) -> bool:
        try:
            http_json(f"{self.url}/api/version", timeout=timeout)
            return True
        except AIError:
            return False

    def ensure_server(self, wait_s: float = 12.0) -> bool:
        """Start `ollama serve` on demand (lazy loading) if allowed."""
        if self.reachable():
            return True
        if not (self.autostart and self.installed()):
            return False
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                from ..system import runner
                log.info("starting ollama serve on demand")
                self._proc = runner.spawn([self.binary(), "serve"])
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if self.reachable(0.8):
                return True
            time.sleep(0.4)
        return False

    def stop_server(self) -> None:
        """Only stops a server NYXO itself started."""
        if self._proc and self._proc.poll() is None:
            log.info("stopping ollama serve we started")
            self._proc.terminate()
            try:
                self._proc.wait(5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None

    def models(self) -> list[str]:
        try:
            data = http_json(f"{self.url}/api/tags", timeout=3)
        except AIError:
            return []
        return sorted(m["name"] for m in data.get("models", []))

    def status(self) -> tuple[bool, str]:
        if not self.installed() and not self.reachable():
            return False, ("Ollama is not installed. Install it from https://ollama.com/download "
                           "then run: ollama pull qwen2.5:1.5b")
        if self.installed() and not self.runtime_ok() and not self.reachable():
            return False, ("Ollama's engine files are incomplete (llama-server missing). "
                           "Re-run scripts/install.sh to install a working copy.")
        if not self.reachable():
            if self.autostart and self.installed():
                return True, "Ollama will start when you first ask something."
            return False, "Ollama server isn't running. Start it with: ollama serve"
        ms = self.models()
        if not ms:
            return False, "Ollama is running but has no models. Run: ollama pull qwen2.5:1.5b"
        if self.model not in ms and f"{self.model}:latest" not in ms:
            return False, f"Model '{self.model}' isn't installed. Available: {', '.join(ms)}"
        return True, f"Ollama · {self.model}"

    def chat(self, messages: list[ChatMessage], system: str,
             on_token: TokenCallback | None = None,
             cancel: threading.Event | None = None) -> str:
        if not self.ensure_server():
            raise AIUnavailable("Ollama isn't running and couldn't be started.")
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}] +
                        [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            "keep_alive": "10m",
            "options": {"temperature": self.temperature, "num_ctx": 4096, "num_thread": 4},
        }
        out: list[str] = []
        for line in http_stream_lines(f"{self.url}/api/chat", payload, timeout=180, cancel=cancel):
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "error" in ev:
                err = ev["error"]
                if "not found" in err:
                    raise AIUnavailable(f"Model '{self.model}' isn't installed. Run: ollama pull {self.model}")
                raise AIError(err)
            tok = ev.get("message", {}).get("content", "")
            if tok:
                out.append(tok)
                if on_token:
                    on_token(tok)
            if ev.get("done"):
                break
        return "".join(out)
