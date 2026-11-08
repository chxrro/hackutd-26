"""Ground-truth stand-ins for the two models, so the *rest* of the perception code
(haptic_hub.perception.vision: depth bands, percentiles, box-growth TTC, fusion)
runs unchanged on simulated frames.

Oracle results test the pipeline, not the models: they say nothing about how
well Depth Anything or YOLO would do on real glasses video.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np

from haptic_hub.config import PerceptionConfig
from haptic_hub.perception.models import TrackedBox
from haptic_hub.perception.vision import VisionPerception
from haptic_hub.sim.render import GtBox
from haptic_hub.types import Frame, PerceptionResult


@dataclass
class OracleNoise:
    """Optional imperfections, to see how the policy copes with a model that's wrong."""

    depth_scale: float = 1.0  # systematic bias: 1.1 = everything looks 10% farther
    depth_sigma: float = 0.0  # per-pixel multiplicative noise (fraction)
    miss_prob: float = 0.0  # chance a detector box is dropped on a frame
    box_jitter_px: float = 0.0  # gaussian noise on box edges (full-res pixels)


@dataclass
class Truth:
    depth: np.ndarray  # full-res z-depth, meters
    boxes: list[GtBox]


class TruthStore:
    """Ground truth per frame seq, written by the simulated phone, read by the oracle."""

    def __init__(self, keep: int = 64) -> None:
        self._data: dict[int, Truth] = {}
        self._keep = keep
        self._lock = threading.Lock()

    def put(self, seq: int, truth: Truth) -> None:
        with self._lock:
            self._data[seq] = truth
            for old in [s for s in self._data if s <= seq - self._keep]:
                del self._data[old]

    def get(self, seq: int) -> Truth:
        with self._lock:
            return self._data[seq]


class _OracleDepth:
    def __init__(self, noise: OracleNoise, rng: np.random.Generator) -> None:
        self.noise, self.rng = noise, rng
        self.truth: Truth | None = None

    def estimate(self, rgb: np.ndarray) -> np.ndarray:
        assert self.truth is not None
        h, w = rgb.shape[:2]
        depth = cv2.resize(self.truth.depth, (w, h), interpolation=cv2.INTER_NEAREST) * self.noise.depth_scale
        if self.noise.depth_sigma:
            depth = depth * (1 + self.rng.normal(0, self.noise.depth_sigma, depth.shape)).clip(0.1)
        return depth.astype(np.float32)


class _OracleTracker:
    """Like ObjectTracker: boxes scoring below `min_confidence` are dropped,
    as YOLO's `conf=` threshold does."""

    def __init__(self, full_width: int, min_confidence: float, noise: OracleNoise, rng: np.random.Generator) -> None:
        self.full_width, self.min_confidence, self.noise, self.rng = full_width, min_confidence, noise, rng
        self.truth: Truth | None = None

    def track(self, bgr: np.ndarray) -> list[TrackedBox]:
        assert self.truth is not None
        k = bgr.shape[1] / self.full_width
        out = []
        for b in self.truth.boxes:
            if b.confidence < self.min_confidence:
                continue
            if self.noise.miss_prob and self.rng.random() < self.noise.miss_prob:
                continue
            j = self.rng.normal(0, self.noise.box_jitter_px, 4) if self.noise.box_jitter_px else np.zeros(4)
            out.append(TrackedBox(b.track_id, b.label, b.confidence, *((np.array([b.x1, b.y1, b.x2, b.y2]) + j) * k)))
        return out


class OraclePerception:
    """VisionPerception with the models swapped for ground truth. Failures can be
    injected (`fail` / `stall_s`) to exercise the hub's safety monitor."""

    name = "oracle"

    def __init__(self, cfg: PerceptionConfig, truth: TruthStore, full_width: int,
                 noise: OracleNoise | None = None, use_depth: bool = True, use_tracker: bool = True,
                 seed: int = 0) -> None:  # fmt: skip
        rng = np.random.default_rng(seed)
        noise = noise or OracleNoise()
        self._truth = truth
        self._depth = _OracleDepth(noise, rng) if use_depth else None
        self._tracker = _OracleTracker(full_width, cfg.tracker_conf, noise, rng) if use_tracker else None
        self._inner = VisionPerception(cfg, self._depth, self._tracker)  # type: ignore[arg-type]
        self.fail: str | None = None  # set to an error message to make process() raise
        self.stall_s: float = 0.0  # extra time per frame (a slow or hung model)

    def process(self, frame: Frame) -> PerceptionResult:
        if self.stall_s:
            time.sleep(self.stall_s)
        if self.fail:
            raise RuntimeError(self.fail)
        truth = self._truth.get(frame.seq)
        for model in (self._depth, self._tracker):
            if model is not None:
                model.truth = truth
        return self._inner.process(frame)
