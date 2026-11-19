"""Push-to-talk speech recognition with Vosk (offline).

The microphone is opened only between start() and the end of the utterance
(silence, stop(), or the max duration) — NYXO never listens in the background.
Audio is captured with pw-record (PipeWire) or arecord and never stored."""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..core import paths
from ..security import policy
from ..core import brand

log = logging.getLogger("nyxo.stt")
RATE = 16000

_model = None
_model_path: Path | None = None
_model_lock = threading.Lock()


def find_model(configured: str = "") -> Path | None:
    if configured and (Path(configured).expanduser() / "am").exists():
        return Path(configured).expanduser()
    d = paths.models_dir()
    if d.exists():
        for p in sorted(d.glob("vosk-model*")):
            if p.is_dir():
                return p
    return None


def load_model(path: Path):
    global _model, _model_path
    with _model_lock:
        if _model is None or _model_path != path:
            from vosk import Model, SetLogLevel
            SetLogLevel(-1)
            log.info("loading vosk model %s", path)
            _model = Model(str(path))
            _model_path = path
        return _model


def vosk_installed() -> bool:
    try:
        import vosk  # noqa: F401
        return True
    except ImportError:
        return False


def recorder_argv() -> list[str] | None:
    if shutil.which("pw-record"):
        return ["pw-record", "--rate", str(RATE), "--channels", "1", "--format", "s16", "-"]
    if shutil.which("arecord"):
        return ["arecord", "-q", "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw"]
    return None


def status(configured: str = "") -> tuple[bool, str]:
    if not vosk_installed():
        return False, f"Vosk isn't installed in {brand.NAME}'s environment."
    if not find_model(configured):
        return False, "No Vosk model found. Use Settings → Voice → Download model."
    if not recorder_argv():
        return False, "No audio recorder (pw-record/arecord) found."
    return True, f"Vosk · {find_model(configured).name}"


def transcribe_wav(path: str, model_dir: Path | None = None) -> str:
    """Offline transcription of a 16-bit mono WAV (used by tests/diagnostics)."""
    from vosk import KaldiRecognizer
    model = load_model(model_dir or find_model())
    with wave.open(path, "rb") as wf:
        rec = KaldiRecognizer(model, wf.getframerate())
        while data := wf.readframes(4000):
            rec.AcceptWaveform(data)
        return json.loads(rec.FinalResult()).get("text", "")


class Listener(QObject):
    partial = Signal(str)
    final = Signal(str)
    error = Signal(str)
    listening = Signal(bool)

    def __init__(self, get):
        super().__init__()
        self._get = get
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None

    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.active():
            return
        ok, detail = status(str(self._get("vosk_model")))
        if not ok:
            self.error.emit(detail)
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        from vosk import KaldiRecognizer
        text = ""
        captured = bytearray()
        try:
            model = load_model(find_model(str(self._get("vosk_model"))))
            rec = KaldiRecognizer(model, RATE)
            argv = recorder_argv()
            policy.check_binary(argv[0])
            self._proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                          stdin=subprocess.DEVNULL)
            self.listening.emit(True)
            deadline = time.monotonic() + float(self._get("stt_max_seconds"))
            heard = False
            while not self._stop.is_set() and time.monotonic() < deadline:
                data = self._proc.stdout.read(3200)      # 100 ms
                if not data:
                    break
                captured += data
                if rec.AcceptWaveform(data):
                    text = json.loads(rec.Result()).get("text", "")
                    if text:
                        break                             # end of utterance
                else:
                    part = json.loads(rec.PartialResult()).get("partial", "")
                    if part:
                        heard = True
                        self.partial.emit(part)
                    elif not heard and time.monotonic() > deadline - float(self._get("stt_max_seconds")) + 6:
                        break                             # 6 s of nothing
            if not text:
                text = json.loads(rec.FinalResult()).get("text", "")
            if heard or text:
                try:
                    from . import whisper_stt
                    better = whisper_stt.transcribe(bytes(captured), brand.NAME, str(self._get("stt_model") or "auto"))
                    if better is not None:
                        text = better
                except Exception as e:  # noqa: BLE001
                    log.warning("whisper failed, keeping vosk text: %s", e)
        except Exception as e:  # noqa: BLE001
            log.exception("listening failed")
            self.error.emit(f"Microphone/recognition error: {e}")
        finally:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(2)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            self._proc = None
            self.listening.emit(False)
        self.final.emit(text.strip())
