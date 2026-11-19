"""Provider-independent AI interface."""
from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Iterator
from ..core import brand

TokenCallback = Callable[[str], None]


class AIError(Exception):
    pass


class AIUnavailable(AIError):
    """Provider not reachable / not configured. The UI shows this clearly."""


class Cancelled(AIError):
    pass


@dataclass
class ChatMessage:
    role: str      # user | assistant
    content: str


class Provider(ABC):
    name: str = "base"
    label: str = "Base"

    @abstractmethod
    def status(self) -> tuple[bool, str]:
        """(available, human readable detail). Must be fast (<3s)."""

    @abstractmethod
    def chat(self, messages: list[ChatMessage], system: str,
             on_token: TokenCallback | None = None,
             cancel: threading.Event | None = None) -> str:
        """Return the full reply; stream tokens to on_token when given."""

    def models(self) -> list[str]:
        return []


# -- small HTTP helpers (stdlib only, keeps the footprint low) ----------------

def http_json(url: str, payload: dict | None = None, headers: dict | None = None,
              timeout: float = 5.0, method: str | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"))
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", f"{brand.NAME}/1.0")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")[:400]
        raise AIError(f"HTTP {e.code}: {body}") from None
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
        raise AIUnavailable(str(getattr(e, "reason", e))) from None


def http_stream_lines(url: str, payload: dict, headers: dict | None = None,
                      timeout: float = 120.0,
                      cancel: threading.Event | None = None) -> Iterator[str]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", f"{brand.NAME}/1.0")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")[:500]
        if e.code in (401, 403):
            raise AIUnavailable(f"authentication failed (HTTP {e.code}). Check your API key.") from None
        raise AIError(f"HTTP {e.code}: {body}") from None
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
        raise AIUnavailable(str(getattr(e, "reason", e))) from None
    with resp:
        for raw in resp:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            line = raw.decode("utf-8", errors="ignore").strip()
            if line:
                yield line
