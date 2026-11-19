"""Character states and their frame-by-frame poses.

Each state maps to a list of Pose objects (one per frame) plus a playback
rate. Walking/running poses are drawn facing right; the runtime mirrors them
for leftward movement.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from .artist import Pose

TAU = 2 * math.pi


class State(str, Enum):
    IDLE = "idle"
    WALKING = "walking"
    RUNNING = "running"
    SLEEPING = "sleeping"
    THINKING = "thinking"
    HAPPY = "happy"
    EXCITED = "excited"
    CONFUSED = "confused"
    SAD = "sad"
    TALKING = "talking"
    LISTENING = "listening"
    WORKING = "working"
    ERROR = "error"
    CELEBRATING = "celebrating"
    WAVING = "waving"
    DRAGGED = "dragged"
    BLINK = "blink"            # short overlays the companion plays at random
    GLANCE = "glance"


@dataclass(frozen=True)
class Anim:
    frames: int
    fps: float
    loop: bool
    build: Callable[[float, int], Pose]   # (phase 0..1, frame index) -> Pose


def _idle(t: float, i: int) -> Pose:
    s = math.sin(t * TAU)
    return Pose(body_dy=-1.5 + s * 1.3, head_dy=math.sin(t * TAU - 0.6) * 0.5,
                arm_l=12 + s * 3, arm_r=12 + math.sin(t * TAU + 0.4) * 3,
                antenna=math.sin(t * TAU + 1) * 4, glow=0.78 + 0.12 * s,
                mouth="smile", shadow=0.95 - s * 0.04)


def _blink(t: float, i: int) -> Pose:
    pose = _idle(0.0, 0)
    pose.blink = (0.55, 1.0, 0.45)[i % 3]
    return pose


def _glance(t: float, i: int) -> Pose:
    # look left, hold, look right, hold, return
    keys = [(0, 0), (-3, 0), (-3.2, 0.3), (-3.2, 0.3), (-1, 0), (2.8, -0.4), (3.2, -0.4),
            (3.2, -0.4), (1, 0), (0, 0)]
    pose = _idle(i / 10, i)
    pose.look = keys[i % len(keys)]
    pose.head_tilt = pose.look[0] * 0.9
    return pose


def _walk(t: float, i: int) -> Pose:
    s = math.sin(t * TAU)
    return Pose(body_dy=-abs(s) * 1.8, tilt=4, look=(2.6, 0), mouth="smile",
                arm_l=14 - s * 22, arm_r=14 + s * 22,
                leg_l=(s * 4, max(0.0, s) * 4.5), leg_r=(-s * 4, max(0.0, -s) * 4.5),
                antenna=-6 - s * 4, head_tilt=s * 1.5)


def _run(t: float, i: int) -> Pose:
    s = math.sin(t * TAU)
    return Pose(body_dy=-abs(s) * 4 - 1, tilt=11, look=(3.2, 0), mouth="grin",
                arm_l=30 - s * 50, arm_r=30 + s * 50,
                leg_l=(s * 7, max(0.0, s) * 7), leg_r=(-s * 7, max(0.0, -s) * 7),
                antenna=-18 - s * 6, eyes="normal", extras=[("speed", t)],
                shadow=0.85)


def _sleep(t: float, i: int) -> Pose:
    s = math.sin(t * TAU)
    return Pose(eyes="closed", mouth="o", head_tilt=9, head_dy=2.5 + s * 0.6,
                body_dy=s * 0.8 + 1, arm_l=5, arm_r=5, antenna=14 + s * 3,
                glow=0.25 + 0.1 * s, extras=[("zzz", t)], squash=0.98)


def _think(t: float, i: int) -> Pose:
    s = math.sin(t * TAU)
    return Pose(eyes="normal", look=(-3, -3.2), mouth="flat", arm_r=-132 + s * 4,
                arm_l=10, head_tilt=-5, antenna=math.sin(t * TAU * 2) * 6,
                glow=0.6 + 0.3 * abs(s), extras=[("dots", t)], blink=0.25)


def _happy(t: float, i: int) -> Pose:
    s = math.sin(t * TAU)
    return Pose(eyes="happy", mouth="grin", body_dy=-abs(s) * 4, squash=1 - abs(math.cos(t * TAU)) * 0.03,
                arm_l=38 + s * 10, arm_r=38 - s * 10, antenna=s * 10,
                head_tilt=s * 3, extras=[("sparkles", t)], glow=0.95)


def _excited(t: float, i: int) -> Pose:
    s = math.sin(t * TAU)
    h = abs(s)
    return Pose(eyes="star", mouth="open", mouth_open=0.9, body_dy=-h * 10,
                squash=1.0 - (1 - h) * 0.06, arm_l=150 + s * 18, arm_r=150 - s * 18,
                leg_l=(-2, h * 2), leg_r=(2, h * 2), antenna=s * 16,
                shadow=1 - h * 0.45, extras=[("sparkles", t)], glow=1.0)


def _confused(t: float, i: int) -> Pose:
    s = math.sin(t * TAU)
    return Pose(eyes="confused", mouth="wavy", head_tilt=10 + s * 4,
                arm_r=158 + math.sin(t * TAU * 3) * 8, arm_l=14,
                antenna=12 + s * 8, extras=[("question", t)])


def _sad(t: float, i: int) -> Pose:
    s = math.sin(t * TAU)
    return Pose(eyes="sad", mouth="frown", head_dy=3, head_tilt=-4, body_dy=1.5 + s * 0.4,
                arm_l=3, arm_r=3, antenna=-26 + s * 2, glow=0.35, tint="blue",
                extras=[("tear", t)], look=(0, 1.5))


def _talk(t: float, i: int) -> Pose:
    """Frame i = loudness level i/(n-1): the runtime picks frames from the
    live audio level for lip-sync (or cycles them when no level is known)."""
    lvl = i / 7
    return Pose(mouth="talk", mouth_open=lvl, arm_r=22 + lvl * 26, arm_l=14 + lvl * 4,
                head_tilt=lvl * 2.0 - 1.0, body_dy=-1.5 - lvl * 0.8, antenna=lvl * 6 - 3,
                glow=0.8 + 0.2 * lvl, look=(0.8, 0))


def _listen(t: float, i: int) -> Pose:
    s = math.sin(t * TAU)
    return Pose(eyes="big", mouth="o", arm_l=162, arm_r=12, head_tilt=-7,
                look=(-1.5, -0.5), antenna=-8 + s * 4, extras=[("waves", t)],
                glow=0.85 + 0.15 * s)


def _work(t: float, i: int) -> Pose:
    tap = (i % 2) * 2.0
    return Pose(eyes="focus", look=(0, 2.4), mouth="flat", arm_l=-22 + tap * 3,
                arm_r=-22 + (2 - tap) * 3, head_dy=1, antenna=math.sin(t * TAU) * 3,
                extras=[("laptop", t)], glow=0.9)


def _error(t: float, i: int) -> Pose:
    jit = (-1.5, 1.5, -1, 1, -0.5, 0.5)[i % 6]
    return Pose(eyes="x", mouth="zigzag", tint="red", body_dx=jit, tilt=jit,
                arm_l=40, arm_r=40, antenna=jit * 6, extras=[("exclaim", t), ("sparks", t)],
                glow=0.6 + 0.4 * (i % 2))


def _celebrate(t: float, i: int) -> Pose:
    s = math.sin(t * TAU * 2)
    h = abs(s)
    return Pose(eyes="happy", mouth="open", mouth_open=1.0, body_dy=-h * 8,
                squash=1 - (1 - h) * 0.05, arm_l=165 - h * 25, arm_r=140 + h * 25,
                leg_l=(-2, h * 3), leg_r=(2, h * 3), antenna=s * 14,
                shadow=1 - h * 0.4, extras=[("confetti", t)], glow=1.0)


def _wave(t: float, i: int) -> Pose:
    s = math.sin(t * TAU)
    return Pose(eyes="happy" if i in (2, 3, 4) else "normal", mouth="grin",
                arm_r=145 + s * 22, arm_l=12, head_tilt=-4 + s * 2,
                body_dy=-abs(s) * 1.2, antenna=s * 8, extras=[("hearts", t)])


def _dragged(t: float, i: int) -> Pose:
    s = math.sin(t * TAU)
    return Pose(eyes="big", mouth="o", arm_l=140 + s * 10, arm_r=140 - s * 10,
                leg_l=(-4 + s * 3, 6), leg_r=(4 - s * 3, 6), body_dy=-6,
                antenna=s * 14, shadow=0.5, tilt=s * 4)


ANIMATIONS: dict[State, Anim] = {
    State.IDLE: Anim(24, 10, True, _idle),
    State.WALKING: Anim(12, 12, True, _walk),
    State.RUNNING: Anim(6, 14, True, _run),
    State.SLEEPING: Anim(12, 4, True, _sleep),
    State.THINKING: Anim(12, 6, True, _think),
    State.HAPPY: Anim(8, 8, True, _happy),
    State.EXCITED: Anim(8, 10, True, _excited),
    State.CONFUSED: Anim(12, 6, True, _confused),
    State.SAD: Anim(10, 5, True, _sad),
    State.TALKING: Anim(8, 9, True, _talk),
    State.LISTENING: Anim(8, 6, True, _listen),
    State.WORKING: Anim(8, 8, True, _work),
    State.ERROR: Anim(6, 8, True, _error),
    State.CELEBRATING: Anim(12, 10, True, _celebrate),
    State.WAVING: Anim(8, 8, True, _wave),
    State.DRAGGED: Anim(6, 8, True, _dragged),
    State.BLINK: Anim(3, 14, False, _blink),
    State.GLANCE: Anim(10, 5, False, _glance),
}


def poses_for(state: State) -> list[Pose]:
    a = ANIMATIONS[state]
    out = []
    for i in range(a.frames):
        pose = a.build(i / a.frames, i)
        pose.hud = (i / a.frames) % 1.0          # holo-pad rotation loops with the clip
        out.append(pose)
    return out
