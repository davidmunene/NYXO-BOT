# NYXO architecture

```
nyxo/
  main.py              CLI, single-instance lock, display-mode selection, signals
  core/                app orchestrator, settings (SQLite-backed), XDG paths, logging, IPC
  character/           artist.py (vector renderer), states.py (16+2 animation states), sprites.py
  desktop/             companion window, speech bubble, X11 hints, GNOME idle monitor, shortcut
  assistant/           intent parser, assistant pipeline, personality, JARVIS briefing
  ai/                  provider interface: Ollama, Anthropic, OpenAI-compatible; credentials
  system/              action registry, safe runner, apps, files, sysinfo, git, watcher
  security/            risk policy, validators, allowlists, redaction
  memory/              SQLite database
  voice/               Piper/espeak TTS with lip-sync levels, Vosk push-to-talk STT
  notifications/       notify-send + reminder scheduler
  settings/            settings window
  startup/             systemd user service management
  ui/                  theme + chat window
```

## Request flow

1. Text (typed or recognised speech) goes to `Assistant.submit`.
2. `intents.parse` maps common requests straight to registry actions. These are
   instant and need no AI.
3. Everything else goes to the configured AI provider with a system prompt
   (personality, memories, time, available actions). Tokens stream into the chat.
4. The AI may *propose* one action (`ACTION: {...}`). It's validated against the
   registry. Read-only actions run; anything that changes state shows a
   confirmation card.
5. Results go to the chat, the speech bubble and the voice (with lip-sync), and
   the robot changes mood.

## Security model

* No shell is ever used: every process is an argv list, and NYXO's own commands
  are limited to an allowlist (`security/policy.py`). `sudo`, shells,
  interpreters, `rm`, `curl`, etc. are forbidden, even in user-defined custom
  commands.
* Actions have risk levels. CONFIRM actions (git commit/branch/switch, trash,
  delete task/memory, clear history, custom commands, opening executables)
  always need an explicit click.
* Path rules block credentials (`~/.ssh`, keyrings, browser profiles, NYXO's own
  key file) and system paths. Only files inside `$HOME` can be moved to the
  Trash (which is recoverable).
* Secrets are redacted before anything is stored. API keys live only in env vars
  or a 0600 file.
* Every action, including rejected or declined ones, is audit-logged
  (Settings → Privacy & security).

## Performance

Sprites are pre-rendered PNG strips, scaled once per size and cached per state.
Timers only run when needed (movement at 30 fps only while walking). Measured on
the target laptop: ~1.9 % of one core when idle. Memory is ~45 MB without the
neural voice and ~260 MB with Piper loaded. Ollama and the Vosk model load only
on first use.
