"""Risk attribution and the additive phone contract. No models or hardware."""
import json
import numpy as np
import pytest
from haptic_hub.config import PerceptionConfig, FlowConfig
from haptic_hub.hazards import hazard_snapshot
from haptic_hub.perception.models import TrackedBox
from haptic_hub.perception.vision import VisionPerception
from haptic_hub.perception.flow import FlowMotion, TrackMotion
from haptic_hub.protocol import BeltCommand, BeltMode
from haptic_hub.types import Frame, PerceptionResult, SectorRisk


def result(sector=SectorRisk(0.8, evidence="depth")):
    return PerceptionResult(42, 10.0, (SectorRisk(0), SectorRisk(0), sector, SectorRisk(0), SectorRisk(0)), 5)


def command():
    return BeltCommand(BeltMode.LEVELS, (0, 0, 200, 0, 0), 250)


def test_snapshot_matches_command_and_is_json_serializable():
    event = hazard_snapshot(result(), command(), True, 10.2)
    assert event["frame_seq"] == 42 and event["result_age_ms"] == 200
    assert event["events"] == [{"id": "2:depth:-", "sector": 2, "level": 200,
                               "evidence": "depth", "motion": "unknown", "label": None, "track_id": None}]
    json.dumps(event, allow_nan=False)


@pytest.mark.parametrize("now,healthy,cmd", [
    (11.01, True, command()), (9, True, command()), (10, False, command()),
    (10, True, BeltCommand.off(5)), (10, True, BeltCommand.fault(5)),
])
def test_stale_unhealthy_and_control_cues_publish_no_hazards(now, healthy, cmd):
    assert hazard_snapshot(result(), cmd, healthy, now)["events"] == []


def test_release_tail_does_not_claim_old_object_is_present():
    r = result(SectorRisk(0.01, label="person", evidence="box_ttc", motion="approaching", track_id=7))
    event = hazard_snapshot(r, command(), True, 10)["events"][0]
    assert event["evidence"] == "unknown" and event["label"] is None
    assert event["level"] == 200


class Depth:
    def estimate(self, rgb):
        return np.full(rgb.shape[:2], 1.0)


class Tracker:
    def track(self, image):
        return [TrackedBox(1, "person", .9, 80, 0, 200, 200),
                TrackedBox(2, "chair", .9, 90, 30, 160, 100)]


def test_largest_box_does_not_label_unrelated_depth_and_ttc_uses_winner():
    p = VisionPerception(PerceptionConfig(), Depth(), Tracker())
    p._ttc.update = lambda *args, **kwargs: None
    frame = Frame(1, np.zeros((240, 384, 3), dtype=np.uint8), 10)
    sectors = p.process(frame).sectors
    assert all(s.label is None and s.evidence == "depth" for s in sectors)
    p._ttc.update = lambda tid, *args, **kwargs: .5 if tid == 2 else None
    sector = p.process(frame).sectors[1]
    assert sector.label == "chair" and sector.track_id == 2
    assert sector.evidence == "box_ttc" and sector.motion == "approaching"


def test_flow_crossing_and_approach_keep_their_winning_tracks():
    flow = FlowMotion(FlowConfig(), 5, 1, 3)
    crossing = TrackMotion(2, "person", (0, 0, 60, 100), "measured",
                           crossing_sectors=(2,), crossing_risk=.5)
    approach = TrackMotion(9, "chair", (155, 0, 210, 100), "measured", ttc_s=1, approach_risk=.8)
    sector = flow.sector_evidence([crossing], 384)[2]
    assert sector.evidence == "flow_crossing" and sector.motion == "crossing" and sector.track_id == 2
    sector = flow.sector_evidence([crossing, approach], 384)[2]
    assert sector.evidence == "flow_approach" and sector.motion == "approaching" and sector.track_id == 9


def test_status_references_command_result_not_newer_unapplied_result(monkeypatch):
    from types import SimpleNamespace
    from haptic_hub.app import Hub
    from haptic_hub.config import HubConfig
    from haptic_hub.belt.sim import SimBelt
    from haptic_hub.perception.sweep import SweepPerception
    monkeypatch.setattr("haptic_hub.app.time.monotonic", lambda: 10.2)
    hub = Hub(HubConfig(), SimpleNamespace(name="test"), SweepPerception(5), SimBelt(5))
    hub.command_result = result()
    hub.result = PerceptionResult(43, 10.1, (SectorRisk(0),) * 5, 5)
    hub.command = command()
    hub.reason = None
    status = hub.status()
    assert status["hazards"]["frame_seq"] == 42
    assert status["hazards"]["events"][0]["level"] == status["levels"][2]
    hub.reason = "alerts paused"
    assert hub.status()["hazards"]["events"] == []
