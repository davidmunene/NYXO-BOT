"""Run the real CLI entry point in a subprocess (catches start-up errors)."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_cli_status_and_help(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(ROOT), QT_QPA_PLATFORM="offscreen",
               XDG_CONFIG_HOME=str(tmp_path / "c"), XDG_DATA_HOME=str(tmp_path / "d"),
               XDG_STATE_HOME=str(tmp_path / "s"), XDG_RUNTIME_DIR=str(tmp_path / "r"))
    (tmp_path / "r").mkdir()
    for args in (["--help"], ["--status"], ["--quit"]):
        r = subprocess.run([sys.executable, "-m", "nyxo", *args], env=env, capture_output=True,
                           text=True, timeout=60)
        assert r.returncode == 0, r.stderr
    assert "Kings" in subprocess.run([sys.executable, "-m", "nyxo", "--help"], env=env,
                                     capture_output=True, text=True).stdout


def test_app_boots_offscreen(tmp_path):
    """Start the full app for a few seconds via main() and quit over IPC."""
    env = dict(os.environ, PYTHONPATH=str(ROOT), QT_QPA_PLATFORM="offscreen", NYXO_NO_SYSTEMD="1",
               XDG_CONFIG_HOME=str(tmp_path / "c"), XDG_DATA_HOME=str(tmp_path / "d"),
               XDG_STATE_HOME=str(tmp_path / "s"), XDG_RUNTIME_DIR=str(tmp_path / "r"))
    (tmp_path / "r").mkdir()
    p = subprocess.Popen([sys.executable, "-m", "nyxo"], env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True)
    try:
        import time
        time.sleep(6)
        assert p.poll() is None, p.stdout.read()
        q = subprocess.run([sys.executable, "-m", "nyxo", "--quit"], env=env, timeout=30)
        assert q.returncode == 0
        assert p.wait(timeout=20) == 0
    finally:
        if p.poll() is None:
            p.kill()
