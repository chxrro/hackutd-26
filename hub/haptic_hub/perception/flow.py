"""Optical-flow motion for tracked boxes: approach (image expansion) and crossing.

Adapted from Nathan's NVIDIA webcam experiment (scripts/visual_motion.py in PR #3).
For each tracked box, sparse Lucas-Kanade flow between the current frame and one
~0.15 s older is fitted with a RANSAC similarity transform:

- **approach**: the fitted scale s over dt gives TTC ~= dt / ln(s). This is
  camera-relative, so walking toward a still pole counts. It is independent of
  the detector's box-height jitter that ttc.py relies on.
- **crossing**: horizontal motion of the box after subtracting the background's
  motion at the same point (so a head turn doesn't make everything "cross").
  Projected ahead `lookahead_s`, the swept interval marks the sectors at risk.

Everything here is an unvalidated estimate. A box without enough texture, a
missing reference frame, or an unreliable background fit yields no
contribution; it never lowers risk from the other estimators.
"""

from __future__ import annotations

import math
import statistics
import time
from collections import deque
from dataclasses import dataclass, replace
from typing import Any, Sequence

import cv2
import numpy as np

from haptic_hub.config import FlowConfig
from haptic_hub.perception.base import Perception
from haptic_hub.perception.geometry import box_sectors, linear_risk
from haptic_hub.types import Frame, PerceptionResult, SectorRisk

Box = tuple[float, float, float, float]  # x1, y1, x2, y2 in pixels of the gray image


@dataclass(frozen=True)
class Fit:
    """Similarity transform fitted to one region's flow (current = A @ [x, y, 1])."""

    affine: np.ndarray  # 2x3
    inliers: int
    inlier_fraction: float

    @property
    def scale(self) -> float:
        return float(math.hypot(self.affine[0, 0], self.affine[1, 0]))

    def move(self, x: float, y: float) -> tuple[float, float]:
        """Where point (x, y) of the older frame went in the newer one."""
        a = self.affine
        return float(a[0, 0] * x + a[0, 1] * y + a[0, 2]), float(a[1, 0] * x + a[1, 1] * y + a[1, 2])


def expand(box: Box, shape: tuple[int, int], margin: float) -> tuple[int, int, int, int]:
    """Integer pixel window around a box, grown by `margin` of its size, clipped to the image."""
    h, w = shape
    x1, y1, x2, y2 = box
    dx, dy = (x2 - x1) * margin, (y2 - y1) * margin
    return (max(0, int(x1 - dx)), max(0, int(y1 - dy)), min(w, int(x2 + dx) + 1), min(h, int(y2 + dy) + 1))


def _corners(gray: np.ndarray, cfg: FlowConfig, window=None, mask=None) -> np.ndarray:
    """Up to cfg.max_corners trackable points, as an Nx2 float32 array in full-image pixels."""
    x0, y0 = 0, 0
    image = gray
    if window is not None:
        x0, y0, x1, y1 = window
        if x1 - x0 < 8 or y1 - y0 < 8:
            return np.empty((0, 2), np.float32)
        image = gray[y0:y1, x0:x1]
    pts = cv2.goodFeaturesToTrack(
        image, maxCorners=cfg.max_corners, qualityLevel=cfg.corner_quality, minDistance=3, mask=mask
    )
    if pts is None:
        return np.empty((0, 2), np.float32)
    return pts.reshape(-1, 2) + np.array([x0, y0], np.float32)


def _fit(src: np.ndarray, dst: np.ndarray, cfg: FlowConfig, min_inliers: int) -> Fit | None:
    if len(src) < max(6, min_inliers):
        return None
    affine, inliers = cv2.estimateAffinePartial2D(
        src, dst, method=cv2.RANSAC, ransacReprojThreshold=cfg.ransac_px, maxIters=1000, confidence=0.99
    )
    if affine is None or inliers is None or not np.isfinite(affine).all():
        return None
    keep = inliers.ravel().astype(bool)
    count = int(keep.sum())
    fraction = count / len(src)
    if count < min_inliers or fraction < cfg.min_inlier_fraction:
        return None
    spread = src[keep]
    # A tiny cluster of points can't establish expansion.
    if np.ptp(spread[:, 0]) < cfg.min_spread_px or np.ptp(spread[:, 1]) < cfg.min_spread_px:
        return None
    return Fit(affine, count, fraction)


def measure(
    older: np.ndarray, newer: np.ndarray, boxes: Sequence[Box], cfg: FlowConfig
) -> tuple[Fit | None, list[Fit | None]]:
    """Background fit plus one fit per box (boxes are in the older frame).

    One forward/backward LK pass for all points keeps the cost close to a single
    region's, however many boxes there are.
    """
    if older.shape != newer.shape:
        raise ValueError(f"frame size changed: {older.shape} -> {newer.shape}")
    shape = older.shape[:2]
    windows = [expand(b, shape, cfg.box_margin) for b in boxes]

    background = np.full(shape, 255, np.uint8)
    for x0, y0, x1, y1 in windows:
        background[y0:y1, x0:x1] = 0
    groups = [_corners(older, cfg, mask=background)] + [_corners(older, cfg, window=w) for w in windows]
    sizes = [len(g) for g in groups]
    if sum(sizes) == 0:
        return None, [None] * len(boxes)

    pts = np.concatenate(groups).reshape(-1, 1, 2).astype(np.float32)
    lk = {
        "winSize": (cfg.lk_window, cfg.lk_window),
        "maxLevel": cfg.lk_levels,
        "criteria": (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01),
    }
    fwd, ok_f, _ = cv2.calcOpticalFlowPyrLK(older, newer, pts, None, **lk)
    back, ok_b, _ = cv2.calcOpticalFlowPyrLK(newer, older, fwd, None, **lk)
    good = (ok_f.ravel() == 1) & (ok_b.ravel() == 1) & (np.linalg.norm(pts - back, axis=2).ravel() < cfg.max_fb_error_px)
    src, dst = pts.reshape(-1, 2), fwd.reshape(-1, 2)

    fits: list[Fit | None] = []
    start = 0
    for i, n in enumerate(sizes):
        sel = slice(start, start + n)
        start += n
        g = good[sel]
        fits.append(_fit(src[sel][g], dst[sel][g], cfg, cfg.min_background_inliers if i == 0 else cfg.min_inliers))
    return fits[0], fits[1:]


@dataclass(frozen=True)
class TrackMotion:
    track_id: int
    label: str
    box: Box  # current box, gray-image pixels
    status: str  # "measured" | "no_reference" | "insufficient_features"
    ttc_s: float | None = None  # from smoothed expansion rate; None = not approaching
    vx: float | None = None  # smoothed horizontal speed relative to background, image widths per second
    crossing_sectors: tuple[int, ...] = ()
    approach_risk: float = 0.0
    crossing_risk: float = 0.0


class FlowMotion:
    """Keeps a short frame history and turns flow into per-track motion and sector risk."""

    def __init__(self, cfg: FlowConfig, sectors: int, ttc_near_s: float, ttc_far_s: float) -> None:
        self.cfg = cfg
        self.sectors = sectors
        self.ttc_near_s = ttc_near_s
        self.ttc_far_s = ttc_far_s
        self._frames: deque[tuple[float, np.ndarray, dict[int, Box]]] = deque()
        # track_id -> recent (t, expansion rate per s, vx or None)
        self._samples: dict[int, deque[tuple[float, float, float | None]]] = {}

    def _reference(self, now: float):
        """The stored frame closest to target_dt_s old, within [min_dt_s, max_dt_s]."""
        cfg = self.cfg
        usable = [f for f in self._frames if cfg.min_dt_s <= now - f[0] <= cfg.max_dt_s]
        return min(usable, key=lambda f: abs(now - f[0] - cfg.target_dt_s), default=None)

    def update(self, now: float, gray: np.ndarray, boxes: Sequence[Any]) -> tuple[list[TrackMotion], dict[str, Any]]:
        """boxes: objects with track_id, label, x1, y1, x2, y2 in `gray`'s pixels."""
        cfg = self.cfg
        if self._frames and self._frames[-1][1].shape != gray.shape:
            self._frames.clear()
            self._samples.clear()
        while self._frames and now - self._frames[0][0] > cfg.max_dt_s:
            self._frames.popleft()

        current = {b.track_id: b for b in boxes if b.track_id is not None}
        ref = self._reference(now)
        info: dict[str, Any] = {"dt": None, "background": None}
        motions: list[TrackMotion] = []

        if ref is None:
            motions = [TrackMotion(b.track_id, b.label, _xyxy(b), "no_reference") for b in current.values()]
        else:
            t_ref, old_gray, old_boxes = ref
            dt = now - t_ref
            info["dt"] = round(dt, 3)
            shared = [tid for tid in current if tid in old_boxes]
            background, fits = measure(old_gray, gray, [old_boxes[tid] for tid in shared], cfg)
            if background is not None:
                info["background"] = {"scale": round(background.scale, 4), "inliers": background.inliers}
            fit_by_id = dict(zip(shared, fits))
            h, w = gray.shape[:2]
            for tid, b in current.items():
                box = _xyxy(b)
                fit = fit_by_id.get(tid)
                if tid not in old_boxes:
                    motions.append(TrackMotion(tid, b.label, box, "no_reference"))
                    continue
                if fit is None or not cfg.min_scale <= fit.scale <= cfg.max_scale:
                    motions.append(TrackMotion(tid, b.label, box, "insufficient_features"))
                    continue
                rate = math.log(fit.scale) / dt
                vx = None
                if background is not None:
                    ox1, oy1, ox2, oy2 = old_boxes[tid]
                    cx, cy = (ox1 + ox2) / 2, (oy1 + oy2) / 2
                    obj_x = fit.move(cx, cy)[0]
                    bg_x = background.move(cx, cy)[0]
                    vx = (obj_x - bg_x) / dt / w
                motions.append(self._assess(tid, b.label, box, now, rate, vx, w, h))

        # Remember this frame, and forget tracks that are gone.
        self._frames.append((now, gray, {tid: _xyxy(b) for tid, b in current.items()}))
        for tid in [tid for tid, s in self._samples.items() if not s or now - s[-1][0] > cfg.max_dt_s * 2]:
            del self._samples[tid]
        return motions, info

    def _assess(self, tid: int, label: str, box: Box, now: float, rate: float, vx: float | None, w: int, h: int) -> TrackMotion:
        cfg = self.cfg
        samples = self._samples.setdefault(tid, deque())
        samples.append((now, rate, vx))
        while samples and now - samples[0][0] > cfg.smooth_window_s:
            samples.popleft()
        if len(samples) < cfg.min_samples:
            return TrackMotion(tid, label, box, "measured")

        med_rate = statistics.median(s[1] for s in samples)
        ttc = 1.0 / med_rate if med_rate > 0 else None
        approach = linear_risk(ttc, self.ttc_near_s, self.ttc_far_s)

        vxs = [s[2] for s in samples if s[2] is not None]
        med_vx = statistics.median(vxs) if len(vxs) >= cfg.min_samples else None
        crossing, swept = 0.0, ()
        x1, y1, x2, y2 = box
        if med_vx is not None and abs(med_vx) >= cfg.min_cross_speed and (y2 - y1) / h >= cfg.crossing_min_box_height:
            shift = med_vx * cfg.lookahead_s * w
            lo, hi = min(x1, x1 + shift), max(x2, x2 + shift)
            c0, c1 = cfg.corridor
            if hi >= c0 * w and lo <= c1 * w:  # the swept path crosses the wearer's heading
                swept = tuple(box_sectors(lo, hi, w, self.sectors))
                crossing = cfg.crossing_risk
        return TrackMotion(tid, label, box, "measured", ttc, med_vx, swept, approach, crossing)

    def sector_evidence(self, motions: Sequence[TrackMotion], width: int) -> list[SectorRisk]:
        """Keep the winning motion and track attached to the risk it caused."""
        out = [SectorRisk(0.0) for _ in range(self.sectors)]
        for m in motions:
            parts = []
            if m.approach_risk > 0:
                parts += [(s, m.approach_risk, m.ttc_s, "flow_approach", "approaching")
                          for s in box_sectors(m.box[0], m.box[2], width, self.sectors)]
            if m.crossing_risk > 0:
                parts += [(s, m.crossing_risk, None, "flow_crossing", "crossing")
                          for s in m.crossing_sectors]
            for s, risk, ttc, evidence, motion in parts:
                if risk > out[s].risk:
                    out[s] = SectorRisk(risk, ttc_s=ttc, label=m.label,
                                        evidence=evidence, motion=motion, track_id=m.track_id)
        return out

    def sector_contributions(self, motions: Sequence[TrackMotion], width: int) -> list[tuple[float, float | None, str | None]]:
        return [(s.risk, s.ttc_s, s.label) for s in self.sector_evidence(motions, width)]


def _xyxy(b: Any) -> Box:
    return (float(b.x1), float(b.y1), float(b.x2), float(b.y2))


class FlowMotionPerception:
    """Wraps a detector-based perception and raises sector risk from optical flow.

    Only ever raises risk (max-merge). Needs the inner perception to publish
    `debug["boxes"]` (tracked boxes) and `debug["infer_size"]`, as VisionPerception does.
    """

    def __init__(self, inner: Perception, cfg: FlowConfig, sectors: int, ttc_near_s: float, ttc_far_s: float) -> None:
        self.inner = inner
        self.name = f"{inner.name}+flow"
        self.motion = FlowMotion(cfg, sectors, ttc_near_s, ttc_far_s)

    def process(self, frame: Frame) -> PerceptionResult:
        result = self.inner.process(frame)
        started = time.perf_counter()
        if "boxes" not in result.debug or "infer_size" not in result.debug:
            raise RuntimeError("flow motion needs tracked boxes from the detector")
        w, h = result.debug["infer_size"]
        small = cv2.resize(frame.image, (w, h), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        motions, info = self.motion.update(frame.received_at, gray, result.debug["boxes"])

        sectors = []
        for base, contribution in zip(result.sectors, self.motion.sector_evidence(motions, w), strict=True):
            if contribution.risk > base.risk:
                sectors.append(replace(contribution, nearest_m=base.nearest_m))
            else:
                sectors.append(base)
        flow_ms = (time.perf_counter() - started) * 1000
        debug = {**result.debug, "flow": {**info, "tracks": motions, "ms": round(flow_ms, 2)}}
        return replace(result, sectors=tuple(sectors), inference_ms=result.inference_ms + flow_ms, debug=debug)


__all__ = ["Fit", "FlowMotion", "FlowMotionPerception", "TrackMotion", "measure"]
