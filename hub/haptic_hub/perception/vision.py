"""Real perception: fuses a depth map (static obstacles) with tracked objects
(things coming toward you) into one risk per belt sector."""

from __future__ import annotations

import math
import time

import cv2
import numpy as np

from haptic_hub.config import PerceptionConfig
from haptic_hub.perception.geometry import (
    FloorPlane,
    back_project,
    box_sectors,
    depth_sector_nearest,
    fit_floor,
    linear_risk,
    obstacle_sector_nearest,
    prior_floor,
)
from haptic_hub.perception.models import DepthEstimator, ObjectTracker
from haptic_hub.perception.ttc import TtcEstimator
from haptic_hub.types import Frame, PerceptionResult, SectorRisk

DEPTH_GEOMETRIES = ("floor", "band")


def focal_px(width: int, hfov_deg: float) -> float:
    return (width / 2) / math.tan(math.radians(hfov_deg) / 2)


class FloorDepth:
    """Per-sector nearest obstacle from a metric depth map, relative to a fitted floor.

    Each frame: back-project the depth map with the (assumed) intrinsics, fit the floor
    plane, then measure only points that stand above it and below head height. If no
    plausible floor is found (e.g. a wall fills the view), the last fit is reused for
    cfg.floor_hold_s, then a level camera at cfg.eye_height_m is assumed.

    The floor is only trusted if few depth points lie *below* it (more than
    cfg.floor_max_below_frac means the depth map isn't consistent with one plane and
    these intrinsics, e.g. a monocular model's distorted floor). An untrusted frame
    falls back to the fixed image band (cfg.band_top..band_bottom), the old behavior,
    and says so in the debug info.
    """

    def __init__(self, cfg: PerceptionConfig, seed: int = 0) -> None:
        self.cfg = cfg
        self._rng = np.random.default_rng(seed)
        self._last: FloorPlane | None = None

    def floor(self, pts: np.ndarray, now: float) -> FloorPlane:
        """pts: (N, 3) camera-frame points sampled from the depth map."""
        cfg = self.cfg
        prior = prior_floor(cfg.eye_height_m)
        held = self._last if self._last is not None and now - self._last.t <= cfg.floor_hold_s else None
        fit = fit_floor(
            pts, self._rng, [hp for hp in (held, prior) if hp is not None],
            max_tilt_deg=cfg.floor_max_tilt_deg, height_range=cfg.floor_height_range,
            tol_m=cfg.floor_tol_m, tol_rel=cfg.floor_tol_rel,
            min_inlier_frac=cfg.floor_min_inlier_frac,
        )  # fmt: skip
        if fit is not None:
            return FloorPlane(fit.normal, fit.height_m, "fit", fit.inliers, now)
        if held is not None:
            return FloorPlane(held.normal, held.height_m, "held", 0, held.t)
        return prior

    def below_fraction(self, pts: np.ndarray, plane: FloorPlane) -> float:
        """Share of points clearly below the floor (physically implausible without a drop-off)."""
        if len(pts) == 0:
            return 0.0
        tol = self.cfg.floor_tol_m + self.cfg.floor_tol_rel * np.linalg.norm(pts, axis=1)
        height = pts @ np.array(plane.normal, np.float32) + plane.height_m
        return float(np.mean(height < -tol))

    def nearest(self, depth_m: np.ndarray, now: float) -> tuple[list[float | None], dict]:
        cfg = self.cfg
        # A 5x5 median removes isolated bad pixels (depth noise, edge artifacts) that
        # would otherwise stand out of the floor; obstacles are much wider than 5 px.
        smooth = cv2.medianBlur(np.ascontiguousarray(depth_m, np.float32), 5)
        f = focal_px(smooth.shape[1], cfg.hfov_deg)
        sample, ok = back_project(smooth, f, max(1, cfg.floor_stride))
        flat = sample[ok]
        flat = flat[flat[:, 2] < 20.0]  # sky / saturated depth carries no floor information
        plane = self.floor(flat, now)
        below = self.below_fraction(flat, plane)
        info = {
            "source": plane.source,
            "pitch_deg": round(plane.pitch_deg, 1),
            "tilt_deg": round(plane.tilt_deg(), 1),
            "height_m": round(plane.height_m, 2),
            "inliers": plane.inliers,
            "below_frac": round(below, 3),
        }
        if below > cfg.floor_max_below_frac:
            info["source"] = f"untrusted {plane.source} -> band"
            nearest = depth_sector_nearest(depth_m, cfg.sectors, cfg.band_top, cfg.band_bottom, cfg.depth_percentile)
            return nearest, info
        if plane.source == "fit":
            self._last = plane  # only a trusted fit may be held
        pts, valid = back_project(smooth, f, 2)
        nearest = obstacle_sector_nearest(
            pts, valid, plane, cfg.sectors, cfg.obstacle_min_height_m, cfg.obstacle_max_above_eye_m,
            cfg.obstacle_percentile,
        )  # fmt: skip
        return nearest, info


class VisionPerception:
    name = "vision"

    def __init__(
        self,
        cfg: PerceptionConfig,
        depth: DepthEstimator | None,
        tracker: ObjectTracker | None,
    ) -> None:
        if depth is None and tracker is None:
            raise ValueError("need at least a depth model or a detector")
        if cfg.depth_geometry not in DEPTH_GEOMETRIES:
            raise ValueError(f"unknown depth_geometry {cfg.depth_geometry!r}; choose from {DEPTH_GEOMETRIES}")
        self.cfg = cfg
        self._depth = depth
        self._tracker = tracker
        self._ttc = TtcEstimator.from_config(cfg)
        self._floor = FloorDepth(cfg)

    def process(self, frame: Frame) -> PerceptionResult:
        started = time.perf_counter()
        cfg, n = self.cfg, self.cfg.sectors

        h0, w0 = frame.image.shape[:2]
        scale = cfg.infer_width / w0
        bgr = cv2.resize(frame.image, (cfg.infer_width, max(1, round(h0 * scale))), interpolation=cv2.INTER_AREA)
        h, w = bgr.shape[:2]

        risk = [0.0] * n
        nearest: list[float | None] = [None] * n
        ttc: list[float | None] = [None] * n
        label: list[str | None] = [None] * n
        uncertain = [False] * n
        debug: dict = {"infer_size": (w, h)}

        if self._depth is not None:
            depth = self._depth.estimate(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
            debug["depth"] = depth
            if cfg.depth_geometry == "floor":
                per_sector, debug["floor"] = self._floor.nearest(depth, frame.received_at)
            else:
                per_sector = depth_sector_nearest(depth, n, cfg.band_top, cfg.band_bottom, cfg.depth_percentile)
            for i, d in enumerate(per_sector):
                nearest[i] = d
                risk[i] = linear_risk(d, cfg.near_m, cfg.far_m)

        if self._tracker is not None:
            boxes = self._tracker.track(bgr)
            confident = cfg.confident_conf
            debug["boxes"] = [b for b in boxes if b.confidence >= confident]
            debug["uncertain_boxes"] = [b for b in boxes if b.confidence < confident]
            track_ttc = {}
            debug["track_ttc"] = track_ttc
            best_box_height = [0.0] * n
            for box in boxes:
                if box.height / h < cfg.min_box_height:
                    continue
                box_ttc = None
                if box.track_id is not None:  # uncertain boxes keep the track's history going too
                    box_ttc = self._ttc.update(box.track_id, frame.received_at, box.height)
                sectors = box_sectors(box.x1, box.x2, w, n)
                if box.confidence < confident:
                    for s in sectors:  # reported, never used for risk
                        uncertain[s] = True
                    continue
                if box.track_id is not None and box_ttc is not None:
                    track_ttc[box.track_id] = box_ttc
                box_risk = linear_risk(box_ttc, cfg.ttc_near_s, cfg.ttc_far_s)
                for s in sectors:
                    if box_risk > risk[s]:
                        risk[s] = box_risk
                    if box_ttc is not None and (ttc[s] is None or box_ttc < ttc[s]):
                        ttc[s] = box_ttc
                    if box.height > best_box_height[s]:  # name the biggest object in the sector
                        best_box_height[s] = box.height
                        label[s] = box.label
            self._ttc.prune(frame.received_at)

        sectors = tuple(SectorRisk(risk[i], nearest[i], ttc[i], label[i], uncertain[i]) for i in range(n))
        return PerceptionResult(
            frame_seq=frame.seq,
            frame_received_at=frame.received_at,
            sectors=sectors,
            inference_ms=(time.perf_counter() - started) * 1000,
            debug=debug,
        )
