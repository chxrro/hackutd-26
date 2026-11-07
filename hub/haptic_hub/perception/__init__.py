"""Frame -> per-sector collision risk."""

from __future__ import annotations

from haptic_hub.config import PerceptionConfig
from haptic_hub.perception.base import Perception
from haptic_hub.perception.sweep import SweepPerception

KINDS = ("vision", "depth", "detector", "sweep")


def build_perception(kind: str, cfg: PerceptionConfig) -> Perception:
    """vision = depth + detector, depth / detector = one model only, sweep = fake."""
    if kind == "sweep":
        return SweepPerception(cfg.sectors)
    if kind not in KINDS:
        raise ValueError(f"unknown perception {kind!r}; choose from {KINDS}")

    from haptic_hub.perception.models import DepthEstimator, ObjectTracker
    from haptic_hub.perception.vision import VisionPerception

    depth = DepthEstimator(cfg.depth_model, cfg.device) if kind in ("vision", "depth") else None
    tracker = (
        ObjectTracker(cfg.yolo_model, cfg.device, cfg.detector_conf) if kind in ("vision", "detector") else None
    )
    return VisionPerception(cfg, depth, tracker)


__all__ = ["KINDS", "Perception", "build_perception"]
