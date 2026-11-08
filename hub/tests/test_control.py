"""Control layer (docs/protocol.md "Control"): settings, pause, belt test, alert
range, classifier swap, pairing over the phone WebSocket, and the status
additions. No models, no hardware, no network beyond loopback."""

import asyncio
import json
import logging
import time

import numpy as np
import pytest
from websockets.asyncio.client import connect

from haptic_hub.app import Hub
from haptic_hub.belt.sim import SimBelt
from haptic_hub.config import HubConfig, PerceptionConfig
from haptic_hub.control import (
    ALERT_RANGES,
    BELT_TEST_LEVEL,
    BELT_TEST_MAX_S,
    PAUSED_REASON,
    Control,
    redact,
    sector_kinds,
)
from haptic_hub.perception.classify import ClassifiedBox, ClassifiedPerception
from haptic_hub.perception.models import TrackedBox
from haptic_hub.perception.sweep import SweepPerception
from haptic_hub.protocol import BeltCommand, BeltMode, FramePacket, encode_frame_packet
from haptic_hub.sources.glasses import GlassesServer
from haptic_hub.types import Frame, Health, PerceptionResult, SectorRisk
from test_end_to_end import FakeSource, _connect_with_retry, _free_port

ACK_KEYS = {"type", "id", "ok", "error", "settings"}
SETTINGS_KEYS = {"classifier", "alert_range", "record", "paused"}
OK = Health(True)
LEVELS = BeltCommand(BeltMode.LEVELS, (0, 0, 200, 0, 0))


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class FakeInner:
    name = "fake-vision"

    def process(self, frame):
        sectors = tuple(SectorRisk(0.0) for _ in range(5))
        return PerceptionResult(frame.seq, frame.received_at, sectors, 1.0, {"infer_size": (100, 100), "boxes": []})


class FakeCloud:
    name = "nim"

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True

    def submit(self, *a):
        pass

    def labels(self, now):
        return {}

    def stale_reason(self, now):
        return None

    def status(self, now):
        return {"backend": "nim", "model": "m", "rtt_ms": None, "last_ok_age_s": None, "error": None}


def run(coro):
    return asyncio.run(coro)


def ack(control, msg, paired=True):
    return run(control.handle(msg, paired))


def check_ack_shape(a, rid):
    assert set(a) == ACK_KEYS
    assert a["type"] == "ack" and a["id"] == rid
    assert set(a["settings"]) == SETTINGS_KEYS
    assert (a["error"] is None) == a["ok"]


# -- settings and acks -----------------------------------------------------


def test_defaults_and_ack_shape():
    c = Control(HubConfig())
    assert c.settings() == {"classifier": "local", "alert_range": "normal", "record": False, "paused": False}
    a = ack(c, {"type": "control", "id": 12, "action": "get"})
    check_ack_shape(a, 12)
    assert a["ok"] and a["settings"] == c.settings()
    assert json.loads(json.dumps(a)) == a  # JSON-safe


def test_control_code_is_16_lowercase_hex_and_random():
    codes = {Control(HubConfig()).code for _ in range(20)}
    assert len(codes) == 20
    for code in codes:
        assert len(code) == 16 and all(ch in "0123456789abcdef" for ch in code)


def test_not_paired_changes_nothing():
    c = Control(HubConfig())
    for msg in (
        {"type": "control", "id": 1, "action": "pause"},
        {"type": "control", "id": 2, "action": "set", "settings": {"alert_range": "far"}},
    ):
        a = ack(c, msg, paired=False)
        check_ack_shape(a, msg["id"])
        assert a["ok"] is False and a["error"] == "not paired"
    assert c.settings() == Control(HubConfig()).settings()


@pytest.mark.parametrize(
    "msg, error",
    [
        ({"action": "explode"}, "unknown action"),
        ({}, "unknown action"),
        ({"action": "set", "settings": {}}, "settings must be a non-empty object"),
        ({"action": "set", "settings": "far"}, "settings must be a non-empty object"),
        ({"action": "set", "settings": {"alert_range": "far", "volume": 3}}, "unknown setting: volume"),
        ({"action": "set", "settings": {"alert_range": "very far"}}, "alert_range must be one of near, normal, far"),
        ({"action": "set", "settings": {"classifier": "gpt"}}, "classifier must be 'local' or 'nim'"),
        ({"action": "set", "settings": {"record": 1}}, "record must be true or false"),
        ({"action": "set", "settings": {"record": True, "alert_range": "far"}}, "recording not available"),
        ({"action": "set", "settings": {"classifier": "nim", "alert_range": "far"}}, "classifier not available (this perception has no detector)"),
    ],
)
def test_rejected_requests_change_nothing(msg, error):
    cfg = HubConfig()
    c = Control(cfg)
    a = ack(c, {"type": "control", "id": 7, **msg})
    check_ack_shape(a, 7)
    assert a["ok"] is False and a["error"] == error
    assert a["settings"] == {"classifier": "local", "alert_range": "normal", "record": False, "paused": False}
    assert (cfg.perception.far_m, cfg.perception.ttc_far_s) == (2.5, 3.0)


def test_record_false_is_a_no_op():
    c = Control(HubConfig())
    a = ack(c, {"type": "control", "id": 1, "action": "set", "settings": {"record": False}})
    assert a["ok"] and a["settings"]["record"] is False


# -- alert range -------------------------------------------------------------


def test_alert_range_presets_change_perception_thresholds():
    d = PerceptionConfig()
    assert (ALERT_RANGES["normal"].far_m, ALERT_RANGES["normal"].ttc_far_s) == (d.far_m, d.ttc_far_s)
    for name in ALERT_RANGES:  # every preset keeps a real ramp: alerts start before full risk
        assert ALERT_RANGES[name].far_m > d.near_m and ALERT_RANGES[name].ttc_far_s > d.ttc_near_s
    assert ALERT_RANGES["near"].far_m < d.far_m < ALERT_RANGES["far"].far_m

    cfg = HubConfig()
    c = Control(cfg)
    a = ack(c, {"type": "control", "id": 1, "action": "set", "settings": {"alert_range": "far"}})
    assert a["ok"] and a["settings"]["alert_range"] == "far"
    assert (cfg.perception.far_m, cfg.perception.ttc_far_s) == (ALERT_RANGES["far"].far_m, ALERT_RANGES["far"].ttc_far_s)
    assert (cfg.perception.near_m, cfg.perception.ttc_near_s) == (d.near_m, d.ttc_near_s)  # untouched
    ack(c, {"type": "control", "id": 2, "action": "set", "settings": {"alert_range": "normal"}})
    assert (cfg.perception.far_m, cfg.perception.ttc_far_s) == (d.far_m, d.ttc_far_s)


# -- pause / fault -----------------------------------------------------------


def test_pause_sends_off_with_explicit_reason_and_resume_restores():
    c = Control(HubConfig())
    assert ack(c, {"type": "control", "id": 1, "action": "pause"})["settings"]["paused"] is True
    cmd, reason = c.apply(LEVELS, OK, "connected", 0.0)
    assert cmd.mode == BeltMode.OFF and cmd.levels == (0,) * 5 and cmd.ttl_ms == LEVELS.ttl_ms
    assert reason == PAUSED_REASON == "alerts paused"
    ack(c, {"type": "control", "id": 2, "action": "resume"})
    assert c.apply(LEVELS, OK, "connected", 0.0) == (LEVELS, None)


def test_faults_win_over_pause_in_status():
    c = Control(HubConfig())
    ack(c, {"type": "control", "id": 1, "action": "pause"})
    cmd, reason = c.apply(BeltCommand.fault(5), Health(False, "no camera frames for 1.2 s"), "connected", 0.0)
    assert reason == "no camera frames for 1.2 s" and cmd.mode == BeltMode.OFF
    assert c.apply(LEVELS, OK, "disconnected", 0.0)[1] == "belt disconnected"
    assert c.apply(LEVELS, OK, "connecting", 0.0)[1] == "belt connecting"
    assert c.apply(LEVELS, OK, "simulated", 0.0)[1] == PAUSED_REASON


# -- belt test -----------------------------------------------------------------


def test_belt_test_sweeps_left_to_right_and_is_bounded():
    clock = FakeClock()
    c = Control(HubConfig(), clock=clock)
    assert ack(c, {"type": "control", "id": 1, "action": "belt_test"})["ok"]
    assert ack(c, {"type": "control", "id": 2, "action": "belt_test"})["error"] == "belt test already running"
    start, seen, t = clock.t, [], clock.t
    while True:
        cmd, reason = c.apply(BeltCommand.fault(5), Health(False, "waiting for camera frames"), "simulated", t)
        if reason != "belt test":
            break
        assert cmd.mode == BeltMode.LEVELS and sorted(cmd.levels) == [0, 0, 0, 0, BELT_TEST_LEVEL]
        hot = cmd.levels.index(BELT_TEST_LEVEL)
        if not seen or seen[-1] != hot:
            seen.append(hot)
        t += 1 / 15
    assert seen == [0, 1, 2, 3, 4]
    assert t - start <= BELT_TEST_MAX_S
    assert reason == "waiting for camera frames" and cmd.mode == BeltMode.FAULT  # back to normal
    assert c.apply(LEVELS, OK, "simulated", t + 10) == (LEVELS, None)


def test_belt_test_duration_is_capped_for_any_motor_count():
    assert Control.belt_test_duration(5) <= BELT_TEST_MAX_S
    assert Control.belt_test_duration(8) == BELT_TEST_MAX_S


def test_belt_test_refused_while_paused_or_belt_down():
    c = Control(HubConfig())
    ack(c, {"type": "control", "id": 1, "action": "pause"})
    a = ack(c, {"type": "control", "id": 2, "action": "belt_test"})
    assert a["ok"] is False and a["error"] == "refused while paused"
    ack(c, {"type": "control", "id": 3, "action": "resume"})
    c.apply(LEVELS, OK, "disconnected", 0.0)
    assert ack(c, {"type": "control", "id": 4, "action": "belt_test"})["error"] == "belt not connected"


def test_pause_cancels_a_running_belt_test():
    clock = FakeClock()
    c = Control(HubConfig(), clock=clock)
    ack(c, {"type": "control", "id": 1, "action": "belt_test"})
    ack(c, {"type": "control", "id": 2, "action": "pause"})
    assert c.apply(LEVELS, OK, "simulated", clock.t + 0.1)[1] == PAUSED_REASON


# -- classifier ---------------------------------------------------------------


def test_classifier_nim_needs_a_configured_endpoint():
    c = Control(HubConfig(), ClassifiedPerception(FakeInner()))
    a = ack(c, {"type": "control", "id": 1, "action": "set", "settings": {"classifier": "nim"}})
    assert a["ok"] is False and a["error"] == "cloud classifier not configured"
    assert c.cloud_available is False
    assert ack(c, {"type": "control", "id": 2, "action": "set", "settings": {"classifier": "local"}})["ok"]


def test_classifier_swaps_cloud_backend_at_runtime():
    perception = ClassifiedPerception(FakeInner())
    built = []

    def factory():
        built.append(FakeCloud())
        return built[-1]

    c = Control(HubConfig(), perception, factory)
    a = ack(c, {"type": "control", "id": 1, "action": "set", "settings": {"classifier": "nim", "alert_range": "near"}})
    assert a["ok"] and a["settings"]["classifier"] == "nim" and a["settings"]["alert_range"] == "near"
    assert perception.cloud is built[0]
    perception.process(Frame(1, np.zeros((10, 10, 3), np.uint8), 0.0))  # still works with the new backend
    a = ack(c, {"type": "control", "id": 2, "action": "set", "settings": {"classifier": "local"}})
    assert a["settings"]["classifier"] == "local" and perception.cloud is None and built[0].closed
    ack(c, {"type": "control", "id": 3, "action": "set", "settings": {"classifier": "nim"}})
    assert len(built) == 2 and perception.cloud is built[1]  # fresh backend: no stale "no reply since"


def test_classifier_switch_failure_changes_nothing():
    def factory():
        raise RuntimeError("can't reach http://localhost:8000/v1/models")

    perception = ClassifiedPerception(FakeInner())
    c = Control(HubConfig(), perception, factory)
    a = ack(c, {"type": "control", "id": 1, "action": "set", "settings": {"classifier": "nim", "alert_range": "far"}})
    assert a["ok"] is False and a["error"].startswith("cloud classifier unavailable:")
    assert a["settings"]["classifier"] == "local" and a["settings"]["alert_range"] == "normal"
    assert perception.cloud is None


def test_started_with_nim_reports_nim():
    c = Control(HubConfig(), ClassifiedPerception(FakeInner(), FakeCloud()))
    assert c.settings()["classifier"] == "nim" and c.cloud_available


# -- status additions -----------------------------------------------------------


def _classified_result():
    person = TrackedBox(1, "person", 0.9, 0, 0, 30, 90)  # covers sectors 0-1 (width 100, 5 sectors)
    chair = TrackedBox(2, "chair", 0.8, 50, 40, 70, 60)  # sector 2-3
    tiny = TrackedBox(3, "cup", 0.8, 90, 0, 99, 2)  # below min_box_height
    classified = [ClassifiedBox(person, "person", "local"), ClassifiedBox(chair, "object", "cloud"), ClassifiedBox(tiny, "object", "local")]
    debug = {"infer_size": (100, 100), "classified": classified}
    return PerceptionResult(1, 0.0, tuple(SectorRisk(0.0) for _ in range(5)), 1.0, debug)


def test_sector_kinds_from_classified_boxes():
    assert sector_kinds(_classified_result(), 5, 0.08) == ["person", "person", "object", "object", None]
    assert sector_kinds(None, 5) == [None] * 5
    no_labels = PerceptionResult(1, 0.0, tuple(SectorRisk(0.0) for _ in range(5)), 1.0, {})
    assert sector_kinds(no_labels, 5) == [None] * 5


def test_status_fields_shape_and_stale_results_give_no_kinds():
    c = Control(HubConfig())
    fields = c.status_fields(_classified_result(), OK, 5)
    assert set(fields) == {"paused", "settings", "sectors"}
    assert fields["sectors"][0] == {"kind": "person"} and len(fields["sectors"]) == 5
    stale = c.status_fields(_classified_result(), Health(False, "no camera frames for 2.0 s"), 5)
    assert stale["sectors"] == [{"kind": None}] * 5  # an old result says nothing about now


def test_redact_hides_the_code():
    msg = {"type": "hello", "client": "ios", "version": 1, "control_code": "3f9c0a1b2d4e5f60"}
    assert "3f9c0a1b2d4e5f60" not in str(redact(msg)) and redact(msg)["client"] == "ios"


# -- the hub loop --------------------------------------------------------------


def _run_hub(hub, steps, between):
    async def scenario():
        task = asyncio.create_task(hub.run())
        out = []
        for step in steps:
            await asyncio.sleep(between)
            out.append(await step(hub))
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return out

    return run(scenario())


def test_hub_pause_turns_belt_off_and_status_says_why():
    belt = SimBelt(5)
    hub = Hub(HubConfig(), FakeSource(seconds=30), SweepPerception(5), belt)

    async def before(h):
        return belt.last.mode, h.status()

    async def pause(h):
        await h.control.handle({"type": "control", "id": 1, "action": "pause"}, paired=True)
        return None

    async def after(h):
        return belt.last.mode, h.status()

    (mode0, s0), _, (mode1, s1) = _run_hub(hub, [before, pause, after], 0.6)
    assert mode0 == BeltMode.LEVELS and s0["healthy"] and s0["paused"] is False
    assert mode1 == BeltMode.OFF
    assert s1["mode"] == "off" and s1["healthy"] is False and s1["reason"] == "alerts paused" and s1["paused"] is True
    assert s1["settings"]["paused"] is True and len(s1["sectors"]) == 5
    for key in ("type", "mode", "healthy", "reason", "levels", "belt", "inference_ms", "frame_age_ms"):
        assert key in s1  # existing fields unchanged


def test_hub_fault_overrides_pause_when_frames_stop():
    cfg = HubConfig()
    cfg.safety.frame_stale_s = 0.3
    belt = SimBelt(5)
    hub = Hub(cfg, FakeSource(seconds=0.4), SweepPerception(5), belt)

    async def pause(h):
        await h.control.handle({"type": "control", "id": 1, "action": "pause"}, paired=True)

    async def later(h):
        return belt.last.mode, h.status()

    _, (mode, s) = _run_hub(hub, [pause, later], 0.5)
    assert s["reason"].startswith("no camera frames") and s["paused"] is True and s["healthy"] is False
    assert mode == BeltMode.OFF  # paused: the belt stays off, but the status reports the fault


def test_hub_belt_test_plays_then_returns_to_normal():
    belt = SimBelt(5)
    hub = Hub(HubConfig(), FakeSource(seconds=30), SweepPerception(5), belt)
    seen = []

    async def start(h):
        a = await h.control.handle({"type": "control", "id": 1, "action": "belt_test"}, paired=True)
        assert a["ok"], a
        started = time.monotonic()
        while time.monotonic() - started < 3.5:
            seen.append((time.monotonic() - started, h.status()["reason"]))
            await asyncio.sleep(0.05)
        return seen

    _run_hub(hub, [start], 0.5)
    test_times = [t for t, reason in seen if reason == "belt test"]
    assert test_times and max(test_times) <= BELT_TEST_MAX_S
    assert seen[-1][1] is None  # healthy again afterwards


# -- pairing over the phone WebSocket ------------------------------------------


async def _recv_ack(ws, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        msg = json.loads(await asyncio.wait_for(ws.recv(), end - time.monotonic()))
        if msg.get("type") == "ack":
            return msg
    raise TimeoutError("no ack")


def test_pairing_over_websocket_and_code_never_logged(caplog):
    caplog.set_level(logging.DEBUG)

    async def scenario():
        port = _free_port()
        server = GlassesServer("127.0.0.1", port)
        hub = Hub(HubConfig(), server, SweepPerception(5), SimBelt(5), status_sink=server)
        code = hub.control.code
        task = asyncio.create_task(hub.run())
        phone = await _connect_with_retry(f"ws://127.0.0.1:{port}")
        other = await connect(f"ws://127.0.0.1:{port}")
        async with phone, other:
            # no code: frames still work, control is refused
            await phone.send(json.dumps({"type": "hello", "client": "ios", "version": 1}))
            await phone.send(json.dumps({"type": "control", "id": 1, "action": "pause"}))
            a1 = await _recv_ack(phone)
            # wrong code
            await phone.send(json.dumps({"type": "hello", "client": "ios", "version": 1, "control_code": "0" * 16}))
            await phone.send(json.dumps({"type": "control", "id": 2, "action": "pause"}))
            a2 = await _recv_ack(phone)
            # right code
            await phone.send(json.dumps({"type": "hello", "client": "ios", "version": 1, "control_code": code}))
            await phone.send(json.dumps({"type": "control", "id": 3, "action": "pause"}))
            a3 = await _recv_ack(phone)
            await phone.send(json.dumps({"type": "control", "id": 4, "action": "set", "settings": {"alert_range": "far"}}))
            a4 = await _recv_ack(phone)
            # the other client is not paired by the phone's hello, and never sees the phone's acks
            await other.send(json.dumps({"type": "control", "id": 99, "action": "resume"}))
            a99 = await _recv_ack(other)
            await asyncio.sleep(0.4)
            other_msgs = []
            while True:
                try:
                    other_msgs.append(json.loads(await asyncio.wait_for(other.recv(), 0.05)))
                except TimeoutError:
                    break
            jpeg = cv2_jpeg()
            await phone.send(encode_frame_packet(FramePacket(1, 0, 8, 8, jpeg)))
            await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return code, server, hub, (a1, a2, a3, a4, a99), other_msgs

    code, server, hub, (a1, a2, a3, a4, a99), other_msgs = run(asyncio.wait_for(scenario(), 20))
    for a, rid in ((a1, 1), (a2, 2), (a3, 3), (a4, 4), (a99, 99)):
        check_ack_shape(a, rid)
    assert (a1["ok"], a1["error"]) == (False, "not paired")
    assert (a2["ok"], a2["error"]) == (False, "not paired")
    assert a3["ok"] and a3["settings"]["paused"] is True
    assert a4["ok"] and a4["settings"]["alert_range"] == "far"
    assert (a99["ok"], a99["error"]) == (False, "not paired")
    assert all(m["type"] == "status" for m in other_msgs) and other_msgs  # status broadcast, no foreign acks
    assert other_msgs[-1]["paused"] is True and other_msgs[-1]["reason"] is not None
    assert server.frames_received == 1
    hub_logs = "\n".join(r.getMessage() for r in caplog.records if not r.name.startswith("websockets.client"))
    assert "phone says hello" in hub_logs and "<redacted>" in hub_logs  # logged, without the code
    assert code not in hub_logs  # not by the hub, and not by websockets' frame logging either


def cv2_jpeg():
    import cv2

    ok, buf = cv2.imencode(".jpg", np.zeros((8, 8, 3), np.uint8))
    assert ok
    return buf.tobytes()
