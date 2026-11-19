"""Factual grounding from Wikipedia (public REST API, no key).

Used for factual questions ("who is…", "what is…", "tell me about…"): the
summary is handed to the language model as a reference so answers are
accurate instead of invented — or spoken directly when no AI is available.
Only the question's topic is sent to Wikipedia; it can be turned off in
Settings → Privacy."""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

from ..core import brand

_FACTUAL = re.compile(
    r"^(?:(?:can you |could you |please )?(?:tell me|explain)(?: more)? about |"
    r"who (?:is|was|are|were) |what (?:is|was|are|were) (?:a |an |the )?|"
    r"where (?:is|was) |when (?:was|did|is) |define |definition of |"
    r"how (?:old|tall|big|far|long|many|much) (?:is|was|are) |history of |facts about )"
    r"(.+?)\??$", re.I)
_NOT_FACTUAL = re.compile(
    r"\b(you|your|yourself|my|me|i|time|date|day|today|tomorrow|weather|battery|cpu|ram|disk|"
    r"playing|this|that|it|git|branch|error|wrong|happening|up)\b", re.I)


def topic_of(question: str) -> str | None:
    q = re.sub(r"^\s*(hey |ok |okay )?" + re.escape(brand.NAME) + r"[,!]?\s*", "", question.strip(), flags=re.I)
    m = _FACTUAL.match(q.strip())
    if not m:
        return None
    topic = m.group(1).strip(" .?!")
    if not topic or len(topic) > 80 or _NOT_FACTUAL.search(topic):
        return None
    return topic


def _get(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "KingsDesktopAssistant/1.0 (personal use)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def wikipedia(topic: str, timeout: float = 4.0) -> dict | None:
    """Returns {"title", "extract", "url"} or None."""
    try:
        q = urllib.parse.quote(topic)
        s = _get("https://en.wikipedia.org/w/api.php?action=query&list=search&format=json"
                 f"&srlimit=1&srprop=&srsearch={q}", timeout)
        hits = s.get("query", {}).get("search", [])
        if not hits:
            return None
        title = hits[0]["title"]
        d = _get("https://en.wikipedia.org/api/rest_v1/page/summary/" +
                 urllib.parse.quote(title.replace(" ", "_")), timeout)
        extract = (d.get("extract") or "").strip()
        if not extract or d.get("type") == "disambiguation":
            return None
        return {"title": d.get("title", title), "extract": extract[:1500],
                "url": d.get("content_urls", {}).get("desktop", {}).get("page", "")}
    except Exception:  # noqa: BLE001 - offline / timeout / unexpected payload
        return None


def first_sentences(text: str, n: int = 3) -> str:
    parts = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(parts[:n])
