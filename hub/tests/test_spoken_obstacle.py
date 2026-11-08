import json
from dataclasses import replace
from types import SimpleNamespace

import numpy as np

from haptic_hub.app import Hub
from haptic_hub.config import HubConfig
from haptic_hub.haptics import HapticPolicy
from haptic_hub.perception.classify import ClassifiedBox, ClassifiedPerception
from haptic_hub.perception.flow import TrackMotion
from haptic_hub.perception.models import TrackedBox
from haptic_hub.perception.spoken_obstacle import spoken_obstacle
from haptic_hub.perception.vision import VisionPerception
from haptic_hub.protocol import BeltCommand, BeltMode
from haptic_hub.types import Frame, Health, PerceptionResult, SectorRisk


LEVELS = (0, 0, 160, 0, 0)


def detection_result():
    box = TrackedBox(1, "person", 0.9, 40, 0, 60, 80)
    depth = np.full((100, 100), 0.5)
    depth[20:60, 45:55] = 2.4
    return PerceptionResult(1, 9.9, (SectorRisk(0), SectorRisk(0), SectorRisk(0.1, nearest_m=2.4),
                                    SectorRisk(0), SectorRisk(0)), 5, {
        "infer_size": (100, 100), "classified": [ClassifiedBox(box, "person", "local")], "depth": depth,
    })


def test_distance_and_motion_belong_to_the_same_detection():
    result = detection_result()
    other = TrackMotion(2, "person", (40, 0, 60, 80), "measured", approach_risk=0.5)
    result.debug["flow"] = {"tracks": [other]}
    cfg = HubConfig().perception
    assert spoken_obstacle(result, LEVELS, cfg) == {
        "sector": 2, "kind": "person", "distance_m": 2.4, "closing": False, "urgent": False,
    }
    result.debug["flow"] = {"tracks": [replace(other, track_id=1)]}
    assert spoken_obstacle(result, LEVELS, cfg)["closing"] is True


def test_missing_depth_is_omitted_from_a_closing_obstacle():
    result = detection_result()
    result = replace(result, sectors=(SectorRisk(0), SectorRisk(0), SectorRisk(0.5), SectorRisk(0), SectorRisk(0)))
    result.debug["depth"][:] = np.nan
    cfg = HubConfig().perception
    assert spoken_obstacle(result, LEVELS, cfg) is None
    result.debug["flow"] = {"tracks": [TrackMotion(1, "person", (40, 0, 60, 80), "measured", approach_risk=0.5)]}
    assert spoken_obstacle(result, LEVELS, cfg)["distance_m"] is None
    assert spoken_obstacle(result, (0, 0, 0, 0, 0), cfg) is None


def test_close_depth_hazard_is_spoken_ahead_of_a_bystander():
    result = detection_result()
    result = replace(result, sectors=(
        SectorRisk(0), SectorRisk(0), SectorRisk(1, nearest_m=0.5),
        SectorRisk(0.5, nearest_m=np.float32(1.5)), SectorRisk(0),
    ))
    cfg = HubConfig().perception
    assert spoken_obstacle(result, (0, 0, 160, 200, 0), cfg) == {
        "sector": 2, "kind": "obstacle", "distance_m": 0.5, "closing": False, "urgent": True,
    }
    side_alert = spoken_obstacle(result, (0, 0, 0, 200, 0), cfg)
    assert json.loads(json.dumps(side_alert)) == {
        "sector": 3, "kind": "obstacle", "distance_m": 1.5, "closing": False, "urgent": False,
    }


def test_low_confidence_and_unrelated_motion_cannot_name_an_obstacle():
    result = detection_result()
    low = replace(result.debug["classified"][0].box, confidence=0.18)
    result.debug["classified"] = [ClassifiedBox(low, "person", "local")]
    result.debug["track_ttc"] = {1: 0.5}
    cfg = HubConfig().perception
    assert spoken_obstacle(result, LEVELS, cfg)["kind"] == "obstacle"
    result = replace(result, sectors=(SectorRisk(0),) * 5)
    assert spoken_obstacle(result, LEVELS, cfg) is None


def test_vision_box_growth_can_raise_an_urgent_named_alert_without_depth_or_flow():
    cfg = HubConfig()
    cfg.perception.infer_width = 120
    boxes = []
    tracker = SimpleNamespace(track=lambda image: boxes)
    vision = ClassifiedPerception(VisionPerception(cfg.perception, None, tracker))
    image = np.zeros((120, 120, 3), np.uint8)
    for seq in range(7):
        at = seq / 10
        boxes[:] = [TrackedBox(1, "person", 0.9, 51, 10, 69, 10 + 40 / (1.35 - at))]
        result = vision.process(Frame(seq, image, at))
    command = HapticPolicy(cfg.haptics, cfg.perception.sectors).command(result, Health(True))
    assert spoken_obstacle(result, command.levels, cfg.perception) == {
        "sector": 2, "kind": "person", "distance_m": None, "closing": True, "urgent": True,
    }


def test_status_never_exposes_stale_obstacle_or_belt_cue(monkeypatch):
    monkeypatch.setattr("haptic_hub.app.time.monotonic", lambda: 10.0)
    hub = Hub(HubConfig(), SimpleNamespace(name="glasses"), None, SimpleNamespace(state="simulated"))
    hub.result = detection_result()
    hub.command = BeltCommand(BeltMode.LEVELS, LEVELS)
    hub.reason = None
    hub.health = Health(True)
    hub.safety.frame_received(9.9)
    hub.safety.perception_ok(9.9, 9.9)
    assert hub.status()["spoken_obstacle"]["distance_m"] == 2.4
    hub.reason = "resuming alerts"
    assert hub.status()["spoken_obstacle"] is None
    hub.reason = None
    hub.safety.perception_ok(9.9, 8.0)
    assert hub.status()["spoken_obstacle"] is None
