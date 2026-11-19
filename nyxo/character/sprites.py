"""Loads PNG spritesheets and serves scaled (and mirrored) frames, cached
lazily per state so memory stays proportional to what is actually shown."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter, QPixmap

from .states import ANIMATIONS, State, poses_for

log = logging.getLogger("nyxo.sprites")


class SpriteBank:
    def __init__(self, anim_dir: Path, size: int):
        self.anim_dir = Path(anim_dir)
        self.size = int(size)
        self._sheets: dict[str, QImage] = {}
        self._cache: dict[tuple[str, bool], list[QPixmap]] = {}
        self.manifest = self._load_manifest()

    def _load_manifest(self) -> dict:
        mf = self.anim_dir / "manifest.json"
        if mf.exists():
            try:
                return json.loads(mf.read_text())
            except (OSError, json.JSONDecodeError):
                log.warning("bad sprite manifest, falling back to vector rendering")
        return {"frame_size": 256, "states": {}}

    def set_size(self, size: int) -> None:
        if int(size) != self.size:
            self.size = int(size)
            self._cache.clear()

    def fps(self, state: State) -> float:
        info = self.manifest["states"].get(state.value)
        return float(info["fps"]) if info else ANIMATIONS[state].fps

    def _sheet_frames(self, state: State) -> list[QImage]:
        info = self.manifest["states"].get(state.value)
        if info:
            img = self._sheets.get(state.value)
            if img is None:
                img = QImage(str(self.anim_dir / info["file"]))
                if not img.isNull():
                    self._sheets[state.value] = img
            if img is not None and not img.isNull():
                fs = img.height()
                return [img.copy(i * fs, 0, fs, fs) for i in range(info["frames"])]
        # fallback: draw directly from the vector artist
        from .artist import draw_robot
        frames = []
        for pose in poses_for(state):
            im = QImage(256, 256, QImage.Format_ARGB32_Premultiplied)
            im.fill(Qt.transparent)
            p = QPainter(im)
            draw_robot(p, pose, 256)
            p.end()
            frames.append(im)
        return frames

    def frames(self, state: State, flip: bool = False) -> list[QPixmap]:
        key = (state.value, flip)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        out = []
        for im in self._sheet_frames(state):
            im = im.scaled(self.size, self.size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            if flip:
                im = im.flipped(Qt.Horizontal)
            out.append(QPixmap.fromImage(im))
        self._cache[key] = out
        # raw sheets are only needed to build the cache; free them
        self._sheets.pop(state.value, None)
        return out

    def drop_cache(self, keep: set[str] | None = None) -> None:
        for k in list(self._cache):
            if keep is None or k[0] not in keep:
                del self._cache[k]
