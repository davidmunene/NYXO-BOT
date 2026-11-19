"""Vector renderer for NYXO (art v2 — "realistic" pass).

Rendering model: a single key light from the upper-left, soft ambient
occlusion where parts meet, a cyan rim light from NYXO's holographic projector
pad, glossy specular highlights on the white shell, a deep glass visor with
bloom-lit LED eyes, and an arc-reactor-style chest core.

Everything is drawn in a 128x128 design space; callers scale the painter.
scripts/generate_sprites.py bakes this into transparent PNG spritesheets and
the runtime falls back to it if the spritesheets are missing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)

# --- palette ---------------------------------------------------------------
OUTLINE = QColor(30, 40, 58, 210)
SHELL_HI = QColor("#ffffff")
SHELL_MID = QColor("#e9edf2")
SHELL_LO = QColor("#aeb8c6")
SHELL_DEEP = QColor("#7d8899")
ORANGE = QColor("#ff8a1f")
ORANGE_HI = QColor("#ffc078")
ORANGE_LO = QColor("#c25400")
CYAN = QColor("#45f3ff")
CYAN_CORE = QColor("#e6fdff")
CYAN_DEEP = QColor("#00a9c7")
VISOR_TOP = QColor("#14243c")
VISOR_BOT = QColor("#03070f")
RED = QColor("#ff4057")
METAL_HI = QColor("#8d98ab")
METAL = QColor("#4a5468")
METAL_LO = QColor("#262d3b")

GROUND_Y = 121.0
CX = 64.0
LINE_W = 1.1


@dataclass
class Pose:
    body_dx: float = 0.0
    body_dy: float = 0.0
    tilt: float = 0.0          # whole-body rotation (deg) about the feet
    squash: float = 1.0
    head_dy: float = 0.0
    head_tilt: float = 0.0
    eyes: str = "normal"       # normal|happy|closed|sad|star|confused|focus|x|big
    blink: float = 0.0
    look: tuple[float, float] = (0.0, 0.0)
    mouth: str = "smile"       # smile|grin|open|talk|flat|frown|o|wavy|zigzag|none
    mouth_open: float = 0.5
    arm_l: float = 12.0        # deg; + = outward, 180 = straight up
    arm_r: float = 12.0
    leg_l: tuple[float, float] = (0.0, 0.0)
    leg_r: tuple[float, float] = (0.0, 0.0)
    antenna: float = 0.0
    glow: float = 0.75
    tint: str | None = None    # None|red|blue
    shadow: float = 1.0
    hud: float = 0.0           # projector-pad animation phase 0..1
    hud_on: bool = True
    extras: list[tuple[str, float]] = field(default_factory=list)


# --- helpers ---------------------------------------------------------------

def _pen(color: QColor = OUTLINE, width: float = LINE_W) -> QPen:
    p = QPen(color, width)
    p.setCapStyle(Qt.RoundCap)
    p.setJoinStyle(Qt.RoundJoin)
    return p


def _a(c: QColor, a: float) -> QColor:
    c = QColor(c)
    c.setAlphaF(max(0.0, min(1.0, a)))
    return c


def _shell(rect: QRectF, deep: bool = False) -> QRadialGradient:
    """Matte-gloss white shell lit from the upper-left."""
    g = QRadialGradient(rect.left() + rect.width() * 0.32, rect.top() + rect.height() * 0.25,
                        max(rect.width(), rect.height()) * 0.95)
    g.setColorAt(0.0, SHELL_HI)
    g.setColorAt(0.45, SHELL_MID)
    g.setColorAt(0.85, SHELL_LO)
    g.setColorAt(1.0, SHELL_DEEP if deep else SHELL_LO)
    return g


def _orange(rect: QRectF) -> QRadialGradient:
    g = QRadialGradient(rect.left() + rect.width() * 0.3, rect.top() + rect.height() * 0.3,
                        max(rect.width(), rect.height()) * 0.9)
    g.setColorAt(0.0, ORANGE_HI)
    g.setColorAt(0.45, ORANGE)
    g.setColorAt(1.0, ORANGE_LO)
    return g


def _metal(a: QPointF, b: QPointF) -> QLinearGradient:
    g = QLinearGradient(a, b)
    g.setColorAt(0.0, METAL_HI)
    g.setColorAt(0.5, METAL)
    g.setColorAt(1.0, METAL_LO)
    return g


def _eye_color(pose: Pose) -> QColor:
    return RED if pose.tint == "red" else CYAN


def _specular(p: QPainter, rect: QRectF, strength: float = 0.85) -> None:
    g = QRadialGradient(rect.center(), max(rect.width(), rect.height()) / 2)
    g.setColorAt(0.0, _a(SHELL_HI, strength))
    g.setColorAt(1.0, _a(SHELL_HI, 0.0))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(g))
    p.drawEllipse(rect)


def _rim(p: QPainter, path: QPainterPath, glow: float, side: float = 1.0) -> None:
    """Cyan rim light on the right edge (light from the holo pad)."""
    p.save()
    p.setClipPath(path)
    r = path.boundingRect()
    g = QLinearGradient(QPointF(r.right(), 0), QPointF(r.right() - r.width() * 0.22, 0))
    g.setColorAt(0.0, _a(CYAN, 0.33 * glow * side))
    g.setColorAt(1.0, _a(CYAN, 0.0))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(g))
    p.drawRect(r)
    # bottom bounce light
    g2 = QLinearGradient(QPointF(0, r.bottom()), QPointF(0, r.bottom() - r.height() * 0.25))
    g2.setColorAt(0.0, _a(CYAN, 0.16 * glow))
    g2.setColorAt(1.0, _a(CYAN, 0.0))
    p.setBrush(QBrush(g2))
    p.drawRect(r)
    p.restore()


def _bloom(p: QPainter, path: QPainterPath, color: QColor, intensity: float,
           fill: bool = True, stroke_w: float = 2.2) -> None:
    """LED with soft bloom halo and a hot white core."""
    r = path.boundingRect()
    halo = QRadialGradient(r.center(), max(r.width(), r.height()) * 1.25 + 4)
    halo.setColorAt(0.0, _a(color, 0.42 * intensity))
    halo.setColorAt(0.55, _a(color, 0.14 * intensity))
    halo.setColorAt(1.0, _a(color, 0.0))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(halo))
    p.drawEllipse(r.center(), max(r.width(), r.height()) * 1.25 + 4, max(r.width(), r.height()) * 1.25 + 4)
    core = QColor(color).lighter(135) if color != RED else QColor("#ffb3bc")
    if fill:
        g = QRadialGradient(r.center(), max(r.width(), r.height()) * 0.7)
        g.setColorAt(0.0, CYAN_CORE if color == CYAN else core)
        g.setColorAt(0.55, color)
        g.setColorAt(1.0, color.darker(115))
        p.setBrush(QBrush(g))
        p.drawPath(path)
    else:
        p.setPen(_pen(_a(color, 0.45 * intensity), stroke_w + 2.6))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.setPen(_pen(color, stroke_w))
        p.drawPath(path)
        p.setPen(_pen(_a(CYAN_CORE if color == CYAN else core, 0.9), stroke_w * 0.4))
        p.drawPath(path)


# --- holographic projector pad ---------------------------------------------

def _draw_pad(p: QPainter, pose: Pose) -> None:
    s = max(0.45, pose.shadow)
    cx, cy = CX, GROUND_Y
    # contact / AO shadow
    g = QRadialGradient(QPointF(cx, cy), 24 * s)
    g.setColorAt(0, QColor(5, 10, 22, int(120 * s)))
    g.setColorAt(1, QColor(5, 10, 22, 0))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(g))
    p.drawEllipse(QRectF(cx - 24 * s, cy - 4.2 * s, 48 * s, 8.4 * s))
    if not pose.hud_on:
        return
    gl = pose.glow
    # projector glow
    g2 = QRadialGradient(QPointF(cx, cy), 34)
    g2.setColorAt(0, _a(CYAN, 0.20 * gl))
    g2.setColorAt(1, _a(CYAN, 0.0))
    p.setBrush(QBrush(g2))
    p.drawEllipse(QRectF(cx - 34, cy - 7, 68, 14))
    # rings
    for rx, ry, a, w in ((30, 5.6, 0.55, 1.0), (22, 4.1, 0.35, 0.8)):
        p.setPen(_pen(_a(CYAN, a * gl), w))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), rx, ry)
    # rotating tick marks (perspective ellipse)
    p.setPen(_pen(_a(CYAN, 0.8 * gl), 1.1))
    for i in range(12):
        ang = (i / 12 + pose.hud) * math.tau
        x, y = math.cos(ang), math.sin(ang)
        front = 0.35 + 0.65 * (y + 1) / 2          # brighter at the front
        p.setPen(_pen(_a(CYAN, front * 0.85 * gl), 1.1))
        p.drawLine(QPointF(cx + x * 30, cy + y * 5.6), QPointF(cx + x * 33.5, cy + y * 6.25))
    # sweeping arc segment
    p.setPen(_pen(_a(ORANGE, 0.75 * gl), 1.4))
    start = int(-pose.hud * 360 * 16) % (360 * 16)
    p.drawArc(QRectF(cx - 26, cy - 4.85, 52, 9.7), start, 70 * 16)


# --- body parts ------------------------------------------------------------

def _draw_leg(p: QPainter, x: float, dx: float, lift: float) -> None:
    top = 103.0
    foot_y = 115.5 - lift
    fx = x + dx
    p.setPen(_pen(METAL_LO, 6.2))
    p.drawLine(QPointF(x, top), QPointF(fx, foot_y))
    pen = QPen(QBrush(_metal(QPointF(x - 3, 0), QPointF(x + 3, 0))), 4.4)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.drawLine(QPointF(x, top), QPointF(fx, foot_y))
    # knee joint
    kx, ky = (x + fx) / 2, (top + foot_y) / 2
    p.setPen(Qt.NoPen)
    p.setBrush(_metal(QPointF(kx - 2, ky - 2), QPointF(kx + 2, ky + 2)))
    p.drawEllipse(QPointF(kx, ky), 2.6, 2.6)
    # foot (rounded shoe with orange sole)
    foot = QRectF(fx - 9, foot_y - 4, 18, 8.5)
    path = QPainterPath()
    path.addRoundedRect(foot, 4.2, 4.2)
    p.setPen(_pen())
    p.setBrush(_shell(foot, deep=True))
    p.drawPath(path)
    sole = QRectF(fx - 9, foot_y + 1.6, 18, 2.9)
    p.setPen(Qt.NoPen)
    p.setBrush(_orange(sole))
    p.drawRoundedRect(sole, 1.4, 1.4)
    _specular(p, QRectF(fx - 6.5, foot_y - 3.2, 7, 2.6), 0.9)


def _draw_arm(p: QPainter, shoulder: QPointF, angle: float, left: bool, glow: float) -> None:
    p.save()
    p.translate(shoulder)
    p.rotate(angle if left else -angle)
    length = 15.5
    # shoulder joint
    p.setPen(_pen())
    p.setBrush(_metal(QPointF(-3, -3), QPointF(3, 3)))
    p.drawEllipse(QPointF(0, 0), 3.6, 3.6)
    arm = QRectF(-3.5, -0.5, 7.0, length)
    path = QPainterPath()
    path.addRoundedRect(arm, 3.5, 3.5)
    p.setPen(_pen())
    p.setBrush(_shell(arm))
    p.drawPath(path)
    p.setPen(_pen(_a(OUTLINE, 0.35), 0.6))
    p.drawLine(QPointF(-3.1, length * 0.55), QPointF(3.1, length * 0.55))
    _rim(p, path, glow, 0.8)
    # hand: orange mitten with a white thumb nub
    hand = QRectF(-4.9, length - 3.2, 9.8, 9.2)
    p.setPen(_pen())
    p.setBrush(_orange(hand))
    p.drawEllipse(hand)
    p.setBrush(_shell(QRectF(-6.2, length - 1, 3.6, 3.6)))
    p.drawEllipse(QRectF(-6.2 if left else 2.6, length - 0.6, 3.6, 3.6))
    _specular(p, QRectF(-3.2, length - 2.2, 4, 3), 0.6)
    p.restore()


def _draw_core(p: QPainter, pose: Pose) -> None:
    c = QPointF(CX, 89.8)
    color = _eye_color(pose)
    gl = pose.glow
    halo = QRadialGradient(c, 11)
    halo.setColorAt(0, _a(color, 0.5 * gl))
    halo.setColorAt(1, _a(color, 0.0))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(halo))
    p.drawEllipse(c, 11, 11)
    # housing
    p.setPen(_pen(OUTLINE, 0.9))
    p.setBrush(_metal(QPointF(CX - 6, 84), QPointF(CX + 6, 96)))
    p.drawEllipse(c, 6.2, 6.2)
    # ring segments
    p.setPen(_pen(_a(color, 0.95), 1.3))
    for i in range(6):
        p.drawArc(QRectF(c.x() - 4.6, c.y() - 4.6, 9.2, 9.2), int((i * 60 + 8 + pose.hud * 360) * 16), 40 * 16)
    core = QRadialGradient(c, 3)
    core.setColorAt(0, CYAN_CORE if color == CYAN else QColor("#ffd0d6"))
    core.setColorAt(1, color)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(core))
    p.drawEllipse(c, 2.6 + 0.4 * gl, 2.6 + 0.4 * gl)


def _draw_body(p: QPainter, pose: Pose) -> None:
    body = QRectF(CX - 22, 77, 44, 31.5)
    path = QPainterPath()
    path.addRoundedRect(body, 16, 14)
    p.setPen(_pen())
    p.setBrush(_shell(body, deep=True))
    p.drawPath(path)
    _rim(p, path, pose.glow)
    # ambient occlusion under the head
    p.save()
    p.setClipPath(path)
    ao = QLinearGradient(QPointF(0, 77), QPointF(0, 86))
    ao.setColorAt(0, QColor(20, 28, 45, 120))
    ao.setColorAt(1, QColor(20, 28, 45, 0))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(ao))
    p.drawRect(body)
    p.restore()
    # panel seams + side vents
    p.setPen(_pen(_a(OUTLINE, 0.35), 0.7))
    p.drawArc(QRectF(CX - 18, 81, 36, 25), 200 * 16, 140 * 16)
    for i in range(3):
        y = 93 + i * 2.6
        p.drawLine(QPointF(CX - 19.5, y), QPointF(CX - 16, y))
        p.drawLine(QPointF(CX + 16, y), QPointF(CX + 19.5, y))
    # orange collar band
    band = QRectF(CX - 15, 78.2, 30, 3.4)
    p.setPen(Qt.NoPen)
    p.setBrush(_orange(band))
    p.drawRoundedRect(band, 1.7, 1.7)
    _draw_core(p, pose)
    _specular(p, QRectF(CX - 17, 82, 10, 6), 0.8)


def _draw_antenna(p: QPainter, pose: Pose) -> None:
    p.save()
    p.translate(CX, 33.5)
    p.rotate(pose.antenna)
    p.setPen(_pen(METAL_LO, 3.0))
    p.drawLine(QPointF(0, 0), QPointF(0, -10))
    pen = QPen(QBrush(_metal(QPointF(-1.5, 0), QPointF(1.5, 0))), 1.8)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.drawLine(QPointF(0, 0), QPointF(0, -10))
    glow_c = RED if pose.tint == "red" else ORANGE
    halo = QRadialGradient(QPointF(0, -13), 10)
    halo.setColorAt(0, _a(glow_c, 0.45 * pose.glow))
    halo.setColorAt(1, _a(glow_c, 0.0))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(halo))
    p.drawEllipse(QPointF(0, -13), 10, 10)
    tip = QRectF(-4.4, -17.4, 8.8, 8.8)
    p.setPen(_pen())
    p.setBrush(_orange(tip))
    p.drawEllipse(tip)
    _specular(p, QRectF(-3.2, -16.4, 3.6, 2.8), 0.95)
    p.restore()


def _draw_ear(p: QPainter, x: float, right: bool, glow: float) -> None:
    ear = QRectF(x - 6, 49.5, 12, 15)
    path = QPainterPath()
    path.addRoundedRect(ear, 5.5, 5.5)
    p.setPen(_pen())
    p.setBrush(_orange(ear))
    p.drawPath(path)
    # speaker grille ring
    p.setPen(_pen(_a(ORANGE_LO, 0.9), 0.8))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(QPointF(x, 57), 3.3, 4.2)
    p.setPen(Qt.NoPen)
    p.setBrush(_a(CYAN, 0.55 * glow) if right else _a(ORANGE_LO, 0.8))
    p.drawEllipse(QPointF(x, 57), 1.2, 1.5)
    _specular(p, QRectF(x - 4.5, 50.5, 4, 3.5), 0.7)


def _eye_shape(kind: str, cx: float, cy: float, pose: Pose, right: bool) -> QPainterPath:
    path = QPainterPath()
    open_f = 1.0 - max(0.0, min(1.0, pose.blink))
    if kind in ("normal", "big"):
        w, h = (8.6, 11.5) if kind == "normal" else (10.5, 13.5)
        h = max(1.4, h * open_f)
        path.addRoundedRect(QRectF(cx - w / 2, cy - h / 2, w, h), min(w, h) / 2.1, min(w, h) / 2.1)
    elif kind == "focus":
        path.addRoundedRect(QRectF(cx - 5.5, cy - 2, 11, 4), 2, 2)
    elif kind == "confused":
        if right:
            path.addRoundedRect(QRectF(cx - 5, cy - 1.4, 10, 3.4), 1.7, 1.7)
        else:
            path.addRoundedRect(QRectF(cx - 5.4, cy - 7.4, 10.8, 14.8), 5, 5)
    elif kind == "star":
        r1, r2 = 7.2, 2.9
        for i in range(10):
            a = -math.pi / 2 + i * math.pi / 5
            r = r1 if i % 2 == 0 else r2
            pt = QPointF(cx + r * math.cos(a), cy + r * math.sin(a))
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        path.closeSubpath()
    elif kind == "sad":
        d = 1 if right else -1
        path.moveTo(cx - 4.4, cy - 2 - 2.4 * d)
        path.lineTo(cx + 4.4, cy - 2 + 2.4 * d)
        path.lineTo(cx + 4.4, cy + 3)
        path.quadTo(cx, cy + 7.6, cx - 4.4, cy + 3)
        path.closeSubpath()
    return path


def _stroke_eye(kind: str, cx: float, cy: float) -> QPainterPath:
    path = QPainterPath()
    if kind == "happy":
        path.moveTo(cx - 4.8, cy + 2.4)
        path.quadTo(cx, cy - 6.2, cx + 4.8, cy + 2.4)
    elif kind == "closed":
        path.moveTo(cx - 4.8, cy)
        path.quadTo(cx, cy + 4.2, cx + 4.8, cy)
    elif kind == "x":
        path.moveTo(cx - 3.8, cy - 3.8)
        path.lineTo(cx + 3.8, cy + 3.8)
        path.moveTo(cx + 3.8, cy - 3.8)
        path.lineTo(cx - 3.8, cy + 3.8)
    return path


def _draw_mouth(p: QPainter, pose: Pose, color: QColor) -> None:
    cx, cy = CX + pose.look[0] * 0.5, 66.4 + pose.look[1] * 0.3
    m = pose.mouth
    path = QPainterPath()
    filled = False
    if m == "smile":
        path.moveTo(cx - 4.6, cy - 0.8)
        path.quadTo(cx, cy + 3.6, cx + 4.6, cy - 0.8)
    elif m == "grin":
        path.moveTo(cx - 6.5, cy - 1.4)
        path.quadTo(cx, cy + 5.6, cx + 6.5, cy - 1.4)
        path.closeSubpath()
        filled = True
    elif m in ("open", "talk"):
        if m == "talk":
            # equaliser-style voice bars: reads as speech, scales with audio level
            lvl = max(0.05, pose.mouth_open)
            for i, k in enumerate((0.45, 0.8, 1.0, 0.8, 0.45)):
                h = 1.2 + 6.0 * lvl * k * (0.85 + 0.15 * math.sin(i * 2.1 + lvl * 9))
                path.addRoundedRect(QRectF(cx - 6.2 + i * 2.7, cy - h / 2 + 0.3, 1.7, h), 0.8, 0.8)
        else:
            h = 1.5 + 5.5 * pose.mouth_open
            path.addRoundedRect(QRectF(cx - 4.5, cy - h / 2 + 0.5, 9, h), min(9, h) / 2, min(9, h) / 2)
        filled = True
    elif m == "flat":
        path.moveTo(cx - 4, cy)
        path.lineTo(cx + 4, cy)
    elif m == "frown":
        path.moveTo(cx - 4.8, cy + 2)
        path.quadTo(cx, cy - 2.8, cx + 4.8, cy + 2)
    elif m == "o":
        path.addEllipse(QPointF(cx, cy + 0.5), 2.1, 2.5)
        filled = True
    elif m == "wavy":
        path.moveTo(cx - 6, cy)
        path.quadTo(cx - 3, cy - 3, cx, cy)
        path.quadTo(cx + 3, cy + 3, cx + 6, cy)
    elif m == "zigzag":
        path.moveTo(cx - 6, cy)
        for i, x in enumerate((-3, 0, 3, 6)):
            path.lineTo(cx + x, cy + (-2.4 if i % 2 == 0 else 2.4))
    else:
        return
    if filled:
        p.setPen(Qt.NoPen)
        g = QLinearGradient(QPointF(0, cy - 4), QPointF(0, cy + 4))
        g.setColorAt(0, CYAN_CORE if color == CYAN else color.lighter(140))
        g.setColorAt(1, color)
        halo = QRadialGradient(QPointF(cx, cy), 9)
        halo.setColorAt(0, _a(color, 0.3 * pose.glow))
        halo.setColorAt(1, _a(color, 0))
        p.setBrush(QBrush(halo))
        p.drawEllipse(QPointF(cx, cy), 9, 6)
        p.setBrush(QBrush(g))
        p.drawPath(path)
    else:
        _bloom(p, path, color, pose.glow * 0.8, fill=False, stroke_w=1.7)


def _draw_head(p: QPainter, pose: Pose) -> None:
    _draw_antenna(p, pose)
    _draw_ear(p, CX - 32, False, pose.glow)
    _draw_ear(p, CX + 32, True, pose.glow)
    head = QRectF(CX - 31, 33, 62, 47)
    hp = QPainterPath()
    hp.addRoundedRect(head, 21, 19)
    p.setPen(_pen())
    p.setBrush(_shell(head))
    p.drawPath(hp)
    _rim(p, hp, pose.glow)
    # crown seam + orange brow light
    p.setPen(_pen(_a(OUTLINE, 0.3), 0.7))
    p.drawArc(QRectF(CX - 26, 36, 52, 30), 30 * 16, 120 * 16)
    stripe = QRectF(CX + 7, 36.6, 10, 3.2)
    p.setPen(Qt.NoPen)
    p.setBrush(_orange(stripe))
    p.drawRoundedRect(stripe, 1.6, 1.6)
    _specular(p, QRectF(CX - 22, 35.5, 22, 9), 0.95)

    # visor: recessed glass
    visor = QRectF(CX - 24, 43, 48, 30)
    bezel = QPainterPath()
    bezel.addRoundedRect(visor.adjusted(-1.6, -1.6, 1.6, 1.6), 14.5, 14.5)
    p.setPen(Qt.NoPen)
    p.setBrush(_metal(visor.topLeft(), visor.bottomRight()))
    p.drawPath(bezel)
    vg = QLinearGradient(visor.topLeft(), visor.bottomLeft())
    top, bot = VISOR_TOP, VISOR_BOT
    if pose.tint == "red":
        top, bot = QColor("#3a1220"), QColor("#12050a")
    elif pose.tint == "blue":
        top, bot = QColor("#152448"), QColor("#060b1d")
    vg.setColorAt(0, top)
    vg.setColorAt(1, bot)
    vpath = QPainterPath()
    vpath.addRoundedRect(visor, 13, 13)
    p.setBrush(QBrush(vg))
    p.drawPath(vpath)

    color = _eye_color(pose)
    lx, ly = pose.look
    ex1, ex2, ey = CX - 10 + lx, CX + 10 + lx, 56.5 + ly
    p.save()
    p.setClipPath(vpath)
    # faint scanlines
    p.setPen(_pen(_a(CYAN, 0.045), 0.5))
    y = visor.top() + 2
    while y < visor.bottom():
        p.drawLine(QPointF(visor.left(), y), QPointF(visor.right(), y))
        y += 2.2
    if pose.eyes in ("happy", "closed", "x"):
        for ex in (ex1, ex2):
            _bloom(p, _stroke_eye(pose.eyes, ex, ey), color, pose.glow, fill=False, stroke_w=2.3)
    else:
        for i, ex in enumerate((ex1, ex2)):
            _bloom(p, _eye_shape(pose.eyes, ex, ey, pose, right=bool(i)), color, pose.glow)
            if pose.eyes in ("normal", "big") and pose.blink < 0.6:
                p.setPen(Qt.NoPen)
                p.setBrush(_a(SHELL_HI, 0.9))
                p.drawEllipse(QPointF(ex - 1.7, ey - 2.7), 1.3, 1.3)
    _draw_mouth(p, pose, color)
    # curved glass reflections
    p.setPen(Qt.NoPen)
    refl = QPainterPath()
    refl.moveTo(visor.left() + 5, visor.top() + 2.5)
    refl.quadTo(visor.center().x() - 4, visor.top() - 1, visor.right() - 6, visor.top() + 2.5)
    refl.quadTo(visor.center().x(), visor.top() + 5.5, visor.left() + 5, visor.top() + 2.5)
    p.setBrush(_a(SHELL_HI, 0.22))
    p.drawPath(refl)
    streak = QPainterPath()
    streak.moveTo(visor.left() + 6, visor.top() + 6)
    streak.lineTo(visor.left() + 12, visor.top() + 6)
    streak.lineTo(visor.left() + 7, visor.bottom() - 6)
    streak.lineTo(visor.left() + 3.5, visor.bottom() - 7)
    streak.closeSubpath()
    p.setBrush(_a(SHELL_HI, 0.06))
    p.drawPath(streak)
    p.restore()
    p.setPen(_pen(_a(OUTLINE, 0.9), 0.9))
    p.setBrush(Qt.NoBrush)
    p.drawPath(vpath)


# --- extras ----------------------------------------------------------------

def _text_path(text: str, x: float, y: float, size: float, weight=QFont.Black) -> QPainterPath:
    f = QFont("DejaVu Sans")
    f.setPixelSize(int(size))
    f.setWeight(weight)
    path = QPainterPath()
    path.addText(QPointF(x, y), f, text)
    return path


def _holo_text(p: QPainter, path: QPainterPath, color: QColor, a: float = 1.0) -> None:
    p.setPen(_pen(_a(color, 0.35 * a), 2.6))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)
    p.setPen(_pen(_a(color.lighter(130), a), 0.8))
    p.setBrush(_a(color, 0.85 * a))
    p.drawPath(path)


def _x_zzz(p, ph, pose):
    for i in range(3):
        t = (ph + i / 3) % 1.0
        _holo_text(p, _text_path("Z", CX + 26 + t * 18, 34 - t * 26, 7 + t * 8), CYAN,
                   math.sin(t * math.pi))


def _x_question(p, ph, pose):
    _holo_text(p, _text_path("?", CX + 30, 30 + math.sin(ph * math.tau) * 2.5, 22), ORANGE)


def _x_exclaim(p, ph, pose):
    s = 1.0 + 0.15 * math.sin(ph * math.tau)
    p.translate(CX + 34, 26)
    p.scale(s, s)
    _holo_text(p, _text_path("!", -4, 0, 22), RED)


def _x_dots(p, ph, pose):
    # holographic "processing" panel
    panel = QRectF(CX + 26, 4, 34, 18)
    p.setPen(_pen(_a(CYAN, 0.8), 0.9))
    p.setBrush(_a(CYAN, 0.12))
    p.drawRoundedRect(panel, 4, 4)
    p.drawLine(QPointF(CX + 28, 24), QPointF(CX + 22, 32))
    n = int(ph * 4) % 4
    for i in range(3):
        p.setPen(Qt.NoPen)
        p.setBrush(_a(CYAN, 0.95 if i < n else 0.22))
        p.drawEllipse(QPointF(panel.left() + 9 + i * 8, panel.center().y()), 2.3, 2.3)


def _sparkle(p, x, y, r, color):
    path = QPainterPath()
    path.moveTo(x, y - r)
    path.quadTo(x, y, x + r, y)
    path.quadTo(x, y, x, y + r)
    path.quadTo(x, y, x - r, y)
    path.quadTo(x, y, x, y - r)
    p.setPen(Qt.NoPen)
    p.setBrush(_a(color, 0.3))
    p.drawEllipse(QPointF(x, y), r * 0.9, r * 0.9)
    p.setBrush(color)
    p.drawPath(path)


def _x_sparkles(p, ph, pose):
    for x, y, off, c in ((CX - 40, 40, 0.0, CYAN), (CX + 40, 50, 0.33, ORANGE),
                         (CX - 34, 78, 0.66, ORANGE), (CX + 36, 22, 0.5, CYAN)):
        r = 5.5 * math.sin(((ph + off) % 1.0) * math.pi)
        if r > 0.4:
            _sparkle(p, x, y, r, c)


_CONFETTI = (ORANGE, CYAN, QColor("#ffd23f"), QColor("#ff5fa2"), QColor("#7cff6b"))


def _x_confetti(p, ph, pose):
    for i in range(16):
        seed = (i * 37) % 100 / 100
        t = (ph + seed) % 1.0
        p.save()
        p.translate(8 + (i * 53) % 112 + math.sin((t + seed) * 6.28) * 4, -4 + t * 70)
        p.rotate((t * 720 + i * 40) % 360)
        p.setPen(Qt.NoPen)
        p.setBrush(_a(_CONFETTI[i % 5], 1.0 - max(0.0, (t - 0.75) * 4)))
        p.drawRect(QRectF(-2.2, -1.2, 4.4, 2.4))
        p.restore()


def _x_waves(p, ph, pose):
    for i in range(3):
        t = (ph + i / 3) % 1.0
        r = 6 + t * 14
        p.setPen(_pen(_a(CYAN, 1 - t), 1.6))
        p.setBrush(Qt.NoBrush)
        p.drawArc(QRectF(CX - 40 - r, 57 - r, 2 * r, 2 * r), 120 * 16, 120 * 16)


def _x_tear(p, ph, pose):
    y = 63 + ph * 26
    a = 1 - max(0.0, (ph - 0.7) * 3.3)
    x = CX + 13
    path = QPainterPath()
    path.moveTo(x, y - 4)
    path.quadTo(x + 3.2, y + 0.5, x, y + 2.6)
    path.quadTo(x - 3.2, y + 0.5, x, y - 4)
    p.setPen(_pen(_a(OUTLINE, a * 0.7), 0.7))
    p.setBrush(_a(QColor("#7fdcff"), a))
    p.drawPath(path)


def _x_laptop(p, ph, pose):
    panel = QPainterPath()
    panel.moveTo(CX - 26, 106)
    panel.lineTo(CX + 26, 106)
    panel.lineTo(CX + 20, 94)
    panel.lineTo(CX - 20, 94)
    panel.closeSubpath()
    p.setPen(_pen(_a(CYAN, 0.9), 1.0))
    p.setBrush(_a(CYAN, 0.2))
    p.drawPath(panel)
    for i in range(3):
        w = 12 + ((i * 7 + int(ph * 8)) % 5) * 5
        p.setPen(_pen(_a(CYAN, 0.8), 0.9))
        p.drawLine(QPointF(CX - w / 2, 97 + i * 3), QPointF(CX + w / 2, 97 + i * 3))
    scr = QRectF(CX + 28, 62, 28, 24)
    p.setPen(_pen(_a(CYAN, 0.85), 0.9))
    p.setBrush(_a(CYAN, 0.12))
    p.drawRoundedRect(scr, 3, 3)
    for i in range(5):
        w = 6 + ((i * 5 + int(ph * 8)) % 4) * 4
        c = ORANGE if i == 2 else CYAN
        p.setPen(_pen(_a(c, 0.85), 0.9))
        p.drawLine(QPointF(scr.left() + 4, scr.top() + 5 + i * 3.6),
                   QPointF(scr.left() + 4 + w, scr.top() + 5 + i * 3.6))


def _x_speed(p, ph, pose):
    for i in range(3):
        off = ((ph * 3 + i * 0.3) % 1.0) * 10
        p.setPen(_pen(_a(CYAN, 0.55), 1.6))
        p.drawLine(QPointF(CX - 34 - off, 70 + i * 12), QPointF(CX - 48 - off, 70 + i * 12))


def _x_sparks(p, ph, pose):
    for i, (x, y) in enumerate(((CX - 30, 38), (CX + 22, 30), (CX - 36, 70))):
        if int(ph * 6 + i) % 2 == 0:
            path = QPainterPath()
            path.moveTo(x, y - 5)
            path.lineTo(x + 2, y)
            path.lineTo(x - 1, y)
            path.lineTo(x + 1, y + 5)
            p.setPen(_pen(ORANGE, 1.4))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)


def _x_hearts(p, ph, pose):
    t = ph % 1.0
    x, y = CX - 36, 40 - t * 18
    path = QPainterPath()
    path.moveTo(x, y + 4)
    path.cubicTo(x - 7, y - 1, x - 3, y - 7, x, y - 3)
    path.cubicTo(x + 3, y - 7, x + 7, y - 1, x, y + 4)
    p.setPen(Qt.NoPen)
    p.setBrush(_a(ORANGE, math.sin(t * math.pi)))
    p.drawPath(path)


_EXTRAS = {
    "zzz": _x_zzz, "question": _x_question, "exclaim": _x_exclaim, "dots": _x_dots,
    "sparkles": _x_sparkles, "confetti": _x_confetti, "waves": _x_waves, "tear": _x_tear,
    "laptop": _x_laptop, "speed": _x_speed, "sparks": _x_sparks, "hearts": _x_hearts,
}
_BEHIND = {"speed", "confetti"}


def _draw_extras(p: QPainter, pose: Pose, names: set[str] | None, behind: bool) -> None:
    for name, ph in pose.extras:
        if (name in _BEHIND) != behind:
            continue
        fn = _EXTRAS.get(name)
        if fn:
            p.save()
            fn(p, ph, pose)
            p.restore()


# --- public API ------------------------------------------------------------

def draw_robot(p: QPainter, pose: Pose, size: float) -> None:
    """Draw NYXO into a size x size square at the painter's origin."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    p.scale(size / 128.0, size / 128.0)
    _draw_extras(p, pose, None, behind=True)
    _draw_pad(p, pose)

    p.save()
    p.translate(CX + pose.body_dx, GROUND_Y)
    p.rotate(pose.tilt)
    p.scale(1.0, pose.squash)
    p.translate(-CX, -GROUND_Y + pose.body_dy)
    _draw_leg(p, CX - 11, *pose.leg_l)
    _draw_leg(p, CX + 11, *pose.leg_r)
    _draw_body(p, pose)
    p.save()
    p.translate(CX, 80)
    p.rotate(pose.head_tilt)
    p.translate(-CX, -80 + pose.head_dy)
    _draw_head(p, pose)
    p.restore()
    _draw_arm(p, QPointF(CX - 20, 84), pose.arm_l, True, pose.glow)
    _draw_arm(p, QPointF(CX + 20, 84), pose.arm_r, False, pose.glow)
    p.restore()

    _draw_extras(p, pose, None, behind=False)
    p.restore()


def draw_icon(p: QPainter, size: float) -> None:
    """App icon: NYXO's head on a navy tile with a HUD ring."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    p.scale(size / 128.0, size / 128.0)
    tile = QRectF(4, 4, 120, 120)
    g = QRadialGradient(QPointF(64, 52), 90)
    g.setColorAt(0, QColor("#1c3560"))
    g.setColorAt(1, QColor("#070f20"))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(g))
    p.drawRoundedRect(tile, 28, 28)
    p.setPen(_pen(_a(CYAN, 0.35), 1.2))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(QPointF(64, 66), 52, 52)
    p.setPen(_pen(_a(ORANGE, 0.8), 2))
    p.drawArc(QRectF(12, 14, 104, 104), 20 * 16, 60 * 16)
    p.translate(64, 70)
    p.scale(1.5, 1.5)
    p.translate(-64, -56)
    _draw_head(p, Pose(eyes="normal", mouth="smile", glow=1.0))
    p.restore()
