"""Text-to-speech.

Engines, best first (\"auto\" picks the first that works):
  * piper  — neural voice, runs locally in-process (default voice: British
             male en_GB-alan-medium). Audio is streamed sentence by sentence
             and a loudness level is emitted in time with playback so the
             character's mouth is lip-synced to the real audio.
  * espeak-ng / espeak / spd-say — lightweight robotic fallbacks.
Playback is interruptible at any time."""
from __future__ import annotations

import array
import json
import logging
import math
import queue
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..core import paths
from ..security import policy

log = logging.getLogger("nyxo.tts")

# Preference order when auto-selecting a Piper voice (JARVIS-like first).
PREFERRED_VOICES = ["en_GB-alan-medium", "en_GB-northern_english_male-medium", "en_US-ryan-high",
                    "en_GB-alan-low", "en_US-ryan-medium", "en_US-joe-medium", "en_GB-alba-medium",
                    "en_US-lessac-medium", "en_US-amy-medium"]
DEFAULT_VOICE_URL = ("https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/"
                     "en_GB-alan-medium.onnx")


def clean_for_speech(text: str, limit: int = 700) -> str:
    text = re.sub(r"```.*?```", " I've put the code on screen. ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"https?://\S+", "a link", text)
    text = re.sub(r"(?m)^\s*[-*•]\s+", "", text)
    text = re.sub(r"(?m)^\s*#+\s*", "", text)
    text = re.sub(r"[*_#>|]+", " ", text)
    text = re.sub(r"[\U0001F000-\U0001FFFF☀-➿️]", "", text)
    text = text.replace("~/", "home folder ").replace(" & ", " and ")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(". ", 1)[0] + "."
    return text


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?:;])\s+", text)
    out, buf = [], ""
    for s in parts:
        buf = f"{buf} {s}".strip()
        if len(buf) > 25:
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return out


def piper_installed() -> bool:
    try:
        import piper  # noqa: F401
        return True
    except ImportError:
        return False


def piper_voices() -> list[Path]:
    d = paths.models_dir() / "piper"
    if not d.exists():
        return []
    found = [p for p in d.glob("*.onnx") if Path(str(p) + ".json").exists()]
    rank = {n: i for i, n in enumerate(PREFERRED_VOICES)}
    return sorted(found, key=lambda p: (rank.get(p.stem, 99), p.stem))


def find_piper_model(configured: str = "") -> Path | None:
    if configured:
        p = Path(configured).expanduser()
        if p.exists() and Path(str(p) + ".json").exists():
            return p
    v = piper_voices()
    return v[0] if v else None


def player_argv(rate: int) -> list[str] | None:
    if shutil.which("aplay"):
        return ["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-c", "1", "-r", str(rate),
                "--buffer-time=150000", "-"]
    if shutil.which("pw-play"):
        return ["pw-play", "--rate", str(rate), "--channels", "1", "--format", "s16", "-"]
    if shutil.which("paplay"):
        return ["paplay", "--raw", f"--rate={rate}", "--format=s16le", "--channels=1"]
    return None


def available_engines() -> list[str]:
    out = []
    if piper_installed() and find_piper_model() and player_argv(22050):
        out.append("piper")
    for e in ("espeak-ng", "espeak", "spd-say"):
        if shutil.which(e):
            out.append(e)
    return out


def rms_levels(pcm: bytes, rate: int, frame_s: float = 0.04) -> list[float]:
    """Loudness 0..1 per frame (used for lip-sync)."""
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    n = max(1, int(rate * frame_s))
    out = []
    for i in range(0, len(samples), n):
        chunk = samples[i:i + n]
        if not chunk:
            break
        rms = math.sqrt(sum(s * s for s in chunk[::4]) / max(1, len(chunk[::4])))
        out.append(min(1.0, (rms / 9000.0) ** 0.9))
    return out


_voice_cache: dict[str, object] = {}
_voice_lock = threading.Lock()


def load_piper(model: Path):
    with _voice_lock:
        v = _voice_cache.get(str(model))
        if v is None:
            from piper import PiperVoice
            log.info("loading piper voice %s", model.name)
            v = PiperVoice.load(str(model))
            _voice_cache.clear()
            _voice_cache[str(model)] = v
        return v


def piper_synth(voice, text: str, length_scale: float) -> tuple[bytes, int]:
    """Synthesize with piper-tts >= 1.3 (or the older 1.2 streaming API)."""
    try:
        from piper import SynthesisConfig
        cfg = SynthesisConfig(length_scale=length_scale, noise_scale=0.6, noise_w_scale=0.75)
        chunks = list(voice.synthesize(text, syn_config=cfg))
        if not chunks:
            return b"", 22050
        return b"".join(c.audio_int16_bytes for c in chunks), chunks[0].sample_rate
    except ImportError:
        rate = voice.config.sample_rate
        return b"".join(voice.synthesize_stream_raw(text, length_scale=length_scale)), rate


class Speaker(QObject):
    started = Signal()
    finished = Signal()
    level = Signal(float)       # live loudness for lip-sync (piper only)

    def __init__(self, get):
        super().__init__()
        self._get = get
        self._procs: list[subprocess.Popen] = []
        self._lock = threading.Lock()
        self._gen = 0
        self._q: queue.Queue = queue.Queue()
        self._busy = False
        threading.Thread(target=self._worker, daemon=True).start()

    def engine(self) -> str | None:
        pref = self._get("tts_engine")
        engines = available_engines()
        if pref == "espeak" and pref not in engines and "espeak-ng" in engines:
            pref = "espeak-ng"
        if pref in engines:
            return pref
        return engines[0] if engines else None

    def voice_label(self) -> str:
        eng = self.engine()
        if eng == "piper":
            m = find_piper_model(str(self._get("piper_model")))
            return f"Piper neural · {m.stem if m else '?'}"
        return eng or "none"

    def speaking(self) -> bool:
        with self._lock:
            return self._busy or any(p.poll() is None for p in self._procs)

    def stop(self) -> None:
        with self._lock:
            self._gen += 1
            while not self._q.empty():
                try:
                    self._q.get_nowait()
                except queue.Empty:
                    break
            for p in self._procs:
                if p.poll() is None:
                    p.kill()
            self._procs.clear()

    def speak(self, text: str) -> bool:
        if self._get("tts_mode") == "never":
            return False
        text = clean_for_speech(text)
        eng = self.engine()
        if not text or not eng:
            return False
        self.stop()
        return self.say_more(text, _cleaned=True)

    def say_more(self, text: str, _cleaned: bool = False) -> bool:
        """Queue text after whatever is being said (used to speak an AI answer
        sentence by sentence while it is still being generated)."""
        if self._get("tts_mode") == "never":
            return False
        text = text if _cleaned else clean_for_speech(text)
        if not text or not self.engine():
            return False
        with self._lock:
            self._q.put((self._gen, text))
        return True

    def _worker(self) -> None:
        while True:
            gen, text = self._q.get()
            if not self._alive(gen):
                continue
            first = not self._busy
            self._busy = True
            if first:
                self.started.emit()
            self._run(self.engine(), text, gen, signals=False)
            if self._q.empty() or not self._alive(gen):
                self._busy = False
                self.level.emit(-1.0)
                self.finished.emit()

    def warm_up(self) -> None:
        """Load the neural voice in the background so the first reply is quick."""
        if self.engine() == "piper":
            m = find_piper_model(str(self._get("piper_model")))
            threading.Thread(target=lambda: load_piper(m), daemon=True).start()

    def _alive(self, gen: int) -> bool:
        with self._lock:
            return gen == self._gen

    def _popen(self, argv, **kw) -> subprocess.Popen:
        policy.check_binary(argv[0])
        p = subprocess.Popen(argv, stderr=subprocess.DEVNULL, start_new_session=True, **kw)
        with self._lock:
            self._procs.append(p)
        return p

    def _run(self, eng: str, text: str, gen: int, signals: bool = True) -> None:
        rate = int(self._get("tts_rate"))
        if signals:
            self.started.emit()
        try:
            if eng == "piper":
                self._run_piper(text, rate, gen)
            elif eng == "spd-say":
                p = self._popen(["spd-say", "-w", "-r", str(int((rate - 165) / 2)), "--", text],
                                stdout=subprocess.DEVNULL)
                p.wait()
            else:
                voice = str(self._get("tts_voice") or "en-gb")
                p = self._popen([eng, "-s", str(rate), "-v", voice, "--stdin"],
                                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
                p.stdin.write(text.encode())
                p.stdin.close()
                p.wait()
        except Exception as e:  # noqa: BLE001
            log.warning("tts failed (%s): %s", eng, e)
        finally:
            if signals:
                self.level.emit(-1.0)
                self.finished.emit()

    def _run_piper(self, text: str, rate_wpm: int, gen: int) -> None:
        model = find_piper_model(str(self._get("piper_model")))
        voice = load_piper(model)
        length = max(0.6, min(1.8, 165 / max(80, rate_wpm)))
        sample_rate = json.loads(Path(str(model) + ".json").read_text()).get("audio", {}).get("sample_rate", 22050)
        player = self._popen(player_argv(sample_rate), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
        frame_s = 0.04
        latency = 0.12                 # approx. device buffer
        t0 = None
        played = 0.0
        try:
            for sentence in split_sentences(text):
                if not self._alive(gen):
                    return
                pcm, sr = piper_synth(voice, sentence, length)
                if not pcm:
                    continue
                pcm += b"\x00\x00" * int(sr * 0.12)          # natural pause between sentences
                levels = rms_levels(pcm, sr, frame_s)
                step = int(sr * frame_s) * 2
                if t0 is None:
                    t0 = time.monotonic() + latency
                elif time.monotonic() > t0 + played:          # synthesis fell behind playback
                    t0 = time.monotonic() - played + latency
                for i in range(0, len(pcm), step):
                    if not self._alive(gen):
                        return
                    player.stdin.write(pcm[i:i + step])
                    player.stdin.flush()
                    # emit this frame's level when it's actually audible
                    due = t0 + played
                    delay = due - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
                    k = i // step
                    self.level.emit(levels[k] if k < len(levels) else 0.0)
                    played += frame_s
            player.stdin.close()
            player.wait(timeout=5)
        except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
            pass
