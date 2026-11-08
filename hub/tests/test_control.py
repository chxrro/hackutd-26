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
from haptic_hub import cues
from haptic_hub.control import (
    ALERT_RANGES,
    BELT_TEST_LEVEL,
    BELT_TEST_MAX_S,
    PAUSED_REASON,
    RESUME_REASON,
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


TICK = 1 / 15  # the hub's belt loop rate


def _play(c, t0, seconds, cmd=LEVELS, health=OK, belt="connected"):
    """Run c.apply at the belt loop's rate; returns [(t - t0, command, reason)]."""
    out, t = [], t0
    while t < t0 + seconds - 1e-9:
        out.append((t - t0, *c.apply(cmd, health, belt, t)))
        t += TICK
    return out


def _quiet_after_cue(c, t0):
    """Plays the pause cue to its end; returns the time it ended."""
    end = t0 + cues.duration(cues.pause_cue(5))
    _play(c, t0, end - t0 + TICK / 2)
    return end


def test_pause_plays_cue_then_stays_quiet_with_explicit_reason_and_resume_restores():
    c = Control(HubConfig())
    assert ack(c, {"type": "control", "id": 1, "action": "pause"})["settings"]["paused"] is True
    ticks = _play(c, 0.0, 1.0)
    assert {reason for _, _, reason in ticks} == {PAUSED_REASON} and PAUSED_REASON == "alerts paused"
    felt = [cmd.levels for _, cmd, _ in ticks if cmd.mode == BeltMode.LEVELS]
    assert felt and all(lv in (cues.CUE_LEVEL, 0) for levels in felt for lv in levels)
    assert all(cmd.ttl_ms == LEVELS.ttl_ms and cmd.mode != BeltMode.FAULT for _, cmd, _ in ticks)
    t, cmd, _ = ticks[-1]
    assert t > cues.duration(cues.pause_cue(5)) and cmd == BeltCommand.off(5, LEVELS.ttl_ms)  # quiet after the cue
    ack(c, {"type": "control", "id": 2, "action": "resume"})
    after = _play(c, 1.0, 1.0)
    assert after[0][2] == RESUME_REASON == "resuming alerts"
    assert after[-1][1:] == (LEVELS, None)  # normal levels again


def test_pause_cue_is_outside_in_and_resume_cue_is_inside_out():
    c = Control(HubConfig())
    ack(c, {"type": "control", "id": 1, "action": "pause"})
    on = [cmd.levels for _, cmd, _ in _play(c, 0.0, 0.8) if any(cmd.levels)]
    assert _rings_in_order(on) == [(0, 4), (1, 3), (2,)]
    ack(c, {"type": "control", "id": 2, "action": "resume"})
    ticks = _play(c, 10.0, 0.8, cmd=BeltCommand.off(5))
    on = [cmd.levels for _, cmd, reason in ticks if reason == RESUME_REASON and any(cmd.levels)]
    assert _rings_in_order(on) == [(2,), (1, 3), (0, 4)]
    resume_ticks = [t for t, _, reason in ticks if reason == RESUME_REASON]
    assert max(resume_ticks) < cues.duration(cues.resume_cue(5)) <= 0.6 + 1e-9  # bounded
    assert ticks[-1][1:] == (BeltCommand.off(5), None)


def _rings_in_order(levels_seq):
    seen = []
    for levels in levels_seq:
        ring = tuple(i for i, lv in enumerate(levels) if lv)
        if not seen or seen[-1] != ring:
            seen.append(ring)
    return seen


def test_pause_reminder_every_15_s_low_and_on_outer_motors():
    c = Control(HubConfig())
    ack(c, {"type": "control", "id": 1, "action": "pause"})
    quiet_from = _quiet_after_cue(c, 0.0)
    ticks = _play(c, quiet_from, 46.0)
    pulses = [t for t, cmd, _ in ticks if any(cmd.levels)]
    assert all(cmd.levels in ((0,) * 5, (110, 0, 0, 0, 110)) for _, cmd, _ in ticks)
    assert all(reason == PAUSED_REASON for _, _, reason in ticks)
    starts = [t for i, t in enumerate(pulses) if i == 0 or t - pulses[i - 1] > 1.0]
    assert [round(t) for t in starts] == [15, 30, 45]
    assert all(t < 15 + cues.REMINDER_PULSE_S for t in pulses if t < 20)  # each pulse is short


def test_pause_reminder_is_configurable_and_can_be_off():
    cfg = HubConfig()
    cfg.haptics.pause_reminder_s = 0
    c = Control(cfg)
    ack(c, {"type": "control", "id": 1, "action": "pause"})
    quiet_from = _quiet_after_cue(c, 0.0)
    assert not any(any(cmd.levels) for _, cmd, _ in _play(c, quiet_from, 60.0))
    cfg.haptics.pause_reminder_s = 4.0
    pulses = [t for t, cmd, _ in _play(c, quiet_from, 9.0) if any(cmd.levels)]
    assert pulses and 4.0 <= pulses[0] < 4.0 + 0.2
    for bad in (-1.0, 0.5, float("nan")):
        cfg.haptics.pause_reminder_s = bad
        with pytest.raises(ValueError):
            Control(cfg)


def test_faults_win_over_pause_on_the_belt_and_in_status():
    c = Control(HubConfig())
    ack(c, {"type": "control", "id": 1, "action": "pause"})
    quiet_from = _quiet_after_cue(c, 0.0)
    stale = Health(False, "no camera frames for 1.2 s")
    cmd, reason = c.apply(BeltCommand.fault(5), stale, "connected", quiet_from + 1)
    assert reason == "no camera frames for 1.2 s" and cmd == BeltCommand.fault(5)
    cmd, reason = c.apply(BeltCommand.fault(5), Health(False, "perception failing: RuntimeError"), "connected", quiet_from + 2)
    assert reason.startswith("perception failing") and cmd.mode == BeltMode.FAULT
    # Even if a caller hands over a non-fault command, an unhealthy verdict sends the fault pattern.
    assert c.apply(LEVELS, stale, "connected", quiet_from + 3)[0].mode == BeltMode.FAULT
    assert c.apply(LEVELS, OK, "disconnected", quiet_from + 4)[1] == "belt disconnected"
    assert c.apply(LEVELS, OK, "connecting", quiet_from + 5)[1] == "belt connecting"
    assert c.apply(LEVELS, OK, "simulated", quiet_from + 6)[1] == PAUSED_REASON
    assert c.settings()["paused"] is True  # a fault doesn't unpause


def test_fault_interrupts_pause_cue_and_cue_replays_when_fault_clears():
    c = Control(HubConfig())
    ack(c, {"type": "control", "id": 1, "action": "pause"})
    _play(c, 0.0, 0.2)  # cue under way
    ticks = _play(c, 0.2, 3.0, cmd=BeltCommand.fault(5), health=Health(False, "no camera frames for 1.1 s"))
    assert all(cmd.mode == BeltMode.FAULT for _, cmd, _ in ticks)
    replay = [cmd.levels for _, cmd, _ in _play(c, 3.2, 0.8) if any(cmd.levels)]
    assert _rings_in_order(replay) == [(0, 4), (1, 3), (2,)]  # "still paused", not "fault over, all clear"
    # same after the belt link drops and comes back
    _play(c, 4.0, 2.0, belt="disconnected")
    replay = [cmd.levels for _, cmd, _ in _play(c, 6.0, 0.8) if any(cmd.levels)]
    assert _rings_in_order(replay) == [(0, 4), (1, 3), (2,)]


def test_resume_during_fault_keeps_fault_then_plays_resume_cue():
    c = Control(HubConfig())
    ack(c, {"type": "control", "id": 1, "action": "pause"})
    _quiet_after_cue(c, 0.0)
    ack(c, {"type": "control", "id": 2, "action": "resume"})
    stale = Health(False, "no camera frames for 1.5 s")
    ticks = _play(c, 5.0, 1.0, cmd=BeltCommand.fault(5), health=stale)
    assert all(cmd.mode == BeltMode.FAULT and reason == stale.reason for _, cmd, reason in ticks)
    ticks = _play(c, 6.0, 1.0)
    assert ticks[0][2] == RESUME_REASON
    assert ticks[-1][1:] == (LEVELS, None)


def test_fault_mid_resume_cue_wins():
    c = Control(HubConfig())
    ack(c, {"type": "control", "id": 1, "action": "pause"})
    _quiet_after_cue(c, 0.0)
    ack(c, {"type": "control", "id": 2, "action": "resume"})
    _play(c, 5.0, 0.3)
    cmd, reason = c.apply(BeltCommand.fault(5), Health(False, "perception failing: x"), "connected", 5.3)
    assert cmd.mode == BeltMode.FAULT and reason == "perception failing: x"


def test_pause_and_resume_are_idempotent_and_do_not_replay_cues():
    c = Control(HubConfig())
    ack(c, {"type": "control", "id": 1, "action": "resume"})  # not paused: no resume cue
    assert c.apply(LEVELS, OK, "connected", 0.0) == (LEVELS, None)
    ack(c, {"type": "control", "id": 2, "action": "pause"})
    quiet_from = _quiet_after_cue(c, 1.0)
    ack(c, {"type": "control", "id": 3, "action": "pause"})  # already paused: no new cue
    assert not any(any(cmd.levels) for _, cmd, _ in _play(c, quiet_from, 1.0))


def test_pause_during_resume_cue_cancels_it():
    c = Control(HubConfig())
    ack(c, {"type": "control", "id": 1, "action": "pause"})
    _quiet_after_cue(c, 0.0)
    ack(c, {"type": "control", "id": 2, "action": "resume"})
    _play(c, 5.0, 0.3)
    ack(c, {"type": "control", "id": 3, "action": "pause"})
    on = [cmd.levels for _, cmd, reason in _play(c, 5.3, 0.8) if any(cmd.levels)]
    assert _rings_in_order(on) == [(0, 4), (1, 3), (2,)]


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


# -- belt not acknowledging (Belt.fault) ---------------------------------------

NOT_ACK = "belt not acknowledging"


def test_belt_not_acknowledging_is_unhealthy_and_sends_the_fault_pattern():
    c = Control(HubConfig())
    cmd, reason = c.apply(LEVELS, OK, "connected", 0.0, NOT_ACK)
    assert reason == NOT_ACK and cmd.mode == BeltMode.FAULT  # if a write gets through, it's the fault pattern
    assert c.apply(LEVELS, OK, "connected", 0.1) == (LEVELS, None)  # recovers as soon as the belt does


def test_belt_link_down_is_unhealthy_even_when_not_paused():
    c = Control(HubConfig())
    assert c.apply(LEVELS, OK, "disconnected", 0.0)[1] == "belt disconnected"
    assert c.apply(LEVELS, OK, "connecting", 0.1)[1] == "belt connecting"
    assert c.apply(LEVELS, OK, "connected", 0.2) == (LEVELS, None)


@pytest.mark.parametrize("paused", [False, True])
def test_reason_precedence_input_fault_then_link_then_not_acknowledging_then_pause(paused):
    c = Control(HubConfig())
    if paused:
        ack(c, {"type": "control", "id": 1, "action": "pause"})
    stale = Health(False, "no camera frames for 1.2 s")
    fault = BeltCommand.fault(5)
    assert c.apply(fault, stale, "disconnected", 0.0, NOT_ACK) == (fault, stale.reason)
    assert c.apply(fault, stale, "connected", 0.1, NOT_ACK) == (fault, stale.reason)
    # a link that is down outranks (and makes moot) "not acknowledging"
    assert c.apply(LEVELS, OK, "disconnected", 0.2, NOT_ACK)[1] == "belt disconnected"
    cmd, reason = c.apply(LEVELS, OK, "connected", 0.3, NOT_ACK)
    assert reason == NOT_ACK and cmd.mode == BeltMode.FAULT
    assert c.settings()["paused"] is paused  # a fault doesn't unpause


def test_pause_cue_replays_after_belt_acknowledges_again():
    c = Control(HubConfig())
    ack(c, {"type": "control", "id": 1, "action": "pause"})
    quiet_from = _quiet_after_cue(c, 0.0)
    ticks = [(t, *c.apply(LEVELS, OK, "connected", quiet_from + t, NOT_ACK)) for t in (0.0, 0.5, 1.0)]
    assert all(cmd.mode == BeltMode.FAULT and reason == NOT_ACK for _, cmd, reason in ticks)
    replay = [cmd.levels for _, cmd, _ in _play(c, quiet_from + 1.1, 0.8) if any(cmd.levels)]
    assert _rings_in_order(replay) == [(0, 4), (1, 3), (2,)]  # "still paused", not "all clear"


def test_resume_cue_waits_for_the_belt_to_acknowledge():
    c = Control(HubConfig())
    ack(c, {"type": "control", "id": 1, "action": "pause"})
    _quiet_after_cue(c, 0.0)
    ack(c, {"type": "control", "id": 2, "action": "resume"})
    _play(c, 5.0, 0.2)  # cue under way ...
    for t in (5.2, 5.5, 5.8):  # ... then the belt stops acknowledging
        cmd, reason = c.apply(LEVELS, OK, "connected", t, NOT_ACK)
        assert cmd.mode == BeltMode.FAULT and reason == NOT_ACK
    ticks = _play(c, 6.0, 1.0)
    assert ticks[0][2] == RESUME_REASON  # the cue plays from the start once the belt hears us
    assert ticks[-1][1:] == (LEVELS, None)


def test_belt_test_refused_and_stopped_when_belt_not_acknowledging():
    clock = FakeClock()
    c = Control(HubConfig(), clock=clock)
    c.apply(LEVELS, OK, "connected", clock.t, NOT_ACK)
    assert ack(c, {"type": "control", "id": 1, "action": "belt_test"})["error"] == NOT_ACK
    c.apply(LEVELS, OK, "connected", clock.t)
    assert ack(c, {"type": "control", "id": 2, "action": "belt_test"})["ok"]
    assert c.apply(LEVELS, OK, "connected", clock.t + 0.1)[1] == "belt test"
    # the belt stops hearing us mid-test: the test ends rather than hiding the fault
    assert c.apply(LEVELS, OK, "connected", clock.t + 0.2, NOT_ACK)[1] == NOT_ACK
    assert c.apply(LEVELS, OK, "connected", clock.t + 0.3) == (LEVELS, None)
    assert not c.belt_test_running(clock.t + 0.3)


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


class RecordingBelt(SimBelt):
    """Keeps every command with the hub's status reason at the time it was sent."""

    def __init__(self, motors):
        super().__init__(motors)
        self.history = []
        self.hub = None

    async def send(self, cmd):
        await super().send(cmd)
        self.history.append((self.last, self.hub.reason if self.hub else None))


def _cue_rings(history, reason):
    lit = [tuple(i for i, lv in enumerate(c.levels) if lv) for c, r in history if r == reason and any(c.levels)]
    return [ring for i, ring in enumerate(lit) if i == 0 or lit[i - 1] != ring]


def test_hub_pause_plays_cue_then_belt_off_and_status_says_why():
    belt = RecordingBelt(5)
    hub = Hub(HubConfig(), FakeSource(seconds=30), SweepPerception(5), belt)
    belt.hub = hub

    async def before(h):
        return belt.last.mode, h.status()

    async def pause(h):
        await h.control.handle({"type": "control", "id": 1, "action": "pause"}, paired=True)
        return len(belt.history)

    async def after(h):
        return belt.last.mode, h.status()

    (mode0, s0), paused_at, (mode1, s1) = _run_hub(hub, [before, pause, after], 0.8)
    assert mode0 == BeltMode.LEVELS and s0["healthy"] and s0["paused"] is False
    assert _cue_rings(belt.history[paused_at:], PAUSED_REASON) == [(0, 4), (1, 3), (2,)]
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
    assert mode == BeltMode.FAULT and s["mode"] == "fault"  # a real fault wins over pause on the belt too


class UnconfirmedBelt(SimBelt):
    """A belt whose link is up but which isn't acknowledging writes (Belt.fault)."""

    state = "connected"
    fault = None


def test_hub_status_reports_belt_not_acknowledging_and_recovers():
    belt = UnconfirmedBelt(5)
    hub = Hub(HubConfig(), FakeSource(seconds=30), SweepPerception(5), belt)

    async def healthy(h):
        return h.status()

    async def lose(h):
        belt.fault = "belt not acknowledging"
        await asyncio.sleep(0.2)
        return belt.last.mode, h.status()

    async def recover(h):
        belt.fault = None
        await asyncio.sleep(0.2)
        return h.status()

    s0, (mode, s1), s2 = _run_hub(hub, [healthy, lose, recover], 0.6)
    assert s0["healthy"] is True
    assert s1["healthy"] is False and s1["reason"] == "belt not acknowledging" and s1["belt"] == "connected"
    assert mode == BeltMode.FAULT and s1["mode"] == "fault"
    assert s2["healthy"] is True and s2["reason"] is None


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


def test_hub_resume_plays_cue_then_normal_levels_return():
    belt = RecordingBelt(5)
    hub = Hub(HubConfig(), FakeSource(seconds=30), SweepPerception(5), belt)
    belt.hub = hub

    async def pause(h):
        await h.control.handle({"type": "control", "id": 1, "action": "pause"}, paired=True)

    async def resume(h):
        await h.control.handle({"type": "control", "id": 2, "action": "resume"}, paired=True)
        return len(belt.history)

    async def after(h):
        return belt.last.mode, h.status()

    _, resumed_at, (mode, s) = _run_hub(hub, [pause, resume, after], 0.8)
    assert _cue_rings(belt.history[resumed_at:], RESUME_REASON) == [(2,), (1, 3), (0, 4)]
    assert mode == BeltMode.LEVELS and s["healthy"] and s["reason"] is None and s["paused"] is False
