"""Hands-free wake word ("Hey Jarvis") + command capture.

Wake-word detection is a dependency-light port of openWakeWord's streaming
pipeline (Apache-2.0 models by dscripka/openWakeWord):
    16 kHz int16 audio, 80 ms chunks
      -> melspectrogram.onnx   (x/10 + 2)
      -> embedding_model.onnx  (76-frame windows -> 96-d embedding)
      -> hey_jarvis_v0.1.onnx  (last 16 embeddings -> score 0..1)
It runs on onnxruntime (already used by the Piper voice), about 1–3 % CPU.

Privacy: when enabled the microphone stream is analysed locally and
continuously, but nothing is recorded or stored; only the words spoken
*after* the wake word are transcribed (by Vosk, offline)."""
from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..core import paths
from ..security import policy
from ..core import brand
from . import stt, whisper_stt

log = logging.getLogger("nyxo.wake")

CHUNK = 1280                     # 80 ms @ 16 kHz
MODEL_FILES = ("melspectrogram.onnx", "embedding_model.onnx", "hey_jarvis_v0.1.onnx")
MODEL_URL = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"
WAKE_PHRASE = "Hey Jarvis"


def model_dir() -> Path:
    return paths.models_dir() / "wakeword"


def models_present() -> bool:
    return all((model_dir() / f).exists() for f in MODEL_FILES)


def runtime_available() -> bool:
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
        return True
    except ImportError:
        return False


def status() -> tuple[bool, str]:
    if not runtime_available():
        return False, "onnxruntime/numpy missing (re-run scripts/install.sh)."
    if not models_present():
        return False, "Wake-word models not downloaded (Settings → Voice)."
    ok, detail = stt.status()
    if not ok:
        return False, detail
    return True, f"Say “{WAKE_PHRASE}” followed by your request."


class WakeWordDetector:
    def __init__(self, directory: Path | None = None, threshold: float = 0.5):
        import numpy as np
        import onnxruntime as ort
        self.np = np
        d = directory or model_dir()
        so = ort.SessionOptions()
        so.intra_op_num_threads = 1
        so.inter_op_num_threads = 1
        prov = ["CPUExecutionProvider"]
        self.mel = ort.InferenceSession(str(d / MODEL_FILES[0]), so, providers=prov)
        self.emb = ort.InferenceSession(str(d / MODEL_FILES[1]), so, providers=prov)
        self.ww = ort.InferenceSession(str(d / MODEL_FILES[2]), so, providers=prov)
        self.mel_in = self.mel.get_inputs()[0].name
        self.emb_in = self.emb.get_inputs()[0].name
        self.ww_in = self.ww.get_inputs()[0].name
        self.n_feat = int(self.ww.get_inputs()[0].shape[1] or 16)
        self.threshold = threshold
        self.reset()

    def reset(self) -> None:
        np = self.np
        self.tail = np.zeros(CHUNK + 160 * 3, dtype=np.float32)   # only what the mel model needs
        self.mel_buf = np.ones((76, 32), dtype=np.float32)
        self.features = np.zeros((0, 96), dtype=np.float32)
        self.n_pred = 0
        self._quiet = 0
        self.skipped = 0
        # warm the feature buffer with quiet noise, as openWakeWord does
        rng = np.random.default_rng(0)
        for _ in range(int(16000 * 2 / CHUNK)):
            self._push(rng.integers(-300, 300, CHUNK).astype(np.int16), predict=False)
        self.n_pred = 0

    def _push(self, chunk, predict: bool = True) -> float:
        np = self.np
        self.tail = np.concatenate((self.tail, chunk.astype(np.float32)))[-(CHUNK + 160 * 3):]
        x = self.tail[None, :]
        spec = np.squeeze(self.mel.run(None, {self.mel_in: x})[0]) / 10.0 + 2.0
        self.mel_buf = np.vstack((self.mel_buf, spec))[-970:]
        win = self.mel_buf[-76:].astype(np.float32)[None, :, :, None]
        e = np.squeeze(self.emb.run(None, {self.emb_in: win})[0]).reshape(1, 96)
        self.features = np.vstack((self.features, e))[-120:]
        if not predict or self.features.shape[0] < self.n_feat:
            return 0.0
        score = float(np.squeeze(self.ww.run(None, {self.ww_in: self.features[-self.n_feat:][None]})[0]))
        self.n_pred += 1
        return score if self.n_pred > 5 else 0.0     # openWakeWord ignores the first 5

    QUIET_RMS = 120.0

    def process(self, chunk) -> float:
        """Feed exactly 1280 int16 samples; returns the wake score 0..1.
        In a quiet room the neural models are skipped (saves most of the CPU);
        the last ~1.5 s of audio is kept so detection resumes instantly."""
        np = self.np
        rms = float(np.sqrt(np.mean(np.square(chunk, dtype=np.float32))))
        if rms < self.QUIET_RMS:
            self._quiet += 1
            if self._quiet > 20:                     # >1.6 s of silence: idle cheaply
                self.tail = np.concatenate((self.tail, chunk.astype(np.float32)))[-(CHUNK + 160 * 3):]
                self.skipped += 1
                return 0.0
        else:
            self._quiet = 0
        return self._push(chunk)


_PREFIX = r"(?:(?:hey|hi|okay|ok|yo)\s+)?"


def _name_pattern() -> str:
    n = re.escape(brand.NAME.lower())
    alts = [n]
    if n.endswith("s"):
        alts += [n[:-1], n[:-1] + "'s"]          # kings / king / king's
    alts.sort(key=len, reverse=True)
    return "(?:" + "|".join(alts) + "|jarvis)"


def wake_regex() -> re.Pattern:
    return re.compile(r"^\s*" + _PREFIX + _name_pattern() + r"\b[\s,.!?:;-]*(.*)$", re.I | re.S)


def strip_wake_words(text: str) -> str:
    m = wake_regex().match(text or "")
    return (m.group(1) if m else (text or "")).strip()


def is_addressed(text: str) -> bool:
    """True if the utterance *starts* with the assistant's name (optionally 'hey …')."""
    return bool(wake_regex().match(text or ""))


class WakeListener(QObject):
    """Continuous voice loop.

    engine "name" (default): call the assistant by name — e.g. “Kings, open
      Firefox”. Stage 1: Vosk spots the name with a tiny grammar (~4 % CPU).
      Stage 2: the utterance is re-checked with the full recogniser and only
      accepted if it *starts* with the name (rejects "the king is…",
      "kingdom", "I like kings…").
    engine "hey_jarvis": openWakeWord "Hey Jarvis" model.
    The command itself is transcribed by Whisper when available (else Vosk)."""
    woke = Signal()
    partial = Signal(str)
    final = Signal(str)
    listening = Signal(bool)
    error = Signal(str)

    MAX_UTT_S = 15

    def __init__(self, get, is_speaking=lambda: False):
        super().__init__()
        self._get = get
        self._is_speaking = is_speaking
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None
        self.suspended = False
        self.last_score = 0.0

    def engine(self) -> str:
        e = str(self._get("wake_engine") or "name")
        return e if e in ("name", "hey_jarvis") else "name"

    def phrase(self) -> str:
        return brand.NAME if self.engine() == "name" else WAKE_PHRASE

    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        if self.active():
            return True
        ok, detail = (stt.status() if self.engine() == "name" else status())
        if not ok:
            self.error.emit(detail)
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    # -- helpers ------------------------------------------------------------
    def _transcribe(self, audio: bytes, fallback: str) -> str:
        try:
            t = whisper_stt.transcribe(audio, brand.NAME, str(self._get("stt_model") or "auto"))
        except Exception as e:  # noqa: BLE001
            log.warning("whisper failed, using vosk text: %s", e)
            t = None
        return fallback if t is None else t

    def _ready(self, now: float, cooldown: float) -> bool:
        return now > cooldown and not self.suspended and not self._is_speaking()

    # -- main loop ----------------------------------------------------------
    def _run(self) -> None:
        import numpy as np
        from vosk import KaldiRecognizer
        engine = self.engine()
        try:
            model = stt.load_model(stt.find_model(str(self._get("vosk_model"))))
            det = WakeWordDetector(threshold=float(self._get("wake_sensitivity"))) \
                if engine == "hey_jarvis" else None
            if engine == "name" and str(self._get("stt_model") or "auto") != "off":
                threading.Thread(target=lambda: whisper_stt.load(str(self._get("stt_model") or "auto")),
                                 daemon=True).start()        # warm up in the background
        except Exception as e:  # noqa: BLE001
            log.exception("voice loop init failed")
            self.error.emit(f"Voice wake-up unavailable: {e}")
            return
        name = brand.NAME.lower()
        words = [name, f"hey {name}", f"okay {name}", f"ok {name}"]
        if name.endswith("s"):
            words.append(name[:-1])
        grammar = json.dumps(words + ["[unk]"])
        rx = wake_regex()
        backoff = 2.0
        while not self._stop.is_set():
            argv = stt.recorder_argv()
            try:
                policy.check_binary(argv[0])
                self._proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                              stdin=subprocess.DEVNULL)
                log.info("voice wake-up listening (say “%s”)", self.phrase())
                spot = KaldiRecognizer(model, stt.RATE, grammar) if engine == "name" else None
                if spot:
                    spot.SetWords(True)
                utt = bytearray()
                mode, rec, cmd, deadline, silent_until, cooldown, heard = "wake", None, bytearray(), 0.0, 0.0, 0.0, False
                vtext_so_far, last_voice = "", 0.0
                audio_t = 0.0                 # stream clock (robust to buffering / slow CPUs)
                while not self._stop.is_set():
                    data = self._proc.stdout.read(CHUNK * 2)
                    if not data or len(data) < CHUNK * 2:
                        break
                    audio_t += CHUNK / stt.RATE
                    now = audio_t
                    if mode == "wake":
                        triggered, spoken_cmd = False, None
                        if engine == "hey_jarvis":
                            score = det.process(np.frombuffer(data, dtype=np.int16))
                            self.last_score = score
                            triggered = score >= det.threshold and self._ready(now, cooldown)
                        else:
                            utt += data
                            if len(utt) > stt.RATE * 2 * self.MAX_UTT_S:
                                del utt[: len(utt) - stt.RATE * 2 * self.MAX_UTT_S]
                            if spot.AcceptWaveform(data):
                                res = json.loads(spot.Result()).get("result", [])
                                # only confident name detections go on to the (costlier) verification
                                heard_words = [w["word"] for w in res
                                               if w.get("word") != "[unk]" and w.get("conf", 0) >= 0.85]
                                audio, utt = bytes(utt), bytearray()
                                if any(w in (name, name.rstrip("s")) for w in heard_words[:2]) \
                                        and self._ready(now, cooldown):
                                    v = KaldiRecognizer(model, stt.RATE)
                                    v.AcceptWaveform(audio)
                                    vtext = json.loads(v.FinalResult()).get("text", "")
                                    m = rx.match(vtext)
                                    log.info("name spotted; verify %r -> %s", vtext, bool(m))
                                    if m:
                                        triggered = True
                                        if m.group(1).strip():
                                            spoken_cmd = (audio, vtext)
                        if triggered:
                            self.woke.emit()
                            self.listening.emit(True)
                            # keep listening until a real pause; keep what was already said
                            mode = "command"
                            cmd = bytearray(spoken_cmd[0]) if spoken_cmd else bytearray()
                            heard = bool(spoken_cmd)
                            vtext_so_far = strip_wake_words(spoken_cmd[1]) if spoken_cmd else ""
                            last_voice = now
                            rec = KaldiRecognizer(model, stt.RATE)
                            deadline = now + float(self._get("stt_max_seconds"))
                            silent_until = now + 5.0
                    else:
                        cmd += data
                        if rec.AcceptWaveform(data):
                            t = json.loads(rec.Result()).get("text", "")
                            if strip_wake_words(t):
                                heard = True
                                vtext_so_far = (vtext_so_far + " " + strip_wake_words(t)).strip()
                                last_voice = now
                        else:
                            p = json.loads(rec.PartialResult()).get("partial", "")
                            if strip_wake_words(p):
                                heard = True
                                last_voice = now
                                self.partial.emit((vtext_so_far + " " + strip_wake_words(p)).strip())
                        done = ((heard and now - last_voice > 0.9) or now > deadline
                                or (not heard and now > silent_until))
                        if done:
                            if not vtext_so_far:
                                vtext_so_far = strip_wake_words(json.loads(rec.FinalResult()).get("text", ""))
                            text = strip_wake_words(self._transcribe(bytes(cmd), vtext_so_far)) if heard else ""
                            log.info("command: %r", text)
                            self.listening.emit(False)
                            self.final.emit(text)
                            mode, rec = "wake", None
                            if det:
                                det.reset()
                            spot = KaldiRecognizer(model, stt.RATE, grammar) if spot else None
                            if spot:
                                spot.SetWords(True)
                            utt = bytearray()
                            cooldown = audio_t + 1.5
                backoff = 2.0
            except Exception as e:  # noqa: BLE001
                log.warning("voice loop error: %s", e)
            finally:
                if self._proc and self._proc.poll() is None:
                    self._proc.terminate()
            if self._stop.wait(backoff):
                break
            backoff = min(60.0, backoff * 2)
        log.info("voice wake-up stopped")
