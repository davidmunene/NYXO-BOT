# Kings (NYXO) development status

_Last updated: 2026-11-18_

## Environment (inspected)

Ubuntu 26.04.1 LTS · GNOME Shell 50.1 · Wayland (XWayland available, DISPLAY=:0)
· Python 3.14.4 · Intel i5-8365U (8 threads) · 14 GB RAM · integrated GPU ·
~9 GB free disk. Ollama 0.32.1 is present in /usr/local/bin, but its runtime is
incomplete (see Known limitations).

## Completed and verified

| Feature | How it was verified |
|---|---|
| Character: 16 states + blink/glance, v2 "realistic" art (shaded shell, glass visor, arc core, holo pad) | rendered contact sheet inspected; sprite test checks every state |
| Transparent frameless always-on-top window, no dock entry (XWayland mode) | ran on the real session; window moves (x 1659→1424 while walking) |
| Random blinking/glancing, eased walking, lip-sync to audio | unit/integration tests; live speech produced 134 level frames |
| Click, double-click, drag-and-drop with gravity, right-click menu, tray | integration tests + manual IPC runs |
| Hide/show/toggle via menu, tray and CLI | `test_hide_show`, IPC tests |
| Chat window: history, timestamps, streaming, typing indicator, confirmation cards, clear | integration tests |
| Action registry, allowlists, validation, confirmations, audit log | 25+ security/action tests (incl. `sudo`/shell/`rm` rejection) |
| Apps, file search/info/storage, system info, git, tasks, reminders, memory, custom commands | action tests against real system, a real git repo, real .desktop files |
| JARVIS personality, spoken login briefing, proactive alerts (battery/power/disk/CPU/network) | `test_jarvis.py` |
| Piper neural voice auto-selected (British en_GB-alan-medium), always-speak default | played through speakers; 12× real-time synthesis |
| Vosk push-to-talk STT | transcribed synthesized "what time is it" correctly |
| AI provider abstraction: Ollama / Anthropic / OpenAI-compatible / off | protocol tests against local fake servers; clear "AI unavailable" fallback tested |
| SQLite persistence, export, backup, redaction of secrets | DB tests (settings survive reopen; file mode 600) |
| Settings window, 11 sections, all controls live-bound | every page built and refreshed in tests |
| Single instance + IPC (`nyxo --chat/--quit/...`) | duplicate launches leave exactly 1 process |
| Login autostart: systemd user service on graphical-session.target | installed and enabled; process has WAYLAND_DISPLAY/DISPLAY |
| Crash restart / clean quit | SIGSEGV → restarted by systemd; `nyxo --quit` → stays stopped (also during a pending restart) |
| Stays visible on the empty desktop / "Show Desktop" / every workspace | mutter reports `_NET_WM_WINDOW_TYPE_DOCK` + `STICKY` + `ABOVE`; forced minimise auto-restored |
| Announces which app sent a notification (never the content) | live `notify-send` → "New notification from Slack"; message text absent from log, DB, WAL |
| “Hey Jarvis” hands-free wake word (openWakeWord models on onnxruntime) + Vosk command | synthetic “Hey Jarvis” scores 0.998, other speech ≤ 0.27; full loop “Hey Jarvis, what time is it” → command transcribed; no false triggers during live music |
| Media control (MPRIS): play/pause/next/previous/what's playing | live: reported the track playing in Brave; control logic tested with mocks |
| Disk-cleanup helper (scan + confirmed per-target clearing, symlink-escape protection) | live scan found 10.8 GB; nothing deleted without approval |
| Step aside for full-screen apps (GNOME helper extension) | extension installed + enabled (backup of extension list kept); syntax-checked; active after next login |
| Renamed to **Kings** (display, voice, prompts, launcher; configurable) | entry-point test checks the name; running instance logs “Kings 1.0.0 started” |
| Portrait avatar from the user's character sheet (cut-out, FFT alignment of 8 expressions, synthesized blinks + 4 lip-sync mouths) | all 18 states rendered; alignment scale 1.48–1.51 consistent; preview inspected |
| Wake by name (“Kings, …”): Vosk grammar spotting + full-model verification | “Kings…/Hey Kings…” accepted; “the king is…”, “kingdom…”, “I like kings…” rejected (tests) |
| Whisper base.en transcription (faster-whisper int8) | 7/7 test phrases exact (Vosk 4/7); ~1.3 s per command on this CPU |
| Wikipedia grounding for factual questions; friendly AI errors; YouTube/web intents | tests; live lookup returned the right article |
| **Local AI fixed**: user-level Ollama runtime (~/.local/opt/ollama, no sudo) | live test passes (“Paris”); qwen2.5:3b (default) + 1.5b installed |
| Speech starts per sentence while the AI is still writing | queue test: 3 sentences → one continuous answer |
| Verified answers for common git/Linux how-tos (no AI guessing) | tests (e.g. “undo last commit, keep changes” → `git reset --soft HEAD~1`) |
| Installer / updater / uninstaller | install and update run several times; service restarted on update |

Test suite: **136 passed** (including the live Ollama test) (`.venv/bin/python -m pytest`). One live-Ollama test
is skipped/failing until the Ollama runtime is repaired.

Resource use (installed, idle): ~1.9 % of one CPU core. Memory ~260 MB with the
neural voice loaded (~45 MB without it).

## Pending / needs the user

* **Reboot/login test:** the service is enabled and verified with the same
  systemd target GNOME uses at login, but an actual log-out/log-in can only be
  confirmed by the user.
* **Visual check on the real desktop:** XWayland can't screenshot the Wayland
  desktop, so the robot's on-screen appearance and the dock/Alt-Tab hiding need
  a quick look.
* **Log out and back in once** to activate the NYXO Helper extension
  (full-screen auto-hide), and to confirm autostart.
* **Say “Hey Jarvis, what time is it?”** to try the wake word with your voice.
* **Show Desktop check:** press your Show-Desktop shortcut (or minimise all
  windows) and confirm NYXO stays put.
* **Microphone test** with a real voice (Settings → Voice → Test microphone).

## Known limitations

* **Local AI speed/quality:** on this i5-8365U with memory under pressure (swap full while browsers
  are open), qwen2.5:3b answers in roughly 20–50 s and can still be vague on precise technical
  questions. Factual questions are grounded in Wikipedia and common how-tos use verified answers.
  For consistently excellent answers, add an Anthropic API key (Settings → AI providers).
* **Disk:** the system drive is ~97 % full after the model downloads. Run “free up disk space”
  (about 10 GB of caches/Trash can be cleared).
* The portrait avatar uses ©YORUHARE artwork: kept privately in ~/.local/share/nyxo/avatars/kings;
  get the artist's permission before sharing.

* **Ollama:** the system copy in /usr/local is incomplete, so Kings now uses its own
  user-level runtime in ~/.local/opt/ollama (`scripts/install_ollama.sh`). The old note follows:
  **Ollama install is incomplete:** `/usr/local/lib/ollama` contains only
  `cuda_v12/` and no CPU `llama-server`, so models download but can't run. Fix:
  `curl -fsSL https://ollama.com/install.sh | sh` (needs sudo), or install
  Ollama another way. The model `qwen2.5:1.5b` is already downloaded.
* Wayland: see docs/WAYLAND.md (XWayland mode for roaming; no global input,
  by design).
* Without the neural voice, speech falls back to robotic espeak.
* Vosk small model accuracy is modest. Larger models can be dropped into the
  models folder.
* As a dock-type window NYXO also stays above full-screen apps (videos/games).
  Use "Hide" or focus mode while watching, or untick Settings → General →
  "Stay visible on the empty desktop".
* Notification announcements see apps that use the standard freedesktop or
  GNOME (GApplication/portal) notification APIs, which covers practically all
  desktop apps. Only the app name is read.
* Wake word: about 10 % of one CPU core while the room is noisy (e.g. music
  playing), near zero in silence. The microphone is analysed continuously while
  it's enabled (nothing is stored); turn it off in Settings → Voice.
* The wake phrase is “Hey Jarvis” (pretrained model). A custom “Hey NYXO”
  would need training a new model.
* “Step aside while video keeps the screen awake” is off by default: browsers
  block screen-sleep for any playing video, even in a small window, so it's
  too coarse to be on by default.
* Cloud AI was tested against protocol mocks only (no API key was provided).
