#!/usr/bin/env python3
"""Build the animated portrait avatar from a character sheet image.

    python scripts/build_avatar.py ~/Downloads/kings.jpeg [--name kings]

Output: ~/.local/share/nyxo/avatars/<name>/ (private user data; the source art
may be copyrighted — it is never copied into the project)."""
import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QGuiApplication  # noqa: E402

from nyxo.character import portrait  # noqa: E402
from nyxo.core import paths  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("image")
ap.add_argument("--name", default="kings")
a = ap.parse_args()
app = QGuiApplication(sys.argv[:1])
portrait.build(Path(a.image).expanduser(), paths.data_dir() / "avatars" / a.name)
