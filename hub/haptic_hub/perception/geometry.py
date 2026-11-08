"""Pure-numpy helpers that map images, depth maps and boxes onto belt sectors.

Sector 0 is the left edge of the image (the wearer's left), sector n-1 the right.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


def sector_edges(width: int, n: int) -> list[tuple[int, int]]:
    """Pixel column ranges [x0, x1) for n equal vertical strips."""
    edges = np.linspace(0, width, n + 1).round().astype(int)
    return [(int(edges[i]), int(edges[i + 1])) for i in range(n)]


def depth_sector_nearest(
    depth_m: np.ndarray,
    n: int,
    band_top: float,
    band_bottom: float,
    percentile: float,
) -> list[float | None]:
    """Approximate nearest distance per sector, from a metric depth map.

    Only rows in [band_top, band_bottom) of the image height are used. A low
    percentile instead of the minimum, so a few bad pixels don't trigger an alert.
    Returns None for a sector with no valid depth values.
    """
    if depth_m.ndim != 2:
        raise ValueError(f"depth map must be 2-D, got shape {depth_m.shape}")
    h, w = depth_m.shape
    y0, y1 = int(h * band_top), max(int(h * band_bottom), int(h * band_top) + 1)
    band = depth_m[y0:y1]
    out: list[float | None] = []
    for x0, x1 in sector_edges(w, n):
        values = band[:, x0:x1]
        values = values[np.isfinite(values) & (values > 0)]
        out.append(float(np.percentile(values, percentile)) if values.size else None)
    return out


def linear_risk(value: float | None, full_at: float, zero_at: float) -> float:
    """1.0 when value <= full_at, 0.0 when value >= zero_at, linear in between."""
    if value is None or not math.isfinite(value):
        return 0.0
    if value <= full_at:
        return 1.0
    if value >= zero_at:
        return 0.0
    return (zero_at - value) / (zero_at - full_at)


def box_sectors(x1: float, x2: float, width: int, n: int) -> list[int]:
    """Indices of the sectors a box's horizontal extent overlaps."""
    lo, hi = max(0.0, min(x1, x2)), min(float(width), max(x1, x2))
    if hi <= lo:
        return []
    return [i for i, (s0, s1) in enumerate(sector_edges(width, n)) if lo < s1 and hi > s0]


# ------------------------------------------------------------------ floor-relative depth
#
# Camera frame: x right, y down, z forward (the depth map's z-depth). A floor plane is
# stored as a unit normal n pointing from the floor toward the camera ("up") and the
# camera's height h above it, so a point P is n.P + h meters above the floor.


@dataclass(frozen=True)
class FloorPlane:
    normal: tuple[float, float, float]  # unit, camera frame, pointing up (toward the camera)
    height_m: float  # camera height above the floor, in the depth map's units
    source: str  # "fit" (this frame) | "held" (an earlier fit) | "prior" (level camera, assumed height)
    inliers: int = 0
    t: float = 0.0  # when it was fitted

    @property
    def pitch_deg(self) -> float:
        """Camera pitch implied by the plane; positive = looking down."""
        _, ny, nz = self.normal
        return math.degrees(math.atan2(-nz, -ny))

    def tilt_deg(self) -> float:
        """Angle between this floor's normal and a level camera's up (0, -1, 0)."""
        return math.degrees(math.acos(max(-1.0, min(1.0, -self.normal[1]))))


def prior_floor(eye_height_m: float) -> FloorPlane:
    """A level camera at the assumed eye height."""
    return FloorPlane((0.0, -1.0, 0.0), eye_height_m, "prior")


def back_project(depth_m: np.ndarray, focal_px: float, stride: int = 1) -> tuple[np.ndarray, np.ndarray]:
    """Camera-frame 3-D points (H', W', 3) for every `stride`-th pixel of a z-depth map,
    plus a validity mask. The principal point is assumed at the image center."""
    h, w = depth_m.shape
    v = np.arange(stride // 2, h, stride, dtype=np.float32)
    u = np.arange(stride // 2, w, stride, dtype=np.float32)
    z = depth_m[stride // 2 :: stride, stride // 2 :: stride].astype(np.float32)
    xn = (u + 0.5 - w / 2) / focal_px
    yn = (v + 0.5 - h / 2) / focal_px
    pts = np.empty((*z.shape, 3), np.float32)
    pts[..., 0] = xn[None, :] * z
    pts[..., 1] = yn[:, None] * z
    pts[..., 2] = z
    return pts, np.isfinite(z) & (z > 0)


def fit_floor(
    pts: np.ndarray,
    rng: np.random.Generator,
    hypotheses: list[FloorPlane],
    *,
    max_tilt_deg: float,
    height_range: tuple[float, float],
    tol_m: float,
    tol_rel: float,
    min_inlier_frac: float,
    iterations: int = 64,
) -> FloorPlane | None:
    """RANSAC plane fit for the floor, constrained to plausible floors: the normal
    within max_tilt_deg of a level camera's up and the camera height within
    height_range. Walls (horizontal normal) and ceilings (normal pointing down) are
    rejected by the tilt limit. `hypotheses` (e.g. the last fit and the prior) are
    scored alongside random ones. A point is a floor inlier if it is within
    tol_m + tol_rel * range of the plane (monocular depth error grows with range).

    pts: (N, 3) camera-frame points. Returns None if no plausible floor has at
    least min_inlier_frac of the points.
    """
    n_pts = len(pts)
    if n_pts < 3:
        return None
    cos_max = math.cos(math.radians(max_tilt_deg))
    tol = tol_m + tol_rel * np.linalg.norm(pts, axis=1)

    # Random hypotheses from point triples, plus the given ones.
    idx = rng.integers(0, n_pts, size=(iterations, 3))
    a, b, c = pts[idx[:, 0]], pts[idx[:, 1]], pts[idx[:, 2]]
    normals = np.cross(b - a, c - a)
    norms = np.linalg.norm(normals, axis=1)
    ok = norms > 1e-9
    normals = normals[ok] / norms[ok, None]
    d = -np.einsum("ij,ij->i", normals, a[ok])  # plane: n.P + d = 0
    flip = d < 0  # orient so the camera (origin) is on the +n side: height = d > 0
    normals[flip] *= -1
    d[flip] *= -1
    if hypotheses:
        normals = np.vstack([normals, np.array([hp.normal for hp in hypotheses], np.float32)])
        d = np.concatenate([d, np.array([hp.height_m for hp in hypotheses], np.float32)])
    plausible = (-normals[:, 1] >= cos_max) & (d >= height_range[0]) & (d <= height_range[1])
    if not plausible.any():
        return None
    normals, d = normals[plausible], d[plausible]
    resid = np.abs(pts @ normals.T + d[None, :])  # (N, K)
    counts = (resid <= tol[:, None]).sum(axis=0)
    best = int(np.argmax(counts))
    if counts[best] < max(3, min_inlier_frac * n_pts):
        return None

    # Refine on the inliers (least squares plane through them), then re-check plausibility.
    inl = pts[resid[:, best] <= tol]
    centroid = inl.mean(axis=0)
    _, _, vt = np.linalg.svd(inl - centroid, full_matrices=False)
    n = vt[2]
    h = -float(n @ centroid)
    if h < 0:
        n, h = -n, -h
    if -n[1] < cos_max or not height_range[0] <= h <= height_range[1]:
        n, h = normals[best], float(d[best])
    count = int((np.abs(pts @ n + h) <= tol).sum())
    return FloorPlane((float(n[0]), float(n[1]), float(n[2])), h, "fit", count)


def obstacle_sector_nearest(
    pts: np.ndarray,
    valid: np.ndarray,
    floor: FloorPlane,
    n: int,
    min_height_m: float,
    max_above_camera_m: float,
    percentile: float,
    max_range_m: float = 20.0,
) -> list[float | None]:
    """Nearest obstacle distance per sector, using only points that stand above the floor.

    A point counts as an obstacle if it is at least min_height_m above the floor and
    no more than max_above_camera_m above the camera (a ceiling, or anything above
    head height, can't be walked into). Its distance is measured along the camera's
    forward direction projected onto the floor (equal to z-depth for a level camera).

    Per sector (a vertical strip of image columns), the `percentile` of that distance
    over *all* the sector's pixels, with non-obstacle pixels counted as infinitely
    far: an obstacle must cover at least `percentile`% of the strip, so a few stray
    pixels don't alert. None = no obstacle in the strip.
    """
    nz = floor.normal[2]
    up = np.array(floor.normal, np.float32)
    fwd = np.array([0.0, 0.0, 1.0], np.float32) - nz * up
    fwd /= max(1e-9, float(np.linalg.norm(fwd)))
    height = pts @ up + floor.height_m
    dist = pts @ fwd
    is_obstacle = valid & (pts[..., 2] < max_range_m) & (height >= min_height_m) & (height <= floor.height_m + max_above_camera_m) & (dist > 0)
    dist = np.where(is_obstacle, dist, np.inf)
    out: list[float | None] = []
    for x0, x1 in sector_edges(dist.shape[1], n):
        values = dist[:, x0:x1]
        if values.size == 0:
            out.append(None)
            continue
        q = float(np.percentile(values, percentile, method="lower"))
        out.append(q if math.isfinite(q) else None)
    return out
