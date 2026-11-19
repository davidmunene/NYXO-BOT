"""Scrub credentials out of anything that gets persisted or logged."""
from __future__ import annotations

import re

_PATTERNS = [
    # provider-style API keys
    (re.compile(r"\b(sk-ant-[A-Za-z0-9_\-]{10,}|sk-[A-Za-z0-9_\-]{16,}|gsk_[A-Za-z0-9]{16,}"
                r"|AIza[0-9A-Za-z_\-]{20,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
                r"|xox[abposr]-[A-Za-z0-9\-]{10,}|AKIA[0-9A-Z]{16})\b"), "[REDACTED-KEY]"),
    # "password: hunter2", "api_key=...", "token is abc"
    (re.compile(r"(?i)\b(pass(?:word|wd|phrase)?|api[_ -]?key|secret|token|pin)\b(\s*(?:is|=|:)\s*)(\S+)"),
     r"\1\2[REDACTED]"),
    # bearer headers
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{12,}"), "Bearer [REDACTED]"),
]


def redact(text: str) -> str:
    if not text:
        return text
    for pat, repl in _PATTERNS:
        text = pat.sub(repl, text)
    return text


def contains_secret(text: str) -> bool:
    return redact(text) != text
