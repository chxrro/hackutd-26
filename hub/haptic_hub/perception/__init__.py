"""Frame -> per-sector collision risk."""

from __future__ import annotations

from haptic_hub.config import PerceptionConfig
from haptic_hub.perception.base import Perception
from haptic_hub.perception.sweep import SweepPerception

KINDS = ("vision", "depth", "detector", "sweep")
DETECTORS = ("yolo", "openvocab")
MOTIONS = ("off", "flow")


def build_perception(kind: str, cfg: PerceptionConfig) -> Perception:
    """vision = depth + detector, depth / detector = one model only, sweep = fake.
    cfg.motion == "flow" adds optical-flow approach/crossing on the tracked boxes."""
    if kind not in KINDS:
        raise ValueError(f"unknown perception {kind!r}; choose from {KINDS}")
    if cfg.detector not in DETECTORS:
        raise ValueError(f"unknown detector {cfg.detector!r}; choose from {DETECTORS}")
    if cfg.motion not in MOTIONS:
        raise ValueError(f"unknown motion {cfg.motion!r}; choose from {MOTIONS}")
    if cfg.motion == "flow" and kind not in ("vision", "detector"):
        raise ValueError("--motion flow needs the detector's tracked boxes: use --perception vision or detector")
    if kind == "sweep":
        return SweepPerception(cfg.sectors)

    from haptic_hub.perception.models import DepthEstimator
    from haptic_hub.perception.vision import VisionPerception

    depth = DepthEstimator(cfg.depth_model, cfg.device) if kind in ("vision", "depth") else None
    tracker = build_tracker(cfg) if kind in ("vision", "detector") else None
    perception: Perception = VisionPerception(cfg, depth, tracker)
    if cfg.motion == "flow":
        from haptic_hub.perception.flow import FlowMotionPerception

        perception = FlowMotionPerception(perception, cfg.flow, cfg.sectors, cfg.ttc_near_s, cfg.ttc_far_s)
    return perception


def build_tracker(cfg: PerceptionConfig):
    """yolo = COCO YOLO (models.ObjectTracker), openvocab = YOLOE with text prompts.
    Both return TrackedBox lists with ByteTrack IDs."""
    if cfg.detector == "openvocab":
        from haptic_hub.perception.openvocab import OpenVocabTracker

        return OpenVocabTracker(cfg.openvocab_model, cfg.openvocab_prompts, cfg.device, cfg.tracker_conf)
    from haptic_hub.perception.models import ObjectTracker

    return ObjectTracker(cfg.yolo_model, cfg.device, cfg.tracker_conf)


__all__ = ["DETECTORS", "KINDS", "MOTIONS", "Perception", "build_perception", "build_tracker"]
