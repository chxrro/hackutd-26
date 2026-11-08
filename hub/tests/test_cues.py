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


# -- session ended (end_session) ----------------------------------------------


@pytest.mark.parametrize("motors", [3, 4, 5, 8])
def test_session_end_cue_is_bounded_and_never_lights_every_motor(motors):
    cue = cues.session_end_cue(motors)
    assert cues.duration(cue) <= 1.5
    assert cue[0] == (cues.CUE_STEP_S, (0,) * motors)  # leading gap, like the other cues
    for _, levels in cue:
        assert len(levels) == motors and not all(levels)


def test_session_end_cue_is_three_slow_fading_pulses_on_the_inner_pair():
    cue = cues.session_end_cue(5)
    pulses = [(s, lv) for s, lv in cue[1:] if any(lv)]
    assert [lv for _, lv in pulses] == [(0, 200, 0, 200, 0), (0, 155, 0, 155, 0), (0, 110, 0, 110, 0)]
    assert all(s == cues.SESSION_END_PULSE_S > cues.CUE_STEP_S for s, _ in pulses)  # slower than any sweep step
    assert cues.duration(cue) == pytest.approx(1.45)
    assert cues.play(cue, cues.duration(cue)) is None  # plays once


def test_session_end_cue_differs_from_every_other_cue():
    end = [(s, lv) for s, lv in cues.session_end_cue(5)[1:]]
    # Rhythm: the same pair pulses three times with gaps; the sweeps move ring to
    # ring with no gap, the reminder is one short pulse, the fault is all motors.
    lit = [lv for _, lv in end if any(lv)]
    assert len({tuple(i for i, x in enumerate(lv) if x) for lv in lit}) == 1 and len(lit) == 3
    assert all(not any(lv) for _, lv in end[1::2])  # a gap between pulses
    longest_other = max(s for s, lv in cues.pause_cue(5) + cues.resume_cue(5) if any(lv))
    assert min(s for s, lv in end if any(lv)) > max(longest_other, cues.REMINDER_PULSE_S)
    # never one motor at a time (belt test) and never the outer pair (reminder)
    assert all(sum(1 for x in lv if x) == 2 and lv[0] == lv[4] == 0 for lv in lit)
