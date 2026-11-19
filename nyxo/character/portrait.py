"""Build an animated avatar from a character sheet image (a "portrait" avatar).

Input: a sheet with a main bust on the left and a 2x4 grid of expression
heads on the right (the layout of the Kings sheet). Steps:
  1. cut out the bust and every expression (edge-connected region growing
     from the light background; the line art stops the fill),
  2. align each expression head onto the bust (FFT cross-correlation of edge
     maps over a range of scales),
  3. transplant faces with a feathered mask -> one full-body image per
     expression, plus synthesized blinks and 4 mouth shapes for lip-sync,
  4. render every animation state (breathing, sway, bounce, hover over the
     holographic pad, effects) into spritesheets + manifest.

The generated files go to ~/.local/share/nyxo/avatars/<name>/ (the user's
private data — the source art may be someone else's copyrighted work)."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QRadialGradient

from . import artist
from .artist import Pose
from .states import ANIMATIONS, State

# Layout of the Kings sheet (1024x863). Other sheets can pass their own.
SHEET_LAYOUT = {
    "main": (40, 125, 572, 775),
    "rows": [(48, 238), (238, 426), (426, 614), (614, 802)],
    "cols": [(572, 772), (776, 976)],
    # names in reading order: row by row, left then right column
    "names": ["wink", "smile", "scared", "pout", "laugh", "blush", "deadpan", "shock"],
    # regions in main-bust coordinates
    "face": (272, 200, 84, 92),          # ellipse cx, cy, rx, ry
    "eyes": (200, 140, 350, 192),        # x0, y0, x1, y1
    "mouth": (236, 222, 306, 266),
}


# ------------------------------------------------------------------ numpy <-> Qt
def to_np(img: QImage) -> np.ndarray:
    img = img.convertToFormat(QImage.Format_RGBA8888)
    h, w = img.height(), img.width()
    return np.frombuffer(img.constBits(), np.uint8).reshape(h, img.bytesPerLine())[:, :w * 4] \
        .reshape(h, w, 4).copy()


def to_q(a: np.ndarray) -> QImage:
    a = np.ascontiguousarray(a.astype(np.uint8))
    h, w, _ = a.shape
    return QImage(a.tobytes(), w, h, w * 4, QImage.Format_RGBA8888).copy()


def scaled(a: np.ndarray, s: float) -> np.ndarray:
    q = to_q(a).scaled(max(1, int(a.shape[1] * s)), max(1, int(a.shape[0] * s)),
                       Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return to_np(q)


# ------------------------------------------------------------------- cut-out
def cutout(rgb: np.ndarray, box, tol: int = 14, bright: int = 200) -> np.ndarray:
    x0, y0, x1, y1 = box
    a = rgb[y0:y1, x0:x1].astype(np.int16)
    h, w, _ = a.shape
    cand = (a.mean(axis=2) > bright) & ((a.max(axis=2) - a.min(axis=2)) < 40)
    mask = np.zeros((h, w), bool)
    mask[0], mask[-1], mask[:, 0], mask[:, -1] = cand[0], cand[-1], cand[:, 0], cand[:, -1]
    close = {}
    for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        close[d] = np.abs(a - np.roll(a, d, axis=(0, 1))).sum(axis=2) < tol
    for _ in range(5000):
        grown = mask.copy()
        for d, c in close.items():
            grown |= np.roll(mask, d, axis=(0, 1)) & cand & c
        if np.array_equal(grown, mask):
            break
        mask = grown
    # de-fringe: pixels touching the background that are still light and tinted
    # like the background (JPEG halo) become transparent too — two passes
    lum = a.mean(axis=2)
    pinkish = (a[..., 0] - a[..., 1]) > 4                  # lavender/pink halo vs neutral silver hair
    for _ in range(2):
        touch = np.zeros_like(mask)
        for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            touch |= np.roll(mask, d, axis=(0, 1))
        mask = mask | (touch & (lum > 150) & (pinkish | (lum > 215)))
    alpha = np.where(mask, 0.0, 255.0)
    soft = (alpha + np.roll(alpha, 1, 0) + np.roll(alpha, -1, 0) + np.roll(alpha, 1, 1) + np.roll(alpha, -1, 1)) / 5
    alpha = np.minimum(alpha, soft * 1.1)
    return np.dstack([a.astype(np.uint8), alpha.clip(0, 255).astype(np.uint8)])


# ----------------------------------------------------------------- alignment
def _edges(rgba: np.ndarray) -> np.ndarray:
    g = rgba[..., :3].astype(np.float32).mean(axis=2)
    e = np.zeros_like(g)
    e[:, 1:] += np.abs(np.diff(g, axis=1))
    e[1:, :] += np.abs(np.diff(g, axis=0))
    return e * (rgba[..., 3] > 0)


def align(main: np.ndarray, tile: np.ndarray, head_rows: int = 330,
          scales=np.arange(1.10, 2.00, 0.01)) -> tuple[float, int, int, float]:
    M = _edges(main)[:head_rows]
    M = M - M.mean()
    H, W = M.shape
    FM = np.fft.rfft2(M, s=(H, W))
    best = (0.0, 1.0, 0, 0)
    for s in scales:
        t = scaled(tile, float(s))
        T = _edges(t)[: int(tile.shape[0] * s * 0.72)]
        T = T - T.mean() * (T != 0)
        th, tw = T.shape
        if th > H or tw > W:
            continue
        pad = np.zeros((H, W), np.float32)
        pad[:th, :tw] = T
        corr = np.fft.irfft2(FM * np.conj(np.fft.rfft2(pad)), s=(H, W))
        iy, ix = np.unravel_index(int(np.argmax(corr)), corr.shape)
        score = float(corr[iy, ix] / (np.linalg.norm(T) + 1e-6))
        if score > best[0]:
            best = (score, float(s), int(ix), int(iy))
    _, s, dx, dy = best
    return s, dx, dy, best[0]


def _ellipse_mask(h, w, cx, cy, rx, ry, feather=10.0) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w]
    d = np.sqrt(((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2)
    return np.clip((1.0 - d) * max(rx, ry) / feather, 0, 1)


def _rect_mask(h, w, box, feather=6.0) -> np.ndarray:
    x0, y0, x1, y1 = box
    cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
    return _ellipse_mask(h, w, cx, cy, rx, ry, feather)


def transplant(base: np.ndarray, tile: np.ndarray, s: float, dx: int, dy: int,
               mask: np.ndarray) -> np.ndarray:
    t = scaled(tile, s)
    layer = np.zeros_like(base)
    h = min(t.shape[0], base.shape[0] - dy)
    w = min(t.shape[1], base.shape[1] - dx)
    layer[dy:dy + h, dx:dx + w] = t[:h, :w]
    m = (mask * (layer[..., 3] / 255.0))[..., None]
    out = base.astype(np.float32)
    out[..., :3] = out[..., :3] * (1 - m) + layer[..., :3] * m
    return out.astype(np.uint8)


# ------------------------------------------------------------ build variants
def build_variants(sheet_path: Path, layout: dict = SHEET_LAYOUT, log=print) -> dict[str, np.ndarray]:
    rgb = to_np(QImage(str(sheet_path)))[..., :3]
    main = cutout(rgb, layout["main"])
    h, w = main.shape[:2]
    variants = {"neutral": main}
    tiles, fits = {}, {}
    names = iter(layout["names"])
    for (y0, y1) in layout["rows"]:
        for (x0, x1) in layout["cols"]:
            n = next(names)
            tiles[n] = cutout(rgb, (x0, y0, x1, y1))
            s, dx, dy, score = align(main, tiles[n])
            fits[n] = (s, dx, dy)
            log(f"  {n:8s} scale {s:.2f} offset ({dx},{dy}) score {score:.0f}")
    face = _ellipse_mask(h, w, *layout["face"])
    for n in layout["names"]:
        variants[n] = transplant(main, tiles[n], *fits[n], face)
    # synthesized: blinks (eyes only) and mouth shapes (mouth only) on the neutral face
    eyes = _rect_mask(h, w, layout["eyes"])
    mouth = _rect_mask(h, w, layout["mouth"], feather=5)
    variants["blink_half"] = transplant(main, tiles["deadpan"], *fits["deadpan"], eyes)
    variants["blink"] = transplant(main, tiles["laugh"], *fits["laugh"], eyes * 0.97)
    variants["talk1"] = transplant(main, tiles["shock"], *fits["shock"], mouth)
    variants["talk2"] = transplant(main, tiles["scared"], *fits["scared"], mouth)
    variants["talk3"] = transplant(main, tiles["laugh"], *fits["laugh"], mouth)
    variants["sleep"] = variants["blink"]
    return variants


# --------------------------------------------------------------- animation
@dataclass
class PFrame:
    face: str = "neutral"
    dy: float = 0.0          # vertical offset (px in 256-frame space)
    tilt: float = 0.0        # degrees
    breathe: float = 0.0     # -1..1
    squash: float = 1.0
    tint: str | None = None
    glow: float = 0.8
    extras: list = None


def _frames_for(state: State) -> list[PFrame]:
    a = ANIMATIONS[state]
    n = a.frames
    out = []
    for i in range(n):
        t = i / n
        s = math.sin(t * math.tau)
        f = PFrame(dy=s * 2.0, breathe=s, tilt=math.sin(t * math.tau + 0.7) * 0.6, extras=[])
        if state == State.IDLE:
            pass
        elif state == State.BLINK:
            f = PFrame(face=("blink_half", "blink", "blink_half")[i % 3], extras=[])
        elif state == State.GLANCE:
            f.face = "smile" if 2 <= i <= 7 else "neutral"
            f.tilt = (-2.5 if i < 5 else 2.0) if 1 <= i <= 8 else 0
        elif state in (State.WALKING, State.RUNNING):
            f.face = "smile" if state == State.WALKING else "laugh"
            f.dy = -abs(s) * (4 if state == State.WALKING else 8)
            f.tilt = 3 if state == State.WALKING else 7
            if state == State.RUNNING:
                f.extras = [("speed", t)]
        elif state == State.SLEEPING:
            f.face, f.tilt, f.dy, f.glow = "sleep", 6 + s, 3 + s, 0.35
            f.extras = [("zzz", t)]
        elif state == State.THINKING:
            f.face, f.tilt = "deadpan", -3
            f.extras = [("dots", t)]
        elif state == State.HAPPY:
            f.face, f.dy = ("blush" if i % 8 < 4 else "smile"), -abs(s) * 6
            f.extras = [("sparkles", t)]
        elif state == State.EXCITED:
            f.face, f.dy, f.squash = "laugh", -abs(s) * 12, 1 - (1 - abs(s)) * 0.03
            f.extras = [("sparkles", t)]
        elif state == State.CONFUSED:
            f.face, f.tilt = "deadpan", 6 + s * 2
            f.extras = [("question", t)]
        elif state == State.SAD:
            f.face, f.tilt, f.dy, f.tint, f.glow = "pout", -3, 3, "blue", 0.4
        elif state == State.TALKING:
            f.face = ("neutral", "talk1", "talk1", "talk2", "talk2", "talk3", "talk3", "talk3")[i]
            f.dy = -i * 0.3
        elif state == State.LISTENING:
            f.face, f.tilt = "smile", -4
            f.extras = [("waves", t)]
        elif state == State.WORKING:
            f.face = "deadpan"
            f.extras = [("laptop", t)]
        elif state == State.ERROR:
            f.face, f.tint = "shock", "red"
            f.tilt = (-1.5, 1.5, -1, 1, -0.5, 0.5)[i % 6]
            f.extras = [("exclaim", t), ("sparks", t)]
        elif state == State.CELEBRATING:
            f.face, f.dy = "laugh", -abs(math.sin(t * math.tau * 2)) * 12
            f.extras = [("confetti", t)]
        elif state == State.WAVING:
            f.face, f.dy, f.tilt = "wink", -abs(s) * 4, s * 3
            f.extras = [("hearts", t)]
        elif state == State.DRAGGED:
            f.face, f.dy, f.tilt = "scared", -8, s * 5
        out.append(f)
    return out


def render_frame(p: QPainter, img: QImage, f: PFrame, size: int, phase: float) -> None:
    """Draw one avatar frame into a size x size square at the origin."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    k = size / 256.0
    # holographic projector pad (shared with the robot art), in 128 space
    p.save()
    p.scale(size / 128.0, size / 128.0)
    artist._draw_pad(p, Pose(hud=phase, glow=f.glow, shadow=1.0 - min(0.5, abs(f.dy) / 30)))
    artist._draw_extras(p, Pose(extras=[e for e in (f.extras or []) if e[0] in artist._BEHIND]), None, True)
    p.restore()
    # character: bust fills ~92 % of the frame height and floats above the pad
    target_h = 236 * k
    sc = target_h / img.height()
    cw = img.width() * sc
    ground = 236 * k
    p.translate(size / 2, ground + f.dy * k)
    p.rotate(f.tilt)
    p.scale(1.0 + f.breathe * 0.004, f.squash * (1.0 + f.breathe * 0.008))
    rect = QRectF(-cw / 2, -target_h, cw, target_h)
    # hologram fade at the bottom so the bust cut blends into the projector light
    layer = QImage(int(cw) + 2, int(target_h) + 2, QImage.Format_ARGB32_Premultiplied)
    layer.fill(Qt.transparent)
    lp = QPainter(layer)
    lp.setRenderHint(QPainter.SmoothPixmapTransform)
    lp.drawImage(QRectF(0, 0, cw, target_h), img)
    lp.setCompositionMode(QPainter.CompositionMode_DestinationIn)
    g = QLinearGradient(QPointF(0, 0), QPointF(0, target_h))
    g.setColorAt(0.0, QColor(0, 0, 0, 255))
    g.setColorAt(0.80, QColor(0, 0, 0, 255))
    g.setColorAt(1.0, QColor(0, 0, 0, 0))
    lp.fillRect(layer.rect(), g)
    if f.tint:
        lp.setCompositionMode(QPainter.CompositionMode_SourceAtop)
        lp.fillRect(layer.rect(), QColor(255, 60, 80, 70) if f.tint == "red" else QColor(60, 110, 255, 45))
    # faint holographic scanlines + cyan rim
    lp.setCompositionMode(QPainter.CompositionMode_SourceAtop)
    lp.setPen(QColor(63, 240, 255, 7))
    for yy in range(0, layer.height(), 4):
        lp.drawLine(0, yy, layer.width(), yy)
    rim = QLinearGradient(QPointF(cw, 0), QPointF(cw * 0.82, 0))
    rim.setColorAt(0, QColor(63, 240, 255, int(60 * f.glow)))
    rim.setColorAt(1, QColor(63, 240, 255, 0))
    lp.fillRect(layer.rect(), rim)
    lp.end()
    p.drawImage(rect.topLeft(), layer)
    p.restore()
    # front effects (128 space)
    p.save()
    p.scale(size / 128.0, size / 128.0)
    artist._draw_extras(p, Pose(extras=[e for e in (f.extras or []) if e[0] not in artist._BEHIND]), None, False)
    p.restore()


def build(sheet_path: Path, out_dir: Path, frame: int = 256, log=print) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    anim = out_dir / "animations"
    anim.mkdir(exist_ok=True)
    log("Cutting out and aligning expressions…")
    variants = build_variants(sheet_path, log=log)
    imgs = {k: to_q(v) for k, v in variants.items()}
    (out_dir / "faces").mkdir(exist_ok=True)
    for k, q in imgs.items():
        q.save(str(out_dir / "faces" / f"{k}.png"))
    manifest = {"frame_size": frame, "kind": "portrait", "source": str(sheet_path), "states": {}}
    for state, a in ANIMATIONS.items():
        frames = _frames_for(state)
        sheet = QImage(frame * len(frames), frame, QImage.Format_ARGB32_Premultiplied)
        sheet.fill(Qt.transparent)
        p = QPainter(sheet)
        for i, f in enumerate(frames):
            p.save()
            p.translate(i * frame, 0)
            render_frame(p, imgs[f.face], f, frame, i / len(frames))
            p.restore()
        p.end()
        sheet.save(str(anim / f"{state.value}.png"))
        manifest["states"][state.value] = {"file": f"{state.value}.png", "frames": len(frames),
                                           "fps": a.fps, "loop": a.loop}
    (anim / "manifest.json").write_text(json.dumps(manifest, indent=2))
    # icon: face close-up on a navy tile
    icon_dir = out_dir / "icons"
    icon_dir.mkdir(exist_ok=True)
    face_img = imgs["smile"]
    for n in (48, 64, 128, 256, 512):
        ic = QImage(n, n, QImage.Format_ARGB32_Premultiplied)
        ic.fill(Qt.transparent)
        p = QPainter(ic)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        g = QRadialGradient(QPointF(n / 2, n * 0.4), n * 0.7)
        g.setColorAt(0, QColor("#1c3560"))
        g.setColorAt(1, QColor("#070f20"))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawRoundedRect(QRectF(n * 0.03, n * 0.03, n * 0.94, n * 0.94), n * 0.22, n * 0.22)
        p.setClipRect(QRectF(n * 0.03, n * 0.03, n * 0.94, n * 0.94))
        src = QRectF(118, 20, 310, 310)            # head region of the bust
        p.drawImage(QRectF(n * 0.06, n * 0.08, n * 0.88, n * 0.88), face_img, src)
        p.end()
        ic.save(str(icon_dir / f"icon-{n}.png"))
    preview = QImage(4 * 200, 5 * 200, QImage.Format_RGB32)
    preview.fill(QColor("#0d1b2e"))
    p = QPainter(preview)
    for idx, state in enumerate(ANIMATIONS):
        frames = _frames_for(state)
        f = frames[len(frames) // 3]
        p.save()
        p.translate((idx % 4) * 200, (idx // 4) * 200)
        render_frame(p, imgs[f.face], f, 200, 0.3)
        p.setPen(QColor("#3ff0ff"))
        p.drawText(QRectF(0, 182, 200, 18), Qt.AlignCenter, state.value)
        p.restore()
    p.end()
    preview.save(str(out_dir / "preview.png"))
    log(f"Avatar written to {out_dir}")
    return out_dir
