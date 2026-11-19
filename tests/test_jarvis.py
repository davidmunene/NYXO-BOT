"""JARVIS features: briefing, proactive alerts, speech helpers, personality."""
from collections import namedtuple
from datetime import datetime

import psutil
import pytest

from nyxo.assistant import briefing, intents, personality
from nyxo.system.watcher import SystemWatcher
from nyxo.voice import tts

Batt = namedtuple("Batt", "percent secsleft power_plugged")


def test_briefing_mentions_essentials(db):
    db.add_task("ship NYXO")
    db.add_reminder("stand-up meeting", datetime.now().timestamp() + 3600)
    text = briefing.build(db, "David")
    assert text.startswith("Good ") and "David" in text
    assert "1 open task" in text and "ship NYXO" in text
    assert "stand-up meeting" in text
    assert "percent" in text or psutil.sensors_battery() is None


def test_watcher_alerts(qapp, monkeypatch):
    w = SystemWatcher()
    state = {"b": Batt(50, 3600, True), "online": True}
    monkeypatch.setattr(psutil, "sensors_battery", lambda: state["b"])
    Disk = namedtuple("Disk", "total used free percent")
    monkeypatch.setattr(psutil, "disk_usage", lambda p: Disk(100, 50, 50, 50.0))   # real disk may be full
    assert w.check(lambda: state["online"]) == []                 # first sample: no alerts
    state["b"] = Batt(48, 3600, False)
    assert "battery" in w.check(lambda: True)[0][0].lower()        # unplugged
    state["b"] = Batt(18, 600, False)
    out = w.check(lambda: True)
    assert any("18 percent" in t for t, _ in out)
    assert w.check(lambda: True) == []                             # no repeat at same level
    state["b"] = Batt(4, 60, False)
    assert any("critical" in t.lower() for t, _ in w.check(lambda: True))
    assert "lost" in w.check(lambda: False)[0][0]
    assert "restored" in w.check(lambda: True)[0][0]


def test_speech_cleanup_and_split():
    t = tts.clean_for_speech("**Done!** 🎉 Run `git status`:\n```bash\ngit status\n```\nSee https://x.com")
    assert "```" not in t and "*" not in t and "🎉" not in t and "https" not in t
    assert "code on screen" in t
    parts = tts.split_sentences("Hello there. This is NYXO speaking today. Ok! And one more sentence here.")
    assert len(parts) >= 2 and all(p for p in parts)


def test_rms_levels():
    import array
    import math
    tone = array.array("h", (int(12000 * math.sin(i / 8)) for i in range(22050)))
    silent = bytes(44100)
    loud = tts.rms_levels(tone.tobytes(), 22050)
    quiet = tts.rms_levels(silent, 22050)
    assert len(loud) == 25 and min(loud) > 0.5
    assert max(quiet) == 0.0


@pytest.mark.skipif(not (tts.piper_installed() and tts.find_piper_model()), reason="piper voice not installed")
def test_piper_voice_autoselected_and_synthesizes(monkeypatch):
    from pathlib import Path
    monkeypatch.setenv("XDG_DATA_HOME", str(Path.home() / ".local/share"))   # the real voice
    assert tts.available_engines()[0] == "piper"
    m = tts.find_piper_model()
    assert m.stem == tts.PREFERRED_VOICES[0] or m.stem in tts.PREFERRED_VOICES
    pcm, sr = tts.piper_synth(tts.load_piper(m), "Systems online.", 1.0)
    assert sr > 8000 and len(pcm) / 2 / sr > 0.4


def test_jarvis_personality():
    from nyxo.core.config import DEFAULTS
    assert DEFAULTS["personality"] == "jarvis" and DEFAULTS["tts_mode"] == "always"
    g = personality.greeting("jarvis", "David")
    assert g.startswith("Good ") and "David" in g
    sp = personality.system_prompt("jarvis", "short", "", [], None, False, spoken=True)
    assert "butler" in sp and "read aloud" in sp
    assert intents.parse("status report").action == "ui:briefing"
    assert intents.parse("give me a briefing").action == "ui:briefing"
