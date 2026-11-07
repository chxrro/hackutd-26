"""Thin wrappers around the pretrained models. Heavy imports happen lazily so the
rest of the hub (belt test, fake perception, unit tests) works without torch."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from haptic_hub.perception.base import resolve_device

log = logging.getLogger(__name__)


class DepthEstimator:
    """Depth Anything V2 (metric variant): RGB image -> depth map in meters.

    Metric monocular depth is an *estimate*. Check it against a tape measure
    before trusting the near/far thresholds.
    """

    def __init__(self, model_id: str, device: str = "auto") -> None:
        from transformers import pipeline

        self.device = resolve_device(device)
        log.info("loading depth model %s on %s", model_id, self.device)
        self._pipe = pipeline("depth-estimation", model=model_id, device=self.device)

    def estimate(self, rgb: np.ndarray) -> np.ndarray:
        from PIL import Image

        out = self._pipe(Image.fromarray(rgb))
        depth = out["predicted_depth"]
        return depth.squeeze().float().cpu().numpy()


@dataclass(frozen=True)
class TrackedBox:
    track_id: int | None
    label: str
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def height(self) -> float:
        return self.y2 - self.y1


class ObjectTracker:
    """YOLO detector + ByteTrack, so the same object keeps its ID across frames."""

    def __init__(self, model_path: str, device: str = "auto", confidence: float = 0.35) -> None:
        from ultralytics import YOLO

        self.device = resolve_device(device)
        self.confidence = confidence
        log.info("loading detector %s on %s", model_path, self.device)
        self._model = YOLO(model_path)

    def track(self, bgr: np.ndarray) -> list[TrackedBox]:
        results = self._model.track(
            bgr,
            persist=True,
            tracker="bytetrack.yaml",
            conf=self.confidence,
            device=self.device,
            verbose=False,
        )
        boxes = results[0].boxes
        if boxes is None or len(boxes) == 0:
            return []
        names = results[0].names
        xyxy = boxes.xyxy.cpu().numpy()
        cls = boxes.cls.cpu().numpy().astype(int)
        conf = boxes.conf.cpu().numpy()
        ids = boxes.id.cpu().numpy().astype(int).tolist() if boxes.id is not None else [None] * len(cls)
        return [
            TrackedBox(tid, names[int(c)], float(p), *map(float, xy))
            for tid, c, p, xy in zip(ids, cls, conf, xyxy, strict=True)
        ]
