"""Wake word, media control, disk cleanup, full-screen step-aside."""
import json
from pathlib import Path

import pytest

from nyxo.assistant import intents
from nyxo.security.policy import NeedsConfirmation, ValidationError
from nyxo.system import cleanup, media
from nyxo.voice import tts, wakeword

REAL_DATA = Path.home() / ".local/share"


@pytest.fixture
def real_models(monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(REAL_DATA))
    if not (wakeword.models_present() and wakeword.runtime_available() and tts.find_piper_model()):
        pytest.skip("wake-word or voice models not installed")


def _speech16k(text: str):
    import numpy as np
    pcm, sr = tts.piper_synth(tts.load_piper(tts.find_piper_model()), text, 1.0)
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    t = np.linspace(0, len(x) / sr, int(len(x) * 16000 / sr), endpoint=False)
    y = np.interp(t, np.arange(len(x)) / sr, x)
    noise = np.random.default_rng(3).normal(0, 50, 16000 * 2)
    return np.concatenate([noise, y, noise]).astype(np.int16)


def test_wake_word_detector(real_models):
    det = wakeword.WakeWordDetector()

    def best(text):
        det.reset()
        a = _speech16k(text)
        return max(det.process(a[i:i + 1280]) for i in range(0, len(a) - 1280, 1280))
    assert best("Hey Jarvis") > 0.8
    assert best("Hello there, the weather is lovely today.") < 0.2
    assert best("Hey Travis") < 0.5


def test_wake_listener_end_to_end(real_models, qapp, qtbot, tmp_path, monkeypatch):
    """Full loop on a simulated microphone: wake phrase, then a command."""
    import numpy as np
    from nyxo.voice import stt
    from nyxo.core.config import DEFAULTS
    audio = np.concatenate([_speech16k("Hey Jarvis"), _speech16k("what time is it")])
    raw = tmp_path / "mic.raw"
    raw.write_bytes(audio.tobytes())
    monkeypatch.setattr(stt, "recorder_argv", lambda: ["cat", str(raw)])
    monkeypatch.setattr(wakeword.policy, "check_binary", lambda b: b)
    cfg = dict(DEFAULTS, wake_engine="hey_jarvis")
    w = wakeword.WakeListener(lambda k: cfg[k])
    woke, final = [], []
    w.woke.connect(lambda: woke.append(True))
    w.final.connect(final.append)
    assert w.start()
    qtbot.waitUntil(lambda: bool(final), timeout=30000)
    w.stop()
    assert woke
    assert "time" in final[0]


def test_strip_wake_words():
    assert wakeword.strip_wake_words("hey jarvis what time is it") == "what time is it"
    assert wakeword.strip_wake_words("jarvis, open firefox") == "open firefox"
    assert wakeword.strip_wake_words("open firefox") == "open firefox"


def test_media_parsing(monkeypatch):
    from nyxo.system.runner import RunResult
    calls = []

    def fake(*args, timeout=4):
        calls.append(args)
        if args[0] == "list":
            return RunResult(0, json.dumps([{"name": ":1.2"}, {"name": "org.mpris.MediaPlayer2.spotify"}]), "")
        if args[0] == "get-property":
            prop = args[4]
            data = {"PlaybackStatus": "Playing", "Identity": "Spotify",
                    "Metadata": {"xesam:title": {"type": "s", "data": "Clocks"},
                                 "xesam:artist": {"type": "as", "data": ["Coldplay"]}}}[prop]
            return RunResult(0, json.dumps({"data": data}), "")
        return RunResult(0, "", "")
    monkeypatch.setattr(media, "_busctl", fake)
    assert media.now_playing() == "Spotify is playing Clocks by Coldplay."
    assert media.control("toggle") == "Paused Spotify."         # toggle on a playing player = pause
    assert calls[-1][-1] == "Pause"
    assert media.control("next").startswith("Skipped")
    with pytest.raises(ValidationError):
        media.control("rm -rf")
    with pytest.raises(ValidationError):
        media._prop("evil; name", "x", "y")


def test_media_no_players(monkeypatch):
    from nyxo.system.runner import RunResult
    monkeypatch.setattr(media, "_busctl", lambda *a, **k: RunResult(0, "[]", ""))
    assert "No media player" in media.control("pause")


def test_cleanup_scan_and_clean(tmp_path, monkeypatch, db):
    home = tmp_path / "home"
    cache = home / ".cache" / "pip"
    (cache / "wheels").mkdir(parents=True)
    (cache / "wheels" / "big.whl").write_bytes(b"x" * 3_000_000)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious.txt").write_text("keep me")
    monkeypatch.setattr(cleanup, "H", home)
    monkeypatch.setattr(cleanup, "TARGETS", {
        "pip": cleanup.Target("pip", "pip download cache", cache, "re-downloaded"),
        "evil": cleanup.Target("evil", "evil", home / ".cache/evil", "x")})
    (home / ".cache" / "evil").symlink_to(outside)
    assert "pip download cache" in cleanup.report()
    from nyxo.system.actions import ActionRegistry
    reg = ActionRegistry(db)
    with pytest.raises(NeedsConfirmation) as ei:
        reg.execute("cleanup", {"target": "pip"})
    assert "pip download cache" in ei.value.explanation
    r = reg.execute("cleanup", {"target": "pip"}, approved=True)
    assert r.ok and "freed 2.9 MB" in r.message
    assert cache.exists() and not any(cache.iterdir())
    bad = reg.execute("cleanup", {"target": "evil"}, approved=True)
    assert not bad.ok and (outside / "precious.txt").exists()      # symlink escape refused
    assert not reg.execute("cleanup", {"target": "/etc"}, approved=True).ok


@pytest.mark.parametrize("text,action,args", [
    ("pause the music", "media_control", {"command": "pause"}),
    ("next track", "media_control", {"command": "next"}),
    ("what's playing", "now_playing", {}),
    ("free up some disk space", "cleanup_scan", {}),
    ("empty the trash", "cleanup", {"target": "trash"}),
    ("clear the browser cache", "cleanup", {"target": "chromium-cache"}),
])
def test_intents(text, action, args):
    it = intents.parse(text)
    assert it.action == action and it.args == args


def test_presence_and_step_aside(qapp, qtbot, tmp_path):
    from nyxo.core.app import NyxoApp
    from nyxo.memory.db import Database
    db = Database(tmp_path / "p.db")
    for k, v in (("ai_provider", "offline"), ("first_run_done", True), ("tts_mode", "never"),
                 ("wake_word", False), ("briefing_on_start", False)):
        db.set_setting(k, v)
    app = NyxoApp(qapp, db, native_wayland=False)
    app.start()
    p = app.presence
    assert p.evaluate(None, True) == (False, "")      # media signal is off by default
    app.settings.set("auto_hide_media", True)
    assert p.evaluate(True, False) == (True, "fullscreen")
    assert p.evaluate(None, True) == (True, "media")
    assert p.evaluate(False, False) == (False, "")
    app.settings.set("auto_hide_media", False)
    assert p.evaluate(None, True) == (False, "")
    c = app.companion
    app._on_step_aside(True, "fullscreen")
    qtbot.waitUntil(lambda: not c.isVisible(), timeout=3000)
    c._brain()                                   # watchdog must not fight the step-aside
    assert not c.isVisible()
    app.notif_watch.recent.update({"Slack": 1})
    app._on_step_aside(False, "")
    qtbot.waitUntil(lambda: c.isVisible() and c.windowOpacity() > 0.95, timeout=3000)
    app.shutdown()
    db.close()
