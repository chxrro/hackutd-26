from haptic_hub.config import HapticsConfig, SafetyConfig
from haptic_hub.haptics import HapticPolicy
from haptic_hub.protocol import BeltMode
from haptic_hub.safety import SafetyMonitor
from haptic_hub.types import Health, PerceptionResult, SectorRisk

OK = Health(True)


def result(seq, risks):
    return PerceptionResult(seq, 0.0, tuple(SectorRisk(r) for r in risks), 10.0)


def test_unhealthy_means_fault_never_silence():
    policy = HapticPolicy(HapticsConfig(), motors=5)
    assert policy.command(result(1, [0] * 5), Health(False, "no frames")).mode == BeltMode.FAULT
    assert policy.command(None, OK).mode == BeltMode.FAULT


def test_no_risk_is_off_and_risk_is_levels():
    policy = HapticPolicy(HapticsConfig(), motors=5)
    assert policy.command(result(1, [0] * 5), OK).mode == BeltMode.OFF
    cmd = policy.command(result(2, [0, 0, 1.0, 0, 0]), OK)
    assert cmd.mode == BeltMode.LEVELS
    assert cmd.levels[2] == 255 and cmd.levels[0] == 0


def test_rising_risk_is_instant_falling_risk_fades():
    cfg = HapticsConfig(release=0.5, deadband=0.0, min_level=0)
    policy = HapticPolicy(cfg, motors=1)
    assert policy.command(result(1, [1.0]), OK).levels == (255,)
    faded = policy.command(result(2, [0.0]), OK).levels[0]
    assert 0 < faded < 255
    # Same result again (belt heartbeat between perception results): no extra decay.
    assert policy.command(result(2, [0.0]), OK).levels[0] == faded


def test_deadband_and_min_level():
    cfg = HapticsConfig(deadband=0.2, min_level=90)
    policy = HapticPolicy(cfg, motors=2)
    levels = policy.command(result(1, [0.1, 0.21]), OK).levels
    assert levels[0] == 0 and levels[1] >= 90


def test_safety_monitor_states():
    mon = SafetyMonitor(SafetyConfig(frame_stale_s=1.0, result_stale_s=1.5))
    assert not mon.check(0.0).ok  # nothing yet
    mon.frame_received(10.0)
    assert mon.check(10.1).reason == "perception warming up"
    mon.perception_ok(10.2, frame_received_at=10.0)
    assert mon.check(10.3).ok
    assert "no camera frames" in mon.check(11.5).reason
    mon.frame_received(11.5)
    mon.perception_failed("RuntimeError")
    assert "perception failing" in mon.check(11.6).reason
    mon.perception_ok(11.7, frame_received_at=11.5)
    assert mon.check(11.8).ok


def test_slow_result_faults_even_though_results_keep_coming():
    """A result 1.4 s after its frame isn't 'stalled' (< result_stale_s), but the
    belt would be playing a scene that old."""
    mon = SafetyMonitor(SafetyConfig(frame_stale_s=1.0, result_stale_s=1.5, result_max_age_s=1.0))
    mon.frame_received(11.4)  # newer frames keep arriving
    mon.perception_ok(11.4, frame_received_at=10.0)
    health = mon.check(11.45)
    assert not health.ok and health.reason == "perception result is 1.4 s old"


def test_old_result_is_not_replayed_after_a_frame_gap():
    mon = SafetyMonitor(SafetyConfig(frame_stale_s=1.0, result_stale_s=1.5, result_max_age_s=1.0))
    mon.frame_received(10.0)
    mon.perception_ok(10.05, frame_received_at=10.0)
    assert "no camera frames" in mon.check(11.1).reason
    mon.frame_received(11.2)  # frames resume; the only result is from before the gap
    assert "1.2 s old" in mon.check(11.2).reason
    mon.perception_ok(11.25, frame_received_at=11.2)
    assert mon.check(11.3).ok
