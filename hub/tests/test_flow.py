"""Optical-flow motion on synthetic textured scenes. No models, camera or hardware.

These check the geometry and the merge rules, not real-world accuracy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np
import pytest

from haptic_hub.config import FlowConfig, PerceptionConfig
from haptic_hub.perception import build_perception
from haptic_hub.perception.flow import FlowMotion, FlowMotionPerception, measure
from haptic_hub.types import Frame, PerceptionResult, SectorRisk

W, H = 384, 288
DT = 0.05  # 20 fps
N = 12


@dataclass(frozen=True)
class Box:
    track_id: int | None
    label: str
    x1: float
    y1: float
    x2: float
    y2: float


def texture(seed: int, w: int = W, h: int = H) -> np.ndarray:
    """Noise at several scales, so coarse pyramid levels still have structure."""
    rng = np.random.default_rng(seed)
    acc = np.zeros((h, w), np.float32)
    for sigma, weight in ((1.5, 1.0), (4, 1.5), (10, 2.0)):
        acc += weight * sigma * cv2.GaussianBlur(rng.random((h, w)).astype(np.float32), (0, 0), sigma)
    acc -= acc.min()
    return (acc / acc.max() * 255).astype(np.uint8)


BACKGROUND = texture(1)
OBJECT = texture(2)


def render(box: tuple[float, float, float, float], *, pan_px: float = 0.0, object_scale: float = 1.0) -> np.ndarray:
    """Background shifted by pan_px; the object texture scaled about its own center
    and pasted inside `box` (which also moves with the pan)."""
    shift = np.float32([[1, 0, pan_px], [0, 1, 0]])
    frame = cv2.warpAffine(BACKGROUND, shift, (W, H), borderMode=cv2.BORDER_REFLECT)
    x1, y1, x2, y2 = box
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    m = cv2.getRotationMatrix2D((W / 2, H / 2), 0, object_scale)
    m[:, 2] += (cx - W / 2, cy - H / 2)  # texture centered on the box
    obj = cv2.warpAffine(OBJECT, m, (W, H), borderMode=cv2.BORDER_REFLECT)
    ix1, iy1, ix2, iy2 = (max(0, round(x1)), max(0, round(y1)), min(W, round(x2)), min(H, round(y2)))
    frame[iy1:iy2, ix1:ix2] = obj[iy1:iy2, ix1:ix2]
    return frame


def scaled_box(cx: float, cy: float, bw: float, bh: float, s: float) -> tuple[float, float, float, float]:
    return (cx - bw * s / 2, cy - bh * s / 2, cx + bw * s / 2, cy + bh * s / 2)


def run(motion: FlowMotion, frames):
    out = None
    for i, (gray, boxes) in enumerate(frames):
        out = motion.update(i * DT, gray, boxes)
    return out


def new_motion(**overrides) -> FlowMotion:
    return FlowMotion(FlowConfig(**overrides), sectors=5, ttc_near_s=1.0, ttc_far_s=3.0)


def test_approaching_object_gets_ttc_and_risk_in_its_sectors():
    rate = 0.5  # ln(scale) per second -> TTC = 2 s
    frames = []
    for i in range(N):
        s = math.exp(rate * i * DT)
        box = scaled_box(W / 2, H / 2, 80, 110, s)
        frames.append((render(box, object_scale=s), [Box(1, "person", *box)]))
    motions, info = run(new_motion(), frames)
    (m,) = motions
    assert m.status == "measured"
    assert m.ttc_s == pytest.approx(2.0, rel=0.25)
    assert 0 < m.approach_risk < 1
    assert info["background"] is not None
    sectors = new_motion().sector_contributions(motions, W)
    assert sectors[2][0] == pytest.approx(m.approach_risk)
    assert sectors[0][0] == 0 and sectors[4][0] == 0
    assert sectors[2][2] == "person"


def test_still_scene_adds_no_risk():
    box = scaled_box(W / 2, H / 2, 100, 150, 1.0)
    frames = [(render(box), [Box(1, "chair", *box)]) for _ in range(N)]
    motions, _ = run(new_motion(), frames)
    (m,) = motions
    assert m.status == "measured"
    assert m.ttc_s is None or m.ttc_s > 3.0
    assert m.approach_risk == 0 and m.crossing_risk == 0


def test_near_object_crossing_toward_heading_raises_swept_sectors():
    speed = 0.3 * W  # px per second, left to right
    frames = []
    for i in range(N):
        x = 40 + speed * i * DT
        box = (x, 60.0, x + 90, 60.0 + 0.6 * H)
        frames.append((render(box), [Box(7, "person", *box)]))
    motions, _ = run(new_motion(), frames)
    (m,) = motions
    assert m.vx == pytest.approx(0.3, rel=0.2)
    assert m.crossing_risk == FlowConfig().crossing_risk
    # Projected 1 s ahead it sweeps from its current place into the middle.
    assert 2 in m.crossing_sectors
    sectors = new_motion().sector_contributions(motions, W)
    assert all(sectors[s][0] == m.crossing_risk for s in m.crossing_sectors)


def test_head_pan_is_not_a_crossing():
    """Everything shifts together: relative to the background the object is still."""
    box0 = (100.0, 60.0, 190.0, 60.0 + 0.6 * H)
    frames = []
    for i in range(N):
        pan = 0.3 * W * i * DT
        box = (box0[0] + pan, box0[1], box0[2] + pan, box0[3])
        frames.append((render(box, pan_px=pan), [Box(3, "person", *box)]))
    motions, _ = run(new_motion(), frames)
    (m,) = motions
    assert m.vx is not None and abs(m.vx) < FlowConfig().min_cross_speed
    assert m.crossing_risk == 0


def test_far_crossing_object_is_ignored():
    """Small (far) boxes moving sideways are below crossing_min_box_height."""
    frames = []
    for i in range(N):
        x = 60 + 0.3 * W * i * DT
        box = (x, 100.0, x + 40, 150.0)
        frames.append((render(box), [Box(4, "person", *box)]))
    motions, _ = run(new_motion(), frames)
    assert motions[0].crossing_risk == 0


def test_textureless_frames_report_insufficient_features_without_raising():
    flat = np.full((H, W), 128, np.uint8)
    box = (150.0, 100.0, 250.0, 250.0)
    motions, info = run(new_motion(), [(flat, [Box(1, "person", *box)]) for _ in range(5)])
    assert motions[0].status == "insufficient_features"
    assert motions[0].approach_risk == 0 and info["background"] is None


def test_no_reference_frame_when_history_is_too_short():
    motion = new_motion()
    box = (150.0, 100.0, 250.0, 250.0)
    gray = render(box)
    motion.update(0.0, gray, [Box(1, "person", *box)])
    motions, info = motion.update(0.03, gray, [Box(1, "person", *box)])  # 30 ms < min_dt_s
    assert motions[0].status == "no_reference" and info["dt"] is None


def test_new_track_has_no_reference_and_untracked_boxes_are_skipped():
    motion = new_motion()
    box = (150.0, 100.0, 250.0, 250.0)
    motion.update(0.0, render(box), [Box(1, "person", *box)])
    motions, _ = motion.update(0.15, render(box), [Box(2, "person", *box), Box(None, "cup", *box)])
    assert [(m.track_id, m.status) for m in motions] == [(2, "no_reference")]


def test_measure_rejects_size_change():
    with pytest.raises(ValueError):
        measure(np.zeros((10, 10), np.uint8), np.zeros((12, 10), np.uint8), [], FlowConfig())


# ---- wrapper: merge rules and wiring ----


class FakeInner:
    name = "fake"

    def __init__(self, risks, boxes, with_boxes=True):
        self.risks, self.boxes, self.with_boxes = risks, boxes, with_boxes

    def process(self, frame: Frame) -> PerceptionResult:
        debug = {"infer_size": (W, H)}
        if self.with_boxes:
            debug["boxes"] = self.boxes(frame.seq)
        sectors = tuple(SectorRisk(r, nearest_m=1.5 if r else None, label="depth" if r else None) for r in self.risks)
        return PerceptionResult(frame.seq, frame.received_at, sectors, 5.0, debug)


def approach_frames(n=N, rate=1.0):
    """BGR frames of an object approaching in the middle; TTC = 1/rate s."""
    out = []
    for i in range(n):
        s = math.exp(rate * i * DT)
        box = scaled_box(W / 2, H / 2, 80, 110, s)
        gray = render(box, object_scale=s)
        out.append((Frame(i, cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR), i * DT), Box(1, "person", *box)))
    return out


def test_wrapper_raises_risk_but_never_lowers_it():
    frames = approach_frames()
    inner = FakeInner([0.9, 0.0, 0.0, 0.0, 0.0], lambda seq: [frames[seq][1]])
    p = FlowMotionPerception(inner, FlowConfig(), 5, ttc_near_s=1.0, ttc_far_s=3.0)
    result = None
    for frame, _ in frames:
        result = p.process(frame)
    assert p.name == "fake+flow"
    assert result.sectors[0].risk == 0.9 and result.sectors[0].label == "depth"  # untouched
    assert result.sectors[2].risk > 0.5 and result.sectors[2].label == "person"  # raised by flow
    assert result.sectors[2].ttc_s == pytest.approx(1.0, rel=0.25)
    assert result.debug["flow"]["tracks"][0].status == "measured"
    assert result.inference_ms > 5.0


def test_wrapper_keeps_higher_inner_risk_in_the_same_sector():
    frames = approach_frames(rate=0.5)  # flow risk ~0.5
    inner = FakeInner([0.0, 0.0, 1.0, 0.0, 0.0], lambda seq: [frames[seq][1]])
    p = FlowMotionPerception(inner, FlowConfig(), 5, 1.0, 3.0)
    for frame, _ in frames:
        result = p.process(frame)
    assert result.sectors[2].risk == 1.0 and result.sectors[2].label == "depth"


def test_wrapper_without_tracked_boxes_raises_so_the_hub_faults():
    inner = FakeInner([0.0] * 5, lambda seq: [], with_boxes=False)
    p = FlowMotionPerception(inner, FlowConfig(), 5, 1.0, 3.0)
    with pytest.raises(RuntimeError):
        p.process(Frame(0, np.zeros((H, W, 3), np.uint8), 0.0))


@pytest.mark.parametrize("kind", ["sweep", "depth"])
def test_flow_needs_the_detector(kind):
    cfg = PerceptionConfig(motion="flow")
    with pytest.raises(ValueError, match="detector"):
        build_perception(kind, cfg)


def test_unknown_motion_is_rejected():
    with pytest.raises(ValueError, match="motion"):
        build_perception("sweep", PerceptionConfig(motion="magic"))


def test_motion_off_is_the_default():
    assert PerceptionConfig().motion == "off"
    assert build_perception("sweep", PerceptionConfig()).name == "sweep"
