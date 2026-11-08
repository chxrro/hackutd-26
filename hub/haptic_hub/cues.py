"""Short belt cues for control events (docs/protocol.md, "Belt cues").

The firmware has no cue command. Every cue is a timeline of ordinary ``levels``
commands that the hub's belt loop samples at its send rate, like the belt test
does. Each command keeps the normal TTL, so if the hub stops, the firmware's
own TTL failsafe still applies.

Shapes, chosen to differ from everything else the wearer can feel:

  obstacle   sustained, per sector, fades out
  fault      all motors at once, double pulse, every 2 s (firmware)
  belt test  one motor at a time, left to right, 0.4 s each
  pause      symmetric sweep from the outside in: outer pair, inner pair, centre
  resume     the reverse, from the centre out: centre, inner pair, outer pair
  reminder   while paused: one short low pulse on the outer pair, every 15 s

Never more than one ring of motors is on at once and never all motors at once,
so no cue looks like the fault pattern. None of this has been felt on a real
belt yet; step times and levels are starting points.
"""

from __future__ import annotations

import math

Step = tuple[float, tuple[int, ...]]  # (seconds, level per motor)

CUE_STEP_S = 0.15  # same length as one firmware fault pulse
CUE_LEVEL = 200
REMINDER_PULSE_S = 0.15
REMINDER_LEVEL = 110  # low, but above HapticsConfig.min_level (weakest reliable PWM)
MIN_REMINDER_S = 2.0  # faster than this would start to feel like the 2 s fault rhythm


def rings(motors: int) -> list[tuple[int, ...]]:
    """Motors grouped in symmetric pairs, outermost first: 5 -> [(0, 4), (1, 3), (2,)]."""
    return [tuple(sorted({i, motors - 1 - i})) for i in range((motors + 1) // 2)]


def _ring_levels(motors: int, ring: tuple[int, ...], level: int) -> tuple[int, ...]:
    return tuple(level if i in ring else 0 for i in range(motors))


def _sweep(motors: int, order: list[tuple[int, ...]]) -> tuple[Step, ...]:
    # A leading gap so the cue never runs straight on from an obstacle vibration.
    gap: Step = (CUE_STEP_S, (0,) * motors)
    return (gap, *((CUE_STEP_S, _ring_levels(motors, r, CUE_LEVEL)) for r in order))


def pause_cue(motors: int) -> tuple[Step, ...]:
    """Outside in: "closing". 0.6 s for 5 motors."""
    return _sweep(motors, rings(motors))


def resume_cue(motors: int) -> tuple[Step, ...]:
    """Inside out: "opening". 0.6 s for 5 motors."""
    return _sweep(motors, rings(motors)[::-1])


def duration(steps: tuple[Step, ...]) -> float:
    return sum(s for s, _ in steps)


def play(steps: tuple[Step, ...], elapsed: float) -> tuple[int, ...] | None:
    """Levels at `elapsed` seconds into the cue, or None once it has finished."""
    if elapsed < 0:
        return None
    t = 0.0
    for seconds, levels in steps:
        t += seconds
        if elapsed < t:
            return levels
    return None


def reminder_levels(motors: int) -> tuple[int, ...]:
    return _ring_levels(motors, rings(motors)[0], REMINDER_LEVEL)


def paused_levels(elapsed: float, motors: int, reminder_s: float) -> tuple[int, ...]:
    """What the belt plays `elapsed` seconds into a paused period: the pause cue,
    then a reminder pulse every `reminder_s` seconds after the cue ends (0 = never)."""
    cue = pause_cue(motors)
    if (levels := play(cue, elapsed)) is not None:
        return levels
    if reminder_s > 0:
        since = elapsed - duration(cue)
        k = math.floor(since / reminder_s)
        if k >= 1 and since - k * reminder_s < REMINDER_PULSE_S:
            return reminder_levels(motors)
    return (0,) * motors


def check_reminder_s(value: float) -> float:
    """0 (off) or at least MIN_REMINDER_S."""
    if value != 0 and not value >= MIN_REMINDER_S:  # also rejects NaN
        raise ValueError(f"pause reminder must be 0 (off) or at least {MIN_REMINDER_S:g} s, got {value}")
    return value
