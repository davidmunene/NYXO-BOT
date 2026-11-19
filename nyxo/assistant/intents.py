"""Deterministic natural-language command parser.

Maps common requests straight onto registry actions so they work instantly
and without any AI model. Returns None for anything else (-> AI).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class Intent:
    action: str                  # registry action name, or "ui:<thing>" for app-level commands
    args: dict = field(default_factory=dict)


_FOLDERS = r"(?:desktop|documents|docs|downloads?|music|pictures|photos|videos|templates|public|home(?: folder)?)"
_PATHLIKE = re.compile(r"^(~|/|\.\.?/|\$HOME)")


def _clean(s: str) -> str:
    return s.strip().strip("\"'`").strip().rstrip("?.!").strip()


_UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
          "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
         "ninety": 90}


def words_to_numbers(text: str) -> str:
    """'in twenty five minutes' -> 'in 25 minutes', 'half an hour' -> '30 minutes'."""
    t = re.sub(r"\b(half an hour|half hour)\b", "30 minutes", text, flags=re.I)
    t = re.sub(r"\ba quarter of an hour\b|\ba quarter hour\b", "15 minutes", t, flags=re.I)
    t = re.sub(r"\ban hour and a half\b", "90 minutes", t, flags=re.I)

    def repl(m: re.Match) -> str:
        tens, unit = m.group(1), m.group(2)
        n = (_TENS.get(tens.lower(), 0) if tens else 0) + (_UNITS.get(unit.lower(), 0) if unit else 0)
        return str(n)
    tens_rx = "|".join(_TENS)
    units_rx = "|".join(sorted(_UNITS, key=len, reverse=True))
    t = re.sub(rf"\b(?:({tens_rx})[\s-]?({units_rx})?|({units_rx}))\b",
               lambda m: repl(m) if m.group(1) else str(_UNITS[m.group(3).lower()]), t, flags=re.I)
    return t


def parse_duration_minutes(text: str) -> float | None:
    """'in 10 minutes', 'in 2h', 'in an hour', 'in 30 sec', 'in 1 hour 30 minutes'."""
    t = text.lower()
    m = re.search(r"\bin\s+((?:an?|\d+(?:\.\d+)?)\s*(?:h(?:ours?|rs?)?|m(?:in(?:ute)?s?)?|s(?:ec(?:ond)?s?)?)\b"
                  r"(?:\s*(?:and\s*)?\d+\s*m(?:in(?:ute)?s?)?\b)?)", t)
    if not m:
        return None
    total = 0.0
    for num, unit in re.findall(r"(an?|\d+(?:\.\d+)?)\s*(h|m|s)", m.group(1)):
        n = 1.0 if num in ("a", "an") else float(num)
        total += n * {"h": 60, "m": 1, "s": 1 / 60}[unit]
    return total or None


def parse_clock_minutes(text: str, now: datetime | None = None) -> float | None:
    """'at 15:30', 'at 3pm', 'at 9:15 am', 'tomorrow at 9'."""
    now = now or datetime.now()
    t = text.lower()
    m = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", t)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    if h > 23 or mi > 59:
        return None
    target = now.replace(hour=h, minute=mi, second=0, microsecond=0)
    if "tomorrow" in t:
        target += timedelta(days=1)
    elif target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds() / 60


def _strip_time(text: str) -> str:
    text = re.sub(r"\b(tomorrow\s+)?at\s+\d{1,2}(?::\d{2})?\s*(am|pm)?\b", "", text, flags=re.I)
    text = re.sub(r"\bin\s+(?:an?|\d+(?:\.\d+)?)\s*(?:h(?:ours?|rs?)?|m(?:in(?:ute)?s?)?|s(?:ec(?:ond)?s?)?)\b"
                  r"(?:\s*(?:and\s*)?\d+\s*m(?:in(?:ute)?s?)?\b)?", "", text, flags=re.I)
    text = re.sub(r"\btomorrow\b", "", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip(" ,.")


def _repo_arg(text: str) -> dict:
    m = re.search(r"\b(?:in|for|of|at)\s+((?:~|/|\.)\S*)", text)
    return {"path": m.group(1)} if m else {}


def parse(text: str) -> Intent | None:  # noqa: C901 - a flat list of rules reads best
    raw = text.strip().rstrip("?!.").strip()
    raw = re.sub(r"^(hey |ok |okay |yo )?nyxo[,!:]?\s*", "", raw, flags=re.I)
    raw = re.sub(r"^(please|can you|could you|would you|will you)\s+", "", raw, flags=re.I)
    raw = re.sub(r",?\s+please$", "", raw, flags=re.I).strip()
    t = raw.lower()
    if not t:
        return None

    # --- app-level / character commands
    if re.fullmatch(r"(hi|hello|hey|hiya|howdy|good (morning|afternoon|evening)|yo|sup|wake up,? daddy'?s home)( there)?( nyxo| jarvis)?", t):
        return Intent("ui:greet")
    if re.fullmatch(r"(status report|briefing|give me (a|my|the) (status report|briefing|rundown)|"
                    r"(daily |morning )?briefing|report|sitrep|how are (things|we doing))", t):
        return Intent("ui:briefing")
    if re.fullmatch(r"(any|check|show|read)( (new|my))? notifications|who (messaged|notified|pinged) me|"
                    r"what notifications did i (get|miss)|did i (get|miss) any(thing| notifications)?", t):
        return Intent("ui:notifications")
    if re.fullmatch(r"(help|commands|what can you do|what do you do|how do i use you)", t):
        return Intent("ui:help")
    if re.fullmatch(r"(wave|say hi|wave at me)", t):
        return Intent("ui:wave")
    if re.fullmatch(r"(dance|celebrate|party( time)?|do a dance)", t):
        return Intent("ui:celebrate")
    if re.fullmatch(r"(go to sleep|sleep|take a nap|nap|good ?night)", t):
        return Intent("ui:sleep")
    if re.fullmatch(r"(wake up|wakey wakey)", t):
        return Intent("ui:wake")
    if re.fullmatch(r"(hide|go away|hide yourself|disappear)", t):
        return Intent("ui:hide")
    if m := re.fullmatch(r"(?:turn )?(focus|quiet|work) mode (on|off)|(?:turn )?(on|off) (focus|quiet|work) mode", t):
        on = (m.group(2) or m.group(3)) == "on"
        return Intent("ui:quiet", {"on": on})
    if re.fullmatch(r"(open |show )?(settings|preferences|config)( window)?", t) and "system" not in t:
        return Intent("ui:settings")
    if re.fullmatch(r"(clear|reset|erase|delete) (the )?(chat|conversation|history|chat history)", t):
        return Intent("clear_history")

    # --- YouTube / web
    yt = re.fullmatch(r"(?:play|put on|watch|find|search)(?: some| me| the)? (.+?) (?:on|from|in) youtube(?: music)?|"
                      r"youtube (.+)|search youtube for (.+)", t)
    if yt:
        q = next(g for g in yt.groups() if g)
        if re.fullmatch(r"(some )?(music|songs?)", q.strip()):
            return Intent("open_url", {"url": "https://music.youtube.com/"})
        import urllib.parse
        return Intent("open_url", {"url": "https://www.youtube.com/results?search_query=" +
                                          urllib.parse.quote_plus(q.strip())})
    if re.fullmatch(r"(open |go to |launch )?(youtube)( music)?", t):
        return Intent("open_url", {"url": "https://music.youtube.com/" if "music" in t else "https://www.youtube.com/"})
    if re.fullmatch(r"play (some )?music", t):
        return Intent("media_control", {"command": "play"})
    web = re.fullmatch(r"(?:search (?:the web|online|google|the internet) for|google|bing|look up online|"
                       r"search online for) (.+)", t) or re.fullmatch(r"look up (.+) online", t)
    if web:
        import urllib.parse
        return Intent("open_url", {"url": "https://www.google.com/search?q=" + urllib.parse.quote_plus(web.group(1))})

    # --- media
    if re.fullmatch(r"(what('?s| is) (playing|this song|on)|what song is (this|playing)|now playing)( now| right now)?", t):
        return Intent("now_playing")
    if m := re.fullmatch(r"(pause|stop|resume|play|unpause|skip|next|previous|prev|go back)"
                         r"( the| this)?( music| song| track| video| playback| audio| media)?( please)?", t):
        verb = m.group(1)
        if verb in ("play", "stop") and not m.group(3):
            pass   # bare "play"/"stop" is too ambiguous; let other rules/AI handle it
        else:
            cmd = {"pause": "pause", "stop": "pause", "resume": "play", "unpause": "play", "play": "play",
                   "skip": "next", "next": "next", "previous": "previous", "prev": "previous",
                   "go back": "previous"}[verb]
            return Intent("media_control", {"command": cmd})
    if re.fullmatch(r"(next|skip( this)?|previous|last) (song|track)", t):
        return Intent("media_control", {"command": "previous" if t.startswith(("previous", "last")) else "next"})

    # --- disk cleanup
    if re.fullmatch(r"(clean ?up|free up|reclaim)( some)?( my)?( disk| storage| space| drive)?( space)?|"
                    r"disk clean ?up|(i'?m |i am )?(running )?(out of|low on) (disk )?space", t):
        return Intent("cleanup_scan")
    if re.fullmatch(r"empty( the| my)? (trash|bin|recycle bin)", t):
        return Intent("cleanup", {"target": "trash"})
    if m := re.fullmatch(r"(?:clean|clear)(?: the| my)? ([a-z\-]+?)(?: cache)?", t):
        from ..system.cleanup import TARGETS
        key = m.group(1)
        key = {"chrome": "chrome-cache", "chromium": "chromium-cache", "brave": "brave-cache",
               "firefox": "mozilla-cache", "browser": "chromium-cache"}.get(key, key)
        if key in TARGETS:
            return Intent("cleanup", {"target": key})

    # --- time & date
    if re.fullmatch(r"(what('?s| is) the )?(time|date|day)( is it)?( now| today)?|what time is it|"
                    r"what('?s| is) (today'?s )?date|what day is (it|today)", t):
        return Intent("datetime")

    # --- system info
    if re.search(r"\b(system (status|info|stats|summary)|how('?s| is) (my |the )?(computer|system|pc|laptop))\b", t):
        return Intent("system_info", {"kind": "all"})
    if re.search(r"\b(what('?s| is) (using|taking|eating) (up )?(my |all )?(disk|storage|space)|storage (usage|breakdown)|"
                 r"where('?s| is) my (disk )?space)", t):
        return Intent("storage_usage")
    if re.search(r"\b(cpu|processor)\b", t) and re.search(r"\b(usage|load|use|busy|how|show|check|status)\b|^cpu$", t):
        return Intent("system_info", {"kind": "cpu"})
    if re.search(r"\b(ram|memory usage|ram usage|how much memory|free memory)\b", t) and "remember" not in t:
        return Intent("system_info", {"kind": "ram"})
    if re.search(r"\b(disk|storage|free space|disk space|drive space)\b", t) and \
            re.search(r"\b(usage|space|free|left|how much|show|check|status|full)\b|^disk$", t):
        return Intent("system_info", {"kind": "disk"})
    if re.search(r"\bbattery\b", t):
        return Intent("system_info", {"kind": "battery"})
    if re.search(r"\b(am i (online|connected)|internet( connection)?( status)?|network( status)?|wi-?fi( status)?|"
                 r"connectivity|is the internet (up|working))\b", t) and not t.startswith(("open", "launch")):
        return Intent("system_info", {"kind": "network"})
    if re.search(r"\buptime\b|how long (has|have) (the |my )?(computer|system|pc|laptop|i) been (on|running|up)", t):
        return Intent("system_info", {"kind": "uptime"})

    # --- git
    if re.match(r"^(show |check |what('?s| is) (the )?)?git status\b|^(show )?status of (my )?(repo|repository)", t):
        return Intent("git_status", _repo_arg(raw))
    if re.match(r"^(show )?(git log|recent commits|commit history|last commits)\b", t):
        return Intent("git_log", _repo_arg(raw))
    if re.match(r"^(show |list )?(git )?branches\b|^git branch$", t):
        return Intent("git_branches", _repo_arg(raw))
    if re.match(r"^(show )?(git diff|uncommitted changes|what (did i|have i) change(d)?)\b", t):
        return Intent("git_diff", _repo_arg(raw))
    if m := re.match(r"^(?:git )?commit(?: (?:with|using))?(?: (?:the )?message)?[: ]+(.+)$", raw, re.I):
        msg = m.group(1).strip()
        args = {}
        pm = re.search(r"\s+in\s+((?:~|/)\S+)\s*$", msg)
        if pm:
            args["path"] = pm.group(1)
            msg = msg[:pm.start()]
        args["message"] = _clean(msg)
        return Intent("git_commit", args)
    if m := re.match(r"^(?:create|make|new)(?: a)?(?: new)?(?: git)? branch(?: called| named)? (\S+)(?: in ((?:~|/)\S+))?$", raw.strip(), re.I):
        return Intent("git_create_branch", {"name": _clean(m.group(1)), **({"path": m.group(2)} if m.group(2) else {})})
    if m := re.match(r"^(?:switch|checkout|change)(?: to)?(?: the)?(?: git)? branch (\S+)(?: in ((?:~|/)\S+))?$|"
                     r"^git (?:switch|checkout) (\S+)$", raw.strip(), re.I):
        name = m.group(1) or m.group(3)
        return Intent("git_switch_branch", {"name": _clean(name), **({"path": m.group(2)} if m.group(2) else {})})

    # --- tasks
    if m := re.match(r"^(?:add|create|new)(?: a)? (?:task|todo|to-do)[: ]+(.+)$", raw.strip(), re.I) or \
            re.match(r"^add (.+?) to (?:my )?(?:task|todo|to-do) ?list$", raw.strip(), re.I) or \
            re.match(r"^todo[: ]+(.+)$", raw.strip(), re.I):
        return Intent("add_task", {"title": _clean(m.group(1))})
    if re.fullmatch(r"(show|list|what are|view|see)( me)?( my)? (tasks|todos?|to-dos?|to-do list|todo list|task list)( list)?|"
                    r"(my )?(tasks|todos)", t):
        return Intent("list_tasks")
    if m := re.fullmatch(r"(?:complete|finish|done|mark done|tick off|check off)(?: task)? #?(\d+)|task #?(\d+) (?:is )?done", t):
        return Intent("complete_task", {"task_id": int(m.group(1) or m.group(2))})
    if m := re.fullmatch(r"(?:delete|remove) task #?(\d+)", t):
        return Intent("delete_task", {"task_id": int(m.group(1))})

    # --- reminders
    if t.startswith("remind me"):
        raw = words_to_numbers(raw)
        mins = parse_duration_minutes(raw)
        if mins is None:
            mins = parse_clock_minutes(raw)
        body = re.sub(r"^remind me\s*(to|about|that)?\s*", "", raw.strip(), flags=re.I)
        body = _strip_time(body)
        body = re.sub(r"^(to|about|that)\s+", "", body, flags=re.I)
        if mins is None:
            return Intent("ui:ask", {"text": "When should I remind you? Try 'remind me to stretch in 20 minutes' "
                                             "or 'remind me at 15:30 to call mum'."})
        return Intent("add_reminder", {"text": _clean(body) or "check in", "minutes": round(mins, 2)})
    if re.fullmatch(r"(show|list|what are)( my)? (reminders|upcoming reminders)|reminders", t):
        return Intent("list_reminders")
    if m := re.fullmatch(r"(?:cancel|delete|remove) reminder #?(\d+)", t):
        return Intent("cancel_reminder", {"reminder_id": int(m.group(1))})

    # --- memory
    if re.fullmatch(r"what do you (remember|know) about me|(show|list)( my)? memories|what do you remember", t):
        return Intent("list_memories")
    if m := re.fullmatch(r"forget (?:memory )?#?(\d+)", t):
        return Intent("forget", {"memory_id": int(m.group(1))})
    if m := re.match(r"^remember(?: that)?[: ]+(.+)$", raw.strip(), re.I):
        return Intent("remember", {"fact": _clean(m.group(1))})

    # --- files
    if m := re.match(r"^(?:find|search(?: for)?|locate|where is|where's)(?: (?:my|the|a))?(?: files?| folders?| director(?:y|ies))?"
                     r"(?: (?:called|named))? (.+?)(?: (?:in|under|inside) (?:my |the )?(" + _FOLDERS + r"|~\S*|/\S+)(?: folder)?)?$",
                     raw.strip(), re.I):
        q = _clean(m.group(1))
        if q and not re.fullmatch(r"(it|this|that|out)", q, re.I):
            args = {"query": q}
            if m.group(2):
                args["where"] = m.group(2)
            return Intent("search_files", args)
    if m := re.match(r"^(?:info|details|information|file info|show info)(?: (?:about|on|for))? ((?:~|/)\S.*)$", raw.strip(), re.I):
        return Intent("file_info", {"path": _clean(m.group(1))})
    if m := re.match(r"^(?:move|send|put) ((?:~|/)\S.*?) to (?:the )?(?:trash|bin)$|^(?:trash|delete|remove) (?:file )?((?:~|/)\S.*)$",
                     raw.strip(), re.I):
        return Intent("trash_file", {"path": _clean(m.group(1) or m.group(2))})

    # --- custom commands
    if m := re.match(r"^run (?:my )?(?:custom )?(?:command )?([a-z0-9 _\-]+)$", t):
        return Intent("run_custom", {"name": m.group(1).strip()})

    # --- open things (apps / folders / urls / paths)
    if m := re.match(r"^(?:open|launch|start|run|fire up|show)(?: up)?(?: me)?(?: the| my)? (.+?)(?: app| application| folder| directory)?$",
                     raw.strip(), re.I):
        target = _clean(m.group(1))
        if re.match(r"^https?://", target):
            return Intent("open_url", {"url": target})
        if re.fullmatch(_FOLDERS, target, re.I) or _PATHLIKE.match(target):
            return Intent("open_path", {"path": target})
        if re.fullmatch(r"(chat|chat window|conversation)", target, re.I):
            return Intent("ui:chat")
        return Intent("open_app", {"app": target})
    if m := re.match(r"^(?:go to|navigate to|take me to)(?: my| the)? (.+?)(?: folder| directory)?$", raw.strip(), re.I):
        return Intent("open_path", {"path": _clean(m.group(1))})

    return None
