#!/bin/bash
# Install a user-level Ollama runtime (CPU only) in ~/.local/opt/ollama — no sudo.
# Kings uses it automatically. Resumable: re-run if the download is interrupted.
set -u
VER="${OLLAMA_VERSION:-v0.32.1}"
mkdir -p ~/.local/opt/ollama-dl
D=~/.local/opt/ollama-dl; cd "$D" || exit 1
U="https://github.com/ollama/ollama/releases/download/$VER/ollama-linux-amd64.tar.zst"
for t in $(seq 1 80); do
  curl -fL -sS -C - --connect-timeout 20 --speed-limit 2000 --speed-time 30 -o ollama.tar.zst "$U" && break
  echo "resume $t at $(stat -c %s ollama.tar.zst)"; sleep 5
done
ls -la ollama.tar.zst
mkdir -p ~/.local/opt/ollama
zstd -dc ollama.tar.zst | tar -x -C ~/.local/opt/ollama --exclude='*cuda*' --exclude='*rocm*' --exclude='*mlx*' --exclude='*vulkan*' && rm -f ollama.tar.zst
du -sh ~/.local/opt/ollama; find ~/.local/opt/ollama -maxdepth 3 | head -20

rmdir ~/.local/opt/ollama-dl 2>/dev/null; ~/.local/opt/ollama/bin/ollama --version
