"""A small simulated world: a flat floor, vertical obstacles, moving people, and a
wearer whose head carries the glasses camera.

Coordinates: x to the east, y to the north, z up, meters. Headings are radians
clockwise from north (0 = +y, +pi/2 = +x), so a positive head yaw looks right.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

Schedule = Callable[[float], float]  # t (s) -> value


def const(value: float) -> Schedule:
    return lambda _t: value


def steps(*points: tuple[float, float]) -> Schedule:
    """Piecewise-constant schedule: steps((0, 1.0), (4, 0.0)) = 1.0 until t=4, then 0."""
    pts = sorted(points)

    def f(t: float) -> float:
        value = pts[0][1]
        for t0, v in pts:
            if t >= t0:
                value = v
        return value

    return f


def ramp(t0: float, t1: float, v0: float, v1: float) -> Schedule:
    """v0 before t0, v1 after t1, linear in between."""

    def f(t: float) -> float:
        if t <= t0:
            return v0
        if t >= t1:
            return v1
        return v0 + (v1 - v0) * (t - t0) / (t1 - t0)

    return f


@dataclass
class Obstacle:
    """Base: something with a footprint, a height, a color and an optional class label.

    `label` is what an ideal object detector would call it (COCO names, e.g.
    "person"). Unlabeled obstacles (walls, poles) are invisible to the detector
    and only show up through depth, which matches what YOLO does with them.
    """

    height: float
    color: tuple[int, int, int]  # BGR
    label: str | None = None
    velocity: tuple[float, float] = (0.0, 0.0)  # m/s, constant
    moving: Schedule = field(default=const(1.0))  # 0 = paused, 1 = moving
    name: str = ""
    confidence: float = 0.9  # score an ideal detector would give it (labeled obstacles only)

    def offset(self, t_moved: float) -> np.ndarray:
        return np.array(self.velocity) * t_moved


@dataclass
class Cylinder(Obstacle):
    center: tuple[float, float] = (0.0, 0.0)
    radius: float = 0.1


@dataclass
class Box(Obstacle):
    center: tuple[float, float] = (0.0, 0.0)
    size: tuple[float, float] = (1.0, 1.0)  # (along x, along y) before rotation
    yaw: float = 0.0  # radians, counter-clockwise rotation of the footprint

    def corners(self) -> np.ndarray:
        hx, hy = self.size[0] / 2, self.size[1] / 2
        c, s = math.cos(self.yaw), math.sin(self.yaw)
        local = np.array([[-hx, -hy], [hx, -hy], [hx, hy], [-hx, hy]])
        return local @ np.array([[c, s], [-s, c]]) + np.array(self.center)


def wall(x0: float, y0: float, x1: float, y1: float, height: float = 2.5, thickness: float = 0.15,
         color: tuple[int, int, int] = (170, 175, 185), name: str = "wall") -> Box:  # fmt: skip
    """A wall segment from (x0, y0) to (x1, y1)."""
    length = math.hypot(x1 - x0, y1 - y0)
    return Box(height, color, center=((x0 + x1) / 2, (y0 + y1) / 2), size=(length, thickness),
               yaw=math.atan2(y1 - y0, x1 - x0), name=name)  # fmt: skip


def pole(x: float, y: float, radius: float = 0.08, height: float = 2.2, name: str = "pole") -> Cylinder:
    return Cylinder(height, (60, 70, 200), center=(x, y), radius=radius, name=name)


def person(x: float, y: float, velocity: tuple[float, float] = (0.0, 0.0), moving: Schedule | None = None,
           name: str = "person", confidence: float = 0.9) -> Cylinder:  # fmt: skip
    return Cylinder(1.75, (90, 120, 40), "person", velocity, moving or const(1.0), name, confidence,
                    center=(x, y), radius=0.25)  # fmt: skip


@dataclass(frozen=True)
class Pose:
    x: float
    y: float
    heading: float  # torso (and belt) direction
    head_yaw: float  # head relative to torso; the camera looks along heading + head_yaw
    pitch: float  # positive = looking down
    eye_height: float

    @property
    def camera_yaw(self) -> float:
        return self.heading + self.head_yaw


@dataclass
class Walker:
    """The wearer. Speed, turning and head motion are schedules over time.
    `react` (optional) closes the loop: it gets the belt's motor levels, whether
    the belt is playing the fault pattern, and the time, and returns a speed
    multiplier, i.e. a simulated person responding to what they feel."""

    x: float = 0.0
    y: float = 0.0
    heading: float = 0.0
    speed: Schedule = field(default=const(0.0))  # m/s along the torso heading
    turn_rate: Schedule = field(default=const(0.0))  # rad/s
    head_yaw: Schedule = field(default=const(0.0))  # rad
    pitch: Schedule = field(default=const(0.0))  # rad, positive = looking down
    eye_height: float = 1.6
    body_radius: float = 0.25
    react: Callable[[tuple[int, ...], bool, float], float] | None = None

    def pose(self, t: float) -> Pose:
        return Pose(self.x, self.y, self.heading, self.head_yaw(t), self.pitch(t), self.eye_height)


@dataclass
class World:
    obstacles: list[Obstacle]
    walker: Walker
    ceiling: float | None = None  # ceiling height in meters (indoors), None = open sky
    floor_color: tuple[int, int, int] = (95, 100, 105)
    light: Schedule = field(default=const(1.0))  # brightness multiplier
    t: float = 0.0
    traveled: float = 0.0
    collisions: list[tuple[float, str]] = field(default_factory=list)
    _moved: dict[int, float] = field(default_factory=dict)
    _origins: dict[int, tuple[float, float]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for i, ob in enumerate(self.obstacles):
            self._origins[i] = ob.center  # type: ignore[attr-defined]
            self._moved[i] = 0.0

    def step(self, t: float, belt_levels: tuple[int, ...] | None = None, belt_fault: bool = False) -> None:
        """Advance everything to time t (seconds since the scenario started)."""
        dt = max(0.0, t - self.t)
        self.t = t
        if dt == 0.0:
            return
        w = self.walker
        speed = w.speed(t)
        if w.react is not None and belt_levels is not None:
            speed *= w.react(belt_levels, belt_fault, t)
        w.heading += w.turn_rate(t) * dt
        nx, ny = w.x + math.sin(w.heading) * speed * dt, w.y + math.cos(w.heading) * speed * dt
        for i, ob in enumerate(self.obstacles):
            self._moved[i] += dt * ob.moving(t)
            ox, oy = self._origins[i]
            off = ob.offset(self._moved[i])
            ob.center = (ox + off[0], oy + off[1])  # type: ignore[attr-defined]
        hit = self.collision_at(nx, ny, w.body_radius)
        if hit is not None:
            if not self.collisions or self.collisions[-1][1] != hit:
                self.collisions.append((t, hit))
            return  # the wearer bumped into something and stays put
        self.traveled += math.hypot(nx - w.x, ny - w.y)
        w.x, w.y = nx, ny

    def pose(self) -> Pose:
        return self.walker.pose(self.t)

    def collision_at(self, x: float, y: float, r: float) -> str | None:
        for ob in self.obstacles:
            if footprint_distance(ob, x, y) < r:
                return ob.name or type(ob).__name__
        return None

    def clearance(self) -> float:
        """Distance from the wearer's body edge to the nearest obstacle footprint."""
        w = self.walker
        if not self.obstacles:
            return math.inf
        return min(footprint_distance(ob, w.x, w.y) for ob in self.obstacles) - w.body_radius


def footprint_distance(ob: Obstacle, x: float, y: float) -> float:
    if isinstance(ob, Cylinder):
        return math.hypot(x - ob.center[0], y - ob.center[1]) - ob.radius
    if isinstance(ob, Box):
        c, s = math.cos(-ob.yaw), math.sin(-ob.yaw)
        dx, dy = x - ob.center[0], y - ob.center[1]
        lx, ly = abs(dx * c - dy * s), abs(dx * s + dy * c)
        qx, qy = lx - ob.size[0] / 2, ly - ob.size[1] / 2
        return math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0)
    raise TypeError(type(ob))
