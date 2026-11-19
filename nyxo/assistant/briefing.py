"""JARVIS-style spoken status briefing."""
from __future__ import annotations

from datetime import datetime

import psutil

from ..memory.db import Database
from . import personality


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def build(db: Database, name: str = "") -> str:
    now = datetime.now()
    who = f", {name}" if name else ""
    parts = [f"Good {personality.part_of_day()}{who}.",
             f"It's {now.strftime('%H:%M')} on {now.strftime('%A')} the {_ordinal(now.day)} of {now.strftime('%B')}."]
    b = psutil.sensors_battery()
    if b is not None:
        state = "and charging" if b.power_plugged else "on battery"
        parts.append(f"Battery is at {b.percent:.0f} percent {state}.")
    mem = psutil.virtual_memory().percent
    try:
        disk = psutil.disk_usage("/").percent
    except OSError:
        disk = 0
    warnings = []
    if mem > 85:
        warnings.append(f"memory is at {mem:.0f} percent")
    if disk > 90:
        warnings.append(f"the system drive is {disk:.0f} percent full")
    tasks = db.list_tasks()
    if tasks:
        parts.append(f"You have {len(tasks)} open task{'s' if len(tasks) != 1 else ''}"
                     f", starting with: {tasks[0]['title']}.")
    rem = db.pending_reminders()
    if rem:
        r = rem[0]
        when = datetime.fromtimestamp(r["due"])
        day = "" if when.date() == now.date() else f" on {when.strftime('%A')}"
        parts.append(f"Your next reminder is at {when.strftime('%H:%M')}{day}: {r['text']}.")
    if warnings:
        parts.append("One note: " + " and ".join(warnings) + ".")
    else:
        parts.append("All systems nominal.")
    return " ".join(parts)
