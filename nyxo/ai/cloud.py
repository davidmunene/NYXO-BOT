"""Cloud providers: Anthropic (Claude) and any OpenAI-compatible endpoint.
API keys come from environment variables or the 0600 credentials file —
never from settings, logs or chat history."""
from __future__ import annotations

import json
import threading

from . import credentials
from .base import AIError, AIUnavailable, ChatMessage, Provider, TokenCallback, http_stream_lines

ANTHROPIC_MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]


def _merge_alternating(messages: list[ChatMessage]) -> list[dict]:
    """Collapse consecutive same-role turns; ensure the first turn is 'user'."""
    out: list[dict] = []
    for m in messages:
        if m.role not in ("user", "assistant") or not m.content.strip():
            continue
        if out and out[-1]["role"] == m.role:
            out[-1]["content"] += "\n\n" + m.content
        else:
            out.append({"role": m.role, "content": m.content})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


class AnthropicProvider(Provider):
    name = "anthropic"
    label = "Anthropic Claude (cloud)"
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, model: str, max_tokens: int = 1024):
        self.model = model
        self.max_tokens = max_tokens

    def models(self) -> list[str]:
        return list(ANTHROPIC_MODELS)

    def status(self) -> tuple[bool, str]:
        if not credentials.get("ANTHROPIC_API_KEY"):
            return False, ("No Anthropic API key. Add it in Settings → AI providers "
                           "or set ANTHROPIC_API_KEY.")
        return True, f"Anthropic · {self.model}"

    def chat(self, messages: list[ChatMessage], system: str,
             on_token: TokenCallback | None = None,
             cancel: threading.Event | None = None) -> str:
        key = credentials.get("ANTHROPIC_API_KEY")
        if not key:
            raise AIUnavailable("No Anthropic API key configured.")
        payload = {"model": self.model, "max_tokens": self.max_tokens, "system": system,
                   "messages": _merge_alternating(messages), "stream": True}
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        out: list[str] = []
        for line in http_stream_lines(self.URL, payload, headers, cancel=cancel):
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            t = ev.get("type")
            if t == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
                tok = ev["delta"].get("text", "")
                out.append(tok)
                if on_token:
                    on_token(tok)
            elif t == "error":
                raise AIError(ev.get("error", {}).get("message", "unknown error"))
            elif t == "message_stop":
                break
        return "".join(out)


class OpenAICompatProvider(Provider):
    name = "openai"
    label = "OpenAI-compatible (cloud)"

    def __init__(self, base_url: str, model: str, temperature: float = 0.7):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature

    def status(self) -> tuple[bool, str]:
        if not credentials.get("OPENAI_API_KEY"):
            return False, ("No API key for the OpenAI-compatible provider. Add it in "
                           "Settings → AI providers or set OPENAI_API_KEY.")
        return True, f"{self.base_url} · {self.model}"

    def chat(self, messages: list[ChatMessage], system: str,
             on_token: TokenCallback | None = None,
             cancel: threading.Event | None = None) -> str:
        key = credentials.get("OPENAI_API_KEY")
        if not key:
            raise AIUnavailable("No API key configured.")
        msgs = [{"role": "system", "content": system}] + _merge_alternating(messages)
        payload = {"model": self.model, "messages": msgs, "stream": True,
                   "temperature": self.temperature}
        headers = {"Authorization": f"Bearer {key}"}
        out: list[str] = []
        for line in http_stream_lines(f"{self.base_url}/chat/completions", payload, headers,
                                      cancel=cancel):
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                ev = json.loads(data)
            except json.JSONDecodeError:
                continue
            if "error" in ev:
                raise AIError(str(ev["error"]))
            for ch in ev.get("choices", []):
                tok = (ch.get("delta") or {}).get("content") or ""
                if tok:
                    out.append(tok)
                    if on_token:
                        on_token(tok)
        return "".join(out)


