"""Renders what the glasses camera would see in a World, with exact ground truth.

A per-pixel ray caster in numpy: every pixel's ray is intersected with the
floor, the optional ceiling and every obstacle (vertical cylinders and boxes).
Besides the BGR image it returns the z-depth in meters (distance along the
optical axis, the quantity a metric depth model estimates) and an object-id map,
from which ideal detector boxes are derived.

The images are deliberately simple (flat shading plus some texture and noise).
They exercise the pipeline; they are not photorealistic, so results from real
models on these renders say little about real-world accuracy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

import cv2
import numpy as np

from haptic_hub.sim.world import Box, Cylinder, Pose, World

SKY_DEPTH_M = 80.0  # what we report for open sky; real metric models also saturate


@dataclass(frozen=True)
class CameraModel:
    """Glasses camera. Defaults match what meta/ requests (504x896 portrait, 15 fps).

    The field of view is an ASSUMPTION (~100 deg diagonal, i.e. ~60 deg horizontal
    for portrait 9:16). It has not been measured on the actual glasses.
    """

    width: int = 504
    height: int = 896
    hfov_deg: float = 60.0

    @property
    def focal_px(self) -> float:
        return (self.width / 2) / math.tan(math.radians(self.hfov_deg) / 2)

    @property
    def vfov_deg(self) -> float:
        return math.degrees(2 * math.atan((self.height / 2) / self.focal_px))

    def column_azimuth(self, u: np.ndarray | float) -> np.ndarray | float:
        """Horizontal angle (rad, positive = right of the optical axis) of pixel column u."""
        return np.arctan((np.asarray(u) + 0.5 - self.width / 2) / self.focal_px)


@dataclass(frozen=True)
class GtBox:
    """Ideal detector output for one labeled object, in full-resolution pixels."""

    track_id: int
    label: str
    x1: float
    y1: float
    x2: float
    y2: float
    distance_m: float
    confidence: float


@dataclass
class Render:
    image: np.ndarray  # BGR uint8, HxWx3
    depth: np.ndarray  # float32 meters (z-depth), HxW
    ids: np.ndarray  # int16, -1 floor/ceiling/sky, else obstacle index
    boxes: list[GtBox]


class Renderer:
    """`scale` < 1 ray-casts at reduced resolution and upsamples to the camera size
    (the hub shrinks frames to 384 px wide before inference anyway)."""

    def __init__(self, camera: CameraModel | None = None, seed: int = 0, scale: float = 0.5) -> None:
        self.camera = camera or CameraModel()
        cam = self.camera
        self._W, self._H = max(8, round(cam.width * scale)), max(8, round(cam.height * scale))
        f = cam.focal_px * self._W / cam.width
        u = (np.arange(self._W, dtype=np.float32) + 0.5 - self._W / 2) / f
        v = (np.arange(self._H, dtype=np.float32) + 0.5 - self._H / 2) / f
        self._xc, self._yc = np.meshgrid(u, v)  # camera-frame ray (x right, y down, z=1 forward)
        self._rng = np.random.default_rng(seed)
        self._cull = math.radians(cam.hfov_deg / 2 + 25)  # generous: pitch bends column azimuths

    def _visible(self, ob, pose: Pose) -> bool:
        if isinstance(ob, Cylinder):
            cx, cy, r = ob.center[0], ob.center[1], ob.radius
        else:
            corners = ob.corners()
            cx, cy = corners.mean(axis=0)
            r = float(np.max(np.hypot(*(corners - (cx, cy)).T)))
        dx, dy = cx - pose.x, cy - pose.y
        dist = math.hypot(dx, dy)
        if dist <= r:
            return True
        off = (math.atan2(dx, dy) - pose.camera_yaw + math.pi) % (2 * math.pi) - math.pi
        return abs(off) - math.asin(min(1.0, r / dist)) < self._cull

    def render(self, world: World, pose: Pose | None = None) -> Render:
        out = self._render(world, pose or world.pose())
        cam = self.camera
        if (self._W, self._H) == (cam.width, cam.height):
            return out
        k = cam.width / self._W
        size = (cam.width, cam.height)
        return Render(
            cv2.resize(out.image, size, interpolation=cv2.INTER_LINEAR),
            cv2.resize(out.depth, size, interpolation=cv2.INTER_NEAREST),
            cv2.resize(out.ids, size, interpolation=cv2.INTER_NEAREST),
            [replace(b, x1=b.x1 * k, y1=b.y1 * k, x2=b.x2 * k, y2=b.y2 * k) for b in out.boxes],
        )

    def _render(self, world: World, pose: Pose) -> Render:
        H, W = self._H, self._W
        yaw, pitch = pose.camera_yaw, pose.pitch
        sy, cy_, sp, cp = math.sin(yaw), math.cos(yaw), math.sin(pitch), math.cos(pitch)

        # World-frame ray for every pixel: d = xc*Right + yc*Down + Forward. With the
        # camera-frame z fixed at 1, the ray parameter s at a hit *is* the z-depth.
        right = (cy_, -sy, 0.0)
        down = (-sy * sp, -cy_ * sp, -cp)
        fwd = (sy * cp, cy_ * cp, -sp)
        xc, yc = self._xc, self._yc
        dx = xc * right[0] + yc * down[0] + fwd[0]
        dy = xc * right[1] + yc * down[1] + fwd[1]
        dz = yc * down[2] + fwd[2]
        px, py, pz = pose.x, pose.y, pose.eye_height

        depth = np.full((H, W), np.inf, np.float32)
        ids = np.full((H, W), -1, np.int16)
        tex = np.zeros((H, W), np.float32)  # per-surface texture coordinate
        shade = np.ones((H, W), np.float32)

        # Floor (z = 0) and ceiling.
        with np.errstate(divide="ignore", invalid="ignore"):
            s_floor = np.where(dz < 0, -pz / dz, np.inf)
            s_ceil = np.where(dz > 0, (world.ceiling - pz) / dz, np.inf) if world.ceiling else np.full_like(dz, np.inf)
        depth = np.minimum(s_floor, s_ceil).astype(np.float32)
        is_floor = s_floor <= s_ceil
        surface = np.where(is_floor & np.isfinite(s_floor), 0, np.where(np.isfinite(s_ceil), 1, 2))  # 0 floor 1 ceil 2 sky

        for k, ob in enumerate(world.obstacles):
            if not self._visible(ob, pose):
                continue
            s, t_coord, cos_inc = self._hit(ob, px, py, dx, dy)
            if s is None:
                continue
            z = pz + s * dz
            ok = np.isfinite(s) & (s > 0.01) & (z >= 0) & (z <= ob.height) & (s < depth)
            depth = np.where(ok, s, depth)
            ids = np.where(ok, k, ids)
            tex = np.where(ok, t_coord + 3.0 * z, tex)
            shade = np.where(ok, 0.45 + 0.55 * cos_inc, shade)

        # Shading.
        img = np.empty((H, W, 3), np.float32)
        with np.errstate(invalid="ignore"):
            hx, hy = px + s_floor * dx, py + s_floor * dy
            checker = ((np.floor(hx * 2) + np.floor(hy * 2)) % 2).astype(np.float32)
        floor_rgb = np.array(world.floor_color, np.float32) * (0.85 + 0.25 * checker[..., None])
        ceil_rgb = np.array((205, 205, 200), np.float32)
        sky_rgb = np.array((235, 200, 150), np.float32) * (0.8 + 0.2 * (-yc.clip(-1, 0)))[..., None]
        img[:] = np.where((surface == 0)[..., None], floor_rgb, np.where((surface == 1)[..., None], ceil_rgb, sky_rgb))
        colors = np.array([ob.color for ob in world.obstacles] or [(0, 0, 0)], np.float32)
        obj = ids >= 0
        if obj.any():
            stripes = 0.85 + 0.15 * (np.sin(tex * 6.0) > 0)
            obj_rgb = colors[ids.clip(0)] * (shade * stripes)[..., None]
            img = np.where(obj[..., None], obj_rgb, img)
        fog = np.exp(-np.minimum(depth, SKY_DEPTH_M) / 40.0)[..., None]  # distant things fade a little
        img = img * fog + 180.0 * (1 - fog)
        img *= world.light(world.t)
        img += self._rng.normal(0, 3.0, img.shape[:2])[..., None]
        image = img.clip(0, 255).astype(np.uint8)

        depth = np.where(np.isfinite(depth), depth, SKY_DEPTH_M).astype(np.float32)
        return Render(image, depth, ids, self._boxes(world, ids, depth))

    @staticmethod
    def _hit(ob, px: float, py: float, dx: np.ndarray, dy: np.ndarray):
        """Ray parameter s of the first hit with an obstacle's vertical surface,
        a texture coordinate along the surface, and |cos| of the incidence angle."""
        if isinstance(ob, Cylinder):
            ox, oy = px - ob.center[0], py - ob.center[1]
            a = dx * dx + dy * dy
            b = 2 * (dx * ox + dy * oy)
            c = ox * ox + oy * oy - ob.radius * ob.radius
            disc = b * b - 4 * a * c
            with np.errstate(invalid="ignore"):
                s = np.where(disc >= 0, (-b - np.sqrt(np.maximum(disc, 0))) / (2 * a), np.inf)
            hx, hy = ox + s * dx, oy + s * dy
            norm = np.sqrt(a) * ob.radius
            with np.errstate(invalid="ignore", divide="ignore"):
                cos_inc = np.abs((hx * dx + hy * dy) / norm)
            return s, np.arctan2(hy, hx) * ob.radius * 4, np.nan_to_num(cos_inc)
        if isinstance(ob, Box):
            corners = ob.corners()
            best = np.full(dx.shape, np.inf, np.float32)
            t_best = np.zeros(dx.shape, np.float32)
            cos_best = np.ones(dx.shape, np.float32)
            dlen = np.sqrt(dx * dx + dy * dy)
            for i in range(4):
                (ax, ay), (bx, by) = corners[i], corners[(i + 1) % 4]
                ex, ey = bx - ax, by - ay
                elen = math.hypot(ex, ey)
                denom = dx * ey - dy * ex
                wx, wy = ax - px, ay - py
                with np.errstate(divide="ignore", invalid="ignore"):
                    s = (wx * ey - wy * ex) / denom
                    u = (wx * dy - wy * dx) / denom
                ok = (np.abs(denom) > 1e-9) & (s > 0) & (u >= 0) & (u <= 1) & (s < best)
                best = np.where(ok, s, best)
                t_best = np.where(ok, u * elen, t_best)
                with np.errstate(invalid="ignore", divide="ignore"):
                    cos_best = np.where(ok, np.abs(denom) / (dlen * elen), cos_best)
            return best, t_best, cos_best
        return None, None, None

    @staticmethod
    def _boxes(world: World, ids: np.ndarray, depth: np.ndarray) -> list[GtBox]:
        boxes = []
        for k, ob in enumerate(world.obstacles):
            if ob.label is None:
                continue
            mask = ids == k
            if mask.sum() < 5:  # (almost) fully occluded or out of view
                continue
            rows, cols = np.nonzero(mask.any(axis=1))[0], np.nonzero(mask.any(axis=0))[0]
            boxes.append(GtBox(k, ob.label, float(cols[0]), float(rows[0]), float(cols[-1] + 1), float(rows[-1] + 1),
                               float(np.median(depth[mask])), ob.confidence))  # fmt: skip
        return boxes


def sector_ground_truth(world: World, camera: CameraModel, sectors: int, rays_per_sector: int = 12,
                        min_height: float = 0.3, max_range: float = 10.0) -> list[float | None]:  # fmt: skip
    """Nearest obstacle per belt sector, measured independently of perception.

    Casts horizontal rays across each sector's slice of the camera's field of
    view and returns the horizontal distance to the nearest obstacle at least
    `min_height` tall (None = nothing within max_range). This is the "truth" the
    scenarios compare the belt against.
    """
    pose = world.pose()
    edges = np.linspace(0, camera.width, sectors + 1)
    out: list[float | None] = []
    for i in range(sectors):
        cols = np.linspace(edges[i], edges[i + 1] - 1, rays_per_sector)
        az = pose.camera_yaw + np.asarray(camera.column_azimuth(cols))
        dx, dy = np.sin(az)[None, :], np.cos(az)[None, :]
        nearest = math.inf
        for ob in world.obstacles:
            if ob.height < min_height:
                continue
            s, _, _ = Renderer._hit(ob, pose.x, pose.y, dx, dy)
            if s is not None:
                s = np.where(s > 0, s, np.inf)
                nearest = min(nearest, float(np.min(s)))
        out.append(nearest if nearest <= max_range else None)
    return out
