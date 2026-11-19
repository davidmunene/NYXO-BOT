#!/usr/bin/env python3
"""Bake NYXO's vector artwork into transparent PNG spritesheets.

Outputs (relative to the project root):
    assets/animations/<state>.png   horizontal strip, FRAME x FRAME per frame
    assets/animations/manifest.json frame counts / fps / loop per state
    assets/icons/nyxo-<n>.png       app icons
    assets/characters/nyxo-preview.png  contact sheet of all states

Usage:  python scripts/generate_sprites.py [--frame 256]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter  # noqa: E402

from nyxo.character.artist import draw_icon, draw_robot  # noqa: E402
from nyxo.character.states import ANIMATIONS, poses_for  # noqa: E402


def blank(w: int, h: int) -> QImage:
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    return img


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frame", type=int, default=256)
    args = ap.parse_args()
    _app = QGuiApplication(sys.argv)
    fs = args.frame

    anim_dir = ROOT / "assets" / "animations"
    icon_dir = ROOT / "assets" / "icons"
    char_dir = ROOT / "assets" / "characters"
    for d in (anim_dir, icon_dir, char_dir):
        d.mkdir(parents=True, exist_ok=True)

    manifest = {"frame_size": fs, "states": {}}
    for state, anim in ANIMATIONS.items():
        poses = poses_for(state)
        sheet = blank(fs * len(poses), fs)
        p = QPainter(sheet)
        for i, pose in enumerate(poses):
            p.save()
            p.translate(i * fs, 0)
            draw_robot(p, pose, fs)
            p.restore()
        p.end()
        out = anim_dir / f"{state.value}.png"
        sheet.save(str(out))
        manifest["states"][state.value] = {
            "file": out.name, "frames": len(poses), "fps": anim.fps, "loop": anim.loop}
        print(f"  {state.value:12s} {len(poses):2d} frames -> {out.relative_to(ROOT)}")
    (anim_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    for n in (48, 64, 128, 256, 512):
        img = blank(n, n)
        p = QPainter(img)
        draw_icon(p, n)
        p.end()
        img.save(str(icon_dir / f"nyxo-{n}.png"))
    print("  icons -> assets/icons/")

    # contact sheet with frame 0 (and a mid frame) of every state
    cell, cols = 200, 4
    states = list(ANIMATIONS)
    rows = (len(states) + cols - 1) // cols
    sheet = blank(cell * cols, (cell + 24) * rows)
    p = QPainter(sheet)
    p.fillRect(sheet.rect(), QColor("#0d1b2e"))
    f = QFont("DejaVu Sans")
    f.setPixelSize(15)
    f.setBold(True)
    p.setFont(f)
    for idx, state in enumerate(states):
        poses = poses_for(state)
        pose = poses[len(poses) // 3]
        x, y = (idx % cols) * cell, (idx // cols) * (cell + 24)
        p.save()
        p.translate(x, y)
        draw_robot(p, pose, cell)
        p.restore()
        p.setPen(QColor("#3ff0ff"))
        p.drawText(QRectF(x, y + cell, cell, 22), Qt.AlignCenter, state.value)
    p.end()
    sheet.save(str(char_dir / "nyxo-preview.png"))
    print("  preview -> assets/characters/nyxo-preview.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
