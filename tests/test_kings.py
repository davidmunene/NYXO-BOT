"""Kings: name wake word (two-stage), Whisper transcription, grounding,
friendly errors, web intents, Ollama runtime check, portrait avatar."""
import os
from pathlib import Path

import pytest

from nyxo.assistant import intents
from nyxo.assistant.assistant import friendly_ai_error
from nyxo.core import brand
from nyxo.system import knowledge
from nyxo.voice import tts, wakeword, whisper_stt

REAL_DATA = Path.home() / ".local/share"


@pytest.fixture
def real_models(monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(REAL_DATA))
    from nyxo.voice import stt
    if not (stt.find_model() and tts.find_piper_model()):
        pytest.skip("speech models not installed")


def _speech(text):
    import numpy as np
    pcm, sr = tts.piper_synth(tts.load_piper(tts.find_piper_model()), text, 1.0)
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    t = np.linspace(0, len(x) / sr, int(len(x) * 16000 / sr), endpoint=False)
    y = np.interp(t, np.arange(len(x)) / sr, x)
    pad = np.random.default_rng(5).normal(0, 50, 16000)
    return np.concatenate([pad, y, pad, pad]).astype(np.int16)


def _run_loop(qtbot, tmp_path, monkeypatch, phrases, wait_ms=30000, expect=True):
    import numpy as np
    from nyxo.core.config import DEFAULTS
    from nyxo.voice import stt
    raw = tmp_path / "mic.raw"
    raw.write_bytes(np.concatenate([_speech(p) for p in phrases]).tobytes())
    monkeypatch.setattr(stt, "recorder_argv", lambda: ["cat", str(raw)])
    monkeypatch.setattr(wakeword.policy, "check_binary", lambda b: b)
    cfg = dict(DEFAULTS, wake_engine="name")
    w = wakeword.WakeListener(lambda k: cfg[k])
    woke, final = [], []
    w.woke.connect(lambda: woke.append(1))
    w.final.connect(final.append)
    assert w.start()
    if expect:
        qtbot.waitUntil(lambda: bool(final), timeout=wait_ms)
    else:
        qtbot.waitUntil(lambda: not w.active() or w._proc is None or w._proc.poll() is not None, timeout=wait_ms)
        qtbot.wait(1500)
    w.stop()
    return woke, final


def test_name_wake_one_breath(real_models, qapp, qtbot, tmp_path, monkeypatch):
    woke, final = _run_loop(qtbot, tmp_path, monkeypatch, ["Kings, play music from YouTube."])
    assert woke and final[0].lower().rstrip(".") == "play music from youtube"
    assert intents.parse(final[0]).action == "open_url"


def test_name_wake_then_command(real_models, qapp, qtbot, tmp_path, monkeypatch):
    woke, final = _run_loop(qtbot, tmp_path, monkeypatch, ["Hey Kings.", "What's using my disk space?"])
    assert woke and "disk space" in final[0].lower()


def test_name_wake_rejects_lookalikes(real_models, qapp, qtbot, tmp_path, monkeypatch):
    woke, final = _run_loop(qtbot, tmp_path, monkeypatch,
                            ["The king is in his castle.", "Kingdom of heaven.", "I like kings and queens."],
                            expect=False)
    assert woke == [] and final == []


@pytest.mark.skipif(not whisper_stt.installed(), reason="faster-whisper missing")
def test_whisper_transcribes(real_models):
    if not whisper_stt.available_sizes():
        pytest.skip("no whisper model")
    out = whisper_stt.transcribe(_speech("Remind me to call the bank in twenty minutes.").tobytes(), "Kings")
    assert "call the bank" in out.lower() and ("twenty" in out.lower() or "20" in out)
    it = intents.parse(out)
    assert it.action == "add_reminder" and it.args["minutes"] == 20


def test_wake_regex_variants():
    assert brand.NAME == "Kings"
    for t, rest in [("Kings, open Firefox.", "open Firefox."), ("hey kings what time is it", "what time is it"),
                    ("OK King's pause", "pause"), ("Kings", "")]:
        assert wakeword.is_addressed(t) and wakeword.strip_wake_words(t) == rest
    for t in ("the king is here", "kingdom come", "i like kings"):
        assert not wakeword.is_addressed(t)


def test_knowledge_topics():
    assert knowledge.topic_of("who is the president of Kenya?") == "the president of Kenya"
    assert knowledge.topic_of("Kings, tell me about Nairobi") == "Nairobi"
    assert knowledge.topic_of("what is quantum computing") == "quantum computing"
    for q in ("what is the time", "who are you", "what is my ip", "what is using my disk"):
        assert knowledge.topic_of(q) is None


def test_friendly_errors():
    raw = 'HTTP 500: {"error":"error starting llama-server: llama-server binary not found (checked: ...)"}'
    msg = friendly_ai_error(raw)
    assert "Ollama" in msg and "{" not in msg
    assert "{" not in friendly_ai_error('HTTP 500: {"error": "weird"}')


@pytest.mark.parametrize("text,url", [
    ("play music from youtube", "https://music.youtube.com/"),
    ("play lofi beats on youtube", "https://www.youtube.com/results?search_query=lofi+beats"),
    ("open youtube", "https://www.youtube.com/"),
    ("search the web for ubuntu news", "https://www.google.com/search?q=ubuntu+news"),
])
def test_web_intents(text, url):
    it = intents.parse(text)
    assert it.action == "open_url" and it.args["url"] == url


def test_ollama_runtime_check(tmp_path, monkeypatch):
    from nyxo.ai.ollama import OllamaProvider
    bindir = tmp_path / "bin"
    libdir = tmp_path / "lib" / "ollama"
    bindir.mkdir()
    libdir.mkdir(parents=True)
    exe = bindir / "ollama"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setattr(OllamaProvider, "USER_BIN", str(exe))
    assert OllamaProvider.binary() == str(exe)
    assert not OllamaProvider.runtime_ok()             # like the broken /usr/local install
    (libdir / "llama-server").write_text("")
    assert OllamaProvider.runtime_ok()


SHEET = Path.home() / "Downloads" / "kings.jpeg"


@pytest.mark.skipif(not SHEET.exists(), reason="character sheet not present")
def test_portrait_avatar_build(qapp, tmp_path):
    from nyxo.character import portrait
    from nyxo.character.sprites import SpriteBank
    from nyxo.character.states import State
    out = portrait.build(SHEET, tmp_path / "kings", log=lambda *a: None)
    bank = SpriteBank(out / "animations", 160)
    for st in State:
        frames = bank.frames(st)
        assert frames and frames[0].width() == 160
    assert (out / "icons" / "icon-256.png").exists()
    # faces differ: the talking frames really open the mouth
    t0 = bank.frames(State.TALKING)[0].toImage()
    t7 = bank.frames(State.TALKING)[7].toImage()
    assert t0 != t7


def test_verified_recipes():
    from nyxo.assistant.recipes import match
    a = match("How do I undo my last git commit but keep the changes?")
    assert "git reset --soft HEAD~1" in a and "--hard" not in a
    assert "git branch -m" in match("how do i rename a branch")
    assert match("what is the capital of france") is None
    assert match("tell me a joke") is None


def test_static_prompt_is_stable():
    """The cached part of the prompt must not change between turns."""
    import time
    from nyxo.assistant import personality
    a = personality.system_prompt("jarvis", "short", "", [], "open_app(app)", False, spoken=True)
    time.sleep(1.1)
    b = personality.system_prompt("jarvis", "short", "", [], "open_app(app)", False, spoken=True)
    assert a == b and len(a) < 2500
    note = personality.context_note("David", ["likes tea"], False, {"title": "X", "extract": "Y"})
    assert "David" in note and "likes tea" in note and "Wikipedia" in note
