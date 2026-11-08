"""Belt cues for pause / resume (haptic_hub/cues.py, docs/protocol.md "Belt cues").
Pure timing logic: no models, no hardware."""

import pytest

from haptic_hub import cues


def test_rings_pair_motors_symmetrically_outside_in():
    assert cues.rings(5) == [(0, 4), (1, 3), (2,)]
    assert cues.rings(4) == [(0, 3), (1, 2)]
    assert cues.rings(1) == [(0,)]


@pytest.mark.parametrize("motors", [3, 4, 5, 8])
def test_cues_never_light_every_motor_and_are_bounded(motors):
    for cue in (cues.pause_cue(motors), cues.resume_cue(motors)):
        assert cues.duration(cue) <= 1.0
        assert cue[0][1] == (0,) * motors  # leading gap: never runs on from an obstacle vibration
        for _, levels in cue:
            assert len(levels) == motors
            assert not all(levels)  # the fault pattern is all motors at once
            assert all(lv in (0, cues.CUE_LEVEL) for lv in levels)


def test_pause_and_resume_cues_are_mirror_sweeps():
    pause = [lv for _, lv in cues.pause_cue(5)][1:]
    resume = [lv for _, lv in cues.resume_cue(5)][1:]
    assert pause == resume[::-1] and pause != resume
    assert pause[0] == (200, 0, 0, 0, 200) and pause[-1] == (0, 0, 200, 0, 0)


def test_cues_differ_from_belt_test_sweep():
    # Belt test: exactly one motor at a time, left to right. Cues light symmetric pairs.
    for _, levels in cues.pause_cue(5) + cues.resume_cue(5):
        lit = [i for i, lv in enumerate(levels) if lv]
        assert lit == sorted({*lit, *(4 - i for i in lit)})  # symmetric about the centre


def test_play_samples_steps_and_ends():
    cue = cues.pause_cue(5)
    assert cues.play(cue, -0.01) is None
    assert cues.play(cue, 0.0) == (0,) * 5
    assert cues.play(cue, 0.16) == (200, 0, 0, 0, 200)
    assert cues.play(cue, 0.31) == (0, 200, 0, 200, 0)
    assert cues.play(cue, 0.46) == (0, 0, 200, 0, 0)
    assert cues.play(cue, cues.duration(cue)) is None


def test_paused_levels_reminder_timing():
    end = cues.duration(cues.pause_cue(5))
    reminder = (cues.REMINDER_LEVEL, 0, 0, 0, cues.REMINDER_LEVEL)
    assert cues.paused_levels(end + 0.01, 5, 15) == (0,) * 5
    assert cues.paused_levels(end + 14.99, 5, 15) == (0,) * 5
    assert cues.paused_levels(end + 15.0, 5, 15) == reminder
    assert cues.paused_levels(end + 15.0 + cues.REMINDER_PULSE_S, 5, 15) == (0,) * 5
    assert cues.paused_levels(end + 30.05, 5, 15) == reminder
    assert cues.paused_levels(end + 15.0, 5, 0) == (0,) * 5  # off
    assert 0 < cues.REMINDER_LEVEL < cues.CUE_LEVEL


@pytest.mark.parametrize("value, ok", [(0, True), (2, True), (15, True), (1.9, False), (-1, False), (float("nan"), False)])
def test_check_reminder_s(value, ok):
    if ok:
        assert cues.check_reminder_s(value) == value
    else:
        with pytest.raises(ValueError):
            cues.check_reminder_s(value)
