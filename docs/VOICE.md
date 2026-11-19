# Voice

## Speech output (on by default)

NYXO speaks everything it says (Settings → Voice → Speak: *Everything NYXO
says*). Engine "Automatic" picks the most natural voice available:

1. **Piper** neural TTS, in-process, offline. The default voice is
   `en_GB-alan-medium` (British male), stored in
   `~/.local/share/nyxo/models/piper/`. It runs about 12× faster than real time
   on an i5-8365U. Speech streams sentence by sentence, and loudness levels
   drive NYXO's mouth (lip-sync).
2. espeak / espeak-ng, then spd-say as lightweight fallbacks.

To use another voice, drop any `.onnx` + `.onnx.json` pair from
<https://huggingface.co/rhasspy/piper-voices> into the piper folder and pick it
under Settings → Voice → Neural voice. Speed is adjustable, and ■ in the chat
stops speech instantly.

## Hands-free: say “Kings”

Default wake mode (Settings → Voice → Wake word). Two stages keep it cheap and accurate:
1. Vosk listens with a tiny grammar for the name (~4 % of one CPU core).
2. When it hears the name, the utterance is re-checked with the full recognizer and accepted only
   if it *starts* with “Kings” / “Hey Kings” / “OK Kings”.
The request is captured until you pause for about 0.9 s, then transcribed by **Whisper base.en**
(offline, about 1.3 s on an i5-8365U). Number words are understood (“in twenty minutes”).

## Alternative: “Hey Jarvis”

On by default (Settings → Voice). Say “Hey Jarvis” and then your request, in
one breath if you like: “Hey Jarvis, pause the music”. Wake-word detection uses
openWakeWord's pretrained models (melspectrogram → speech embedding → “hey
jarvis”), run directly on onnxruntime. The models are in
`~/.local/share/nyxo/models/wakeword/`. The microphone stream is analysed
locally and never recorded. Only the words after the wake phrase are
transcribed (by Vosk). Silence is detected cheaply, so a quiet room costs
almost no CPU. Sensitivity is adjustable.

## Speech recognition (push-to-talk)

Vosk (`vosk-model-small-en-us-0.15`, offline). The microphone opens only when
you press 🎤 in the chat, choose "Talk to NYXO", or run `nyxo --listen`. It
closes after you stop talking (or after 12 s). Audio is never stored. Recording
uses `pw-record` (PipeWire) or `arecord`.

If NYXO hears nothing, check GNOME Settings → Sound → Input, and Settings →
Voice → Test microphone.
