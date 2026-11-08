import math

import numpy as np
import pytest

from haptic_hub.config import HapticsConfig, PerceptionConfig
from haptic_hub.evaluate import CachedModel, compare_frames, load_frames
from haptic_hub.haptics import HapticPolicy
from haptic_hub.perception.models import TrackedBox
from haptic_hub.perception.ttc import TtcEstimator
from haptic_hub.perception.vision import VisionPerception
from haptic_hub.protocol import BeltMode
from haptic_hub.types import Frame, Health, PerceptionResult, SectorRisk


def result(seq, t, risk):
    return PerceptionResult(seq, t, (SectorRisk(risk),), 0)


def stable():
    return HapticPolicy(HapticsConfig(profile="stable"), 1)


def test_flash_filtered_and_sustained_risk_confirmed():
    p = stable()
    assert p.command(result(1, 0, .5), Health(True)).mode == BeltMode.OFF
    assert p.decisions == ["confirming"]
    assert p.command(result(2, .1, 0), Health(True)).mode == BeltMode.OFF
    for seq, t in [(3, .2), (4, .3)]:
        assert p.command(result(seq, t, .5), Health(True)).mode == BeltMode.OFF
    assert p.command(result(5, .4, .5), Health(True)).mode == BeltMode.LEVELS
    assert p.decisions == ["confirmed"]


def test_urgent_and_faults_immediate():
    p = stable()
    assert p.command(result(1, 0, 1), Health(True)).levels == (255,)
    assert p.decisions == ["urgent"]
    assert p.command(result(2, .1, 1), Health(False, "stale")).mode == BeltMode.FAULT
    assert p.command(result(3, .2, .5), Health(True)).mode == BeltMode.OFF


def test_escalation_bypasses_pending_confirmation():
    p = stable()
    assert p.command(result(1, 0, .4), Health(True)).mode == BeltMode.OFF
    assert p.command(result(2, .05, 1), Health(True)).levels == (255,)


def test_sectors_confirm_independently():
    p = HapticPolicy(HapticsConfig(profile="stable"), 2)
    for i in range(3):
        r = PerceptionResult(i, i * .1, (SectorRisk(.5), SectorRisk(.5 if i == 2 else 0)), 0)
        cmd = p.command(r, Health(True))
    assert cmd.levels[0] > 0 and cmd.levels[1] == 0


def test_heartbeats_and_two_samples_do_not_confirm():
    p = stable()
    r = result(1, 0, .5)
    for _ in range(20):
        assert p.command(r, Health(True)).mode == BeltMode.OFF
    assert p.command(result(2, .25, .5), Health(True)).mode == BeltMode.OFF
    assert p.command(result(3, .29, .5), Health(True)).mode == BeltMode.LEVELS


@pytest.mark.parametrize("times", [(0, .1, .5), (0, .1, .05)])
def test_gap_or_backward_time_requires_new_confirmation(times):
    p = stable()
    for seq, t in enumerate(times):
        assert p.command(result(seq, t, .5), Health(True)).mode == BeltMode.OFF


def test_sequence_restart_resets_confirmation():
    p = stable()
    p.command(result(100, 0, .5), Health(True))
    p.command(result(101, .1, .5), Health(True))
    assert p.command(result(0, .2, .5), Health(True)).mode == BeltMode.OFF


def test_release_time_based_and_duplicates_do_not_decay():
    levels = []
    for hz in (10, 20):
        p = stable()
        p.command(result(0, 0, 1), Health(True))
        for i in range(1, int(hz * .2) + 1):
            r = result(i, i / hz, 0)
            cmd = p.command(r, Health(True))
            assert p.command(r, Health(True)) == cmd
        levels.append(cmd.levels)
    assert levels[0] == levels[1]


@pytest.mark.parametrize("risk", [math.nan, math.inf, -math.inf])
def test_invalid_risk_faults(risk):
    assert stable().command(result(1, 0, risk), Health(True)).mode == BeltMode.FAULT


def test_robust_ttc_constant_speed_approach():
    p = TtcEstimator(robust=True)
    for t in np.arange(0, .51, .05):
        ttc = p.update(1, float(t), 400 / (4 - t))
    assert ttc == pytest.approx(3.5, abs=.01)


def test_robust_ttc_rejects_static_jitter_and_box_spike():
    p = TtcEstimator(robust=True)
    for i in range(20):
        assert p.update(1, i * .05, 100 + [0, 8, -5, 6, -8][i % 5]) is None
    assert p.update(1, 1.0, 130) is None


def test_clipping_resets_ttc_but_preserves_depth():
    p = TtcEstimator(robust=True)
    for i in range(8):
        p.update(1, i * .05, 100 / (1 - i * .05))
    assert p.update(1, .4, 170, clipped=True) is None
    assert p.update(1, .45, 180) is None

    class Depth:
        def estimate(self, rgb):
            return np.full(rgb.shape[:2], .5)

    class Tracker:
        def track(self, bgr):
            return [TrackedBox(1, "person", .9, 0, 0, 100, 100)]

    vision = VisionPerception(PerceptionConfig(robust_ttc=True), Depth(), Tracker())
    r = vision.process(Frame(1, np.zeros((100, 100, 3), np.uint8), 1))
    assert all(s.risk == 1 and s.evidence == "depth" for s in r.sectors)


def test_robust_ttc_bad_time_gap_and_height_reset():
    for bad_t, bad_h in [(0.1, 120), (1.0, 120), (.15, math.nan), (.15, -1)]:
        p = TtcEstimator(robust=True)
        p.update(1, 0, 100)
        p.update(1, .05, 105)
        p.update(1, .1, 110)
        assert p.update(1, bad_t, bad_h) is None


def test_evaluator_reuses_predictions_and_does_not_claim_accuracy():
    class Model:
        calls = 0
        def estimate(self, image):
            self.calls += 1
            return image

    cache = CachedModel(Model(), "estimate")

    class Perception:
        def process(self, f):
            cache.estimate(f.image)
            return PerceptionResult(f.seq, f.received_at, (SectorRisk(.5),) * 5, 0)

    frames = [Frame(i, np.zeros((1, 1, 3)), i / 10) for i in range(4)]
    report = compare_frames(frames, {"baseline": Perception(), "stable": Perception()}, [cache])
    assert cache.model.calls == len(frames)
    assert report["kind"] == "offline_replay_not_accuracy"
    assert report["summary"]["baseline"]["alert_frames"] == 4
    assert report["summary"]["stable"]["alert_frames"] == 2
    assert report["summary"]["stable"]["alert_seconds"] == pytest.approx(.1)
    with pytest.raises(ValueError, match="strictly increasing"):
        compare_frames([frames[0], frames[0]], {"baseline": Perception()})


def test_evaluator_rejects_missing_or_escaping_images(tmp_path):
    import json
    for file in ["missing.jpg", "../outside.jpg"]:
        (tmp_path / "frames.jsonl").write_text(json.dumps({"seq": 1, "received_at": 1, "file": file}))
        with pytest.raises(ValueError):
            list(load_frames(tmp_path, 10))
