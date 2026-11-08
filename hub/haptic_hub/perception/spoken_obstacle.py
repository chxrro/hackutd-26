"""Optional speech metadata for one current belt alert."""

from __future__ import annotations

import numpy as np

from haptic_hub.config import PerceptionConfig
from haptic_hub.perception.geometry import box_sectors, linear_risk
from haptic_hub.perception.models import TrackedBox
from haptic_hub.types import PerceptionResult


def _box_depth(depth: np.ndarray, box: TrackedBox, width: int, height: int) -> float | None:
    """Median camera depth in the central half of this detection's box."""
    if depth.ndim != 2 or not np.isfinite([box.x1, box.x2, box.y1, box.y2]).all():
        return None
    rows, cols = depth.shape
    x1, x2 = np.clip([box.x1, box.x2], 0, width) * (cols / width)
    y1, y2 = np.clip([box.y1, box.y2], 0, height) * (rows / height)
    dx, dy = (x2 - x1) / 4, (y2 - y1) / 4
    patch = depth[int(y1 + dy):int(y2 - dy), int(x1 + dx):int(x2 - dx)]
    # Saturated values from the indoor metric model do not provide a usable estimate.
    valid = patch[np.isfinite(patch) & (patch > 0) & (patch < 20)]
    return float(np.median(valid)) if valid.size and valid.size >= patch.size / 2 else None


def spoken_obstacle(result: PerceptionResult, levels: tuple[int, ...], cfg: PerceptionConfig) -> dict | None:
    if len(result.sectors) != len(levels):
        return None

    active = {i: s for i, s in enumerate(result.sectors) if levels[i] > 0 and s.risk > 0}
    candidates = []
    center = (len(levels) - 1) / 2

    def add_candidate(sector: int, kind: str, distance: float | None, approach: float, risk: float) -> None:
        score = (risk, -abs(sector - center), kind != "obstacle", approach)
        candidates.append((score, {
            "sector": sector,
            "kind": kind,
            "distance_m": float(distance) if distance is not None else None,
            "closing": bool(approach > 0),
            "urgent": bool(risk >= 1.0),
        }))

    # A depth hazard can be real even when the detector has no name for it.
    depth_risks = {}
    for sector, evidence in active.items():
        nearest = evidence.nearest_m
        if nearest is not None and 0 < nearest < cfg.far_m:
            risk = linear_risk(nearest, cfg.near_m, cfg.far_m)
            depth_risks[sector] = risk
            add_candidate(sector, "obstacle", nearest, 0.0, risk)

    width, height = result.debug.get("infer_size", (0, 0))
    depth = result.debug.get("depth")
    track_ttc = result.debug.get("track_ttc", {})
    flow_risks = {
        motion.track_id: motion.approach_risk for motion in result.debug.get("flow", {}).get("tracks", ())
        if motion.status == "measured" and motion.approach_risk > 0
    }
    for detection in result.debug.get("classified", ()):
        box = detection.box
        if (not width or not height or detection.kind not in ("person", "object")
                or box.confidence < cfg.confident_conf or box.height / height < cfg.min_box_height):
            continue
        overlaps = [i for i in box_sectors(box.x1, box.x2, width, len(levels)) if i in active]
        if not overlaps:
            continue
        approach = max(
            linear_risk(track_ttc.get(box.track_id), cfg.ttc_near_s, cfg.ttc_far_s),
            flow_risks.get(box.track_id, 0.0),
        )
        distance = _box_depth(depth, box, width, height) if isinstance(depth, np.ndarray) else None
        for sector in overlaps:
            # Sector depth alone can't identify a box. Require its own depth
            # to be close to the hazard, or independent approach on this track.
            nearest = active[sector].nearest_m
            matches_depth = (
                distance is not None and distance < cfg.far_m and nearest is not None
                and abs(distance - nearest) <= 0.5
            )
            risk = max(approach, depth_risks.get(sector, 0.0) if matches_depth else 0.0)
            if risk > 0:
                add_candidate(sector, detection.kind, distance, approach, risk)

    best = max(candidates, key=lambda candidate: candidate[0], default=None)
    return best[1] if best is not None else None
