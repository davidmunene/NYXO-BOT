"""High-accuracy speech-to-text with Whisper (faster-whisper / CTranslate2,
int8 on CPU, fully offline).

Vosk still does the cheap always-on work (wake word, live partial text, end
of speech); once you stop talking, the captured utterance is transcribed by
Whisper, which is far more accurate — especially with accents and names.
The models live in ~/.local/share/nyxo/models/whisper/<size>/."""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from ..core import paths

log = logging.getLogger("nyxo.whisper")

SIZES = ["base.en", "small.en", "tiny.en"]       # auto picks the first installed (base.en: best speed/accuracy on a laptop CPU)
BEAM = 2
_model = None
_model_key: str | None = None
_lock = threading.Lock()


def installed() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def model_path(size: str) -> Path:
    return paths.models_dir() / "whisper" / size


def available_sizes() -> list[str]:
    return [s for s in SIZES if (model_path(s) / "model.bin").exists()]


def pick_size(preferred: str = "auto") -> str | None:
    if preferred == "off":
        return None
    have = available_sizes()
    if preferred in have:
        return preferred
    return have[0] if have else None


def status(preferred: str = "auto") -> tuple[bool, str]:
    if not installed():
        return False, "Whisper (faster-whisper) isn't installed — using Vosk."
    size = pick_size(preferred)
    if preferred == "off":
        return False, "Whisper turned off — using Vosk only."
    if not size:
        return False, "No Whisper model downloaded — using Vosk."
    return True, f"Whisper {size} (offline)"


def load(preferred: str = "auto"):
    global _model, _model_key
    size = pick_size(preferred)
    if size is None:
        return None
    with _lock:
        if _model is None or _model_key != size:
            from faster_whisper import WhisperModel
            log.info("loading whisper %s", size)
            _model = WhisperModel(str(model_path(size)), device="cpu", compute_type="int8",
                                  cpu_threads=4, num_workers=1)
            _model_key = size
        return _model


def transcribe(pcm16: bytes, name: str = "", preferred: str = "auto") -> str | None:
    """Transcribe 16 kHz mono int16 audio. Returns None if Whisper is unavailable."""
    if not installed() or len(pcm16) < 3200:
        return None
    model = load(preferred)
    if model is None:
        return None
    import numpy as np
    audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
    # The prompt biases spelling of the assistant's name and common commands.
    prompt = (f"{name}, open Firefox. {name}, what's the time? Pause the music. Git status. "
              "Remind me in ten minutes. VS Code, YouTube, Ubuntu." if name else None)
    segments, _info = model.transcribe(audio, language="en", beam_size=BEAM, best_of=1,
                                       temperature=0.0, condition_on_previous_text=False,
                                       initial_prompt=prompt, vad_filter=False,
                                       without_timestamps=True)
    text = " ".join(s.text.strip() for s in segments).strip()
    # Whisper sometimes hallucinates the prompt or stock phrases on near-silence
    if text.lower().strip(" .!") in {"you", "thank you", "thanks for watching", ""}:
        return ""
    return text
