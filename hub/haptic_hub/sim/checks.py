"""What a scenario run records, and pass/fail checks over that record.

Checks look at what the *belt firmware* outputs (the thing the wearer would
feel), not at hub internals, so a bug anywhere in the chain shows up.
"""

from __future__ import annotations

import bisect
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from haptic_hub.sim.firmware import BeltSample
from haptic_hub.sim.world import Pose


@dataclass
class FrameSample:
    """Ground truth at the moment a frame was captured."""

    t: float
    seq: int
    pose: Pose
    sector_range_m: list[float | None]  # nearest obstacle per sector (independent of perception)
    clearance_m: float  # wearer body edge to nearest obstacle
    labeled_ranges: dict[str, float] = field(default_factory=dict)


@dataclass
class HubSample:
    t: float
    mode: str
    levels: tuple[int, ...]
    reason: str | None
    risks: tuple[float, ...] | None


@dataclass
class Timeline:
    belt: list[BeltSample] = field(default_factory=list)
    frames: list[FrameSample] = field(default_factory=list)
    hub: list[HubSample] = field(default_factory=list)
    statuses: list[tuple[float, dict[str, Any]]] = field(default_factory=list)
    collisions: list[tuple[float, str]] = field(default_factory=list)
    counters: dict[str, Any] = field(default_factory=dict)

    def belt_between(self, t0: float, t1: float) -> list[BeltSample]:
        ts = [s.t for s in self.belt]
        return self.belt[bisect.bisect_left(ts, t0) : bisect.bisect_right(ts, t1)]

    def frames_between(self, t0: float, t1: float) -> list[FrameSample]:
        return [f for f in self.frames if t0 <= f.t <= t1]


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


Check = Callable[[Timeline], CheckResult]


def _frac(samples: Sequence[Any], pred: Callable[[Any], bool]) -> float:
    return sum(1 for s in samples if pred(s)) / len(samples) if samples else float("nan")


def _sectors(sectors: int | Sequence[int]) -> tuple[int, ...]:
    return (sectors,) if isinstance(sectors, int) else tuple(sectors)


def _names(sectors: tuple[int, ...]) -> str:
    return "+".join(str(s) for s in sectors)


def motors_on(sectors: int | Sequence[int], t0: float, t1: float, min_frac: float = 0.9) -> Check:
    """Every listed motor vibrates (alert, not the fault pattern) for most of [t0, t1]."""
    ss = _sectors(sectors)

    def check(tl: Timeline) -> CheckResult:
        samples = tl.belt_between(t0, t1)
        f = _frac(samples, lambda s: not s.signalling_fault and all(s.levels[i] > 0 for i in ss))
        return CheckResult(f"motor {_names(ss)} on {t0:g}-{t1:g}s", f >= min_frac, f"{f:.0%} of samples (need {min_frac:.0%})")

    return check


def motors_off(sectors: int | Sequence[int], t0: float, t1: float, max_frac: float = 0.05) -> Check:
    """None of the listed motors vibrates during [t0, t1] (the fault pattern counts as vibrating)."""
    ss = _sectors(sectors)

    def check(tl: Timeline) -> CheckResult:
        samples = tl.belt_between(t0, t1)
        f = _frac(samples, lambda s: s.signalling_fault or any(s.levels[i] > 0 for i in ss))
        return CheckResult(f"motor {_names(ss)} quiet {t0:g}-{t1:g}s", f <= max_frac, f"buzzing {f:.0%} (max {max_frac:.0%})")

    return check


def strongest(sector: int, t0: float, t1: float) -> Check:
    """The listed motor has the highest mean level over [t0, t1]."""

    def check(tl: Timeline) -> CheckResult:
        samples = [s for s in tl.belt_between(t0, t1) if not s.signalling_fault]
        if not samples:
            return CheckResult(f"motor {sector} strongest {t0:g}-{t1:g}s", False, "no alert samples")
        mean = np.mean([s.levels for s in samples], axis=0)
        best = int(np.argmax(mean))
        return CheckResult(f"motor {sector} strongest {t0:g}-{t1:g}s", best == sector,
                           f"mean levels {np.round(mean).astype(int).tolist()}")  # fmt: skip

    return check


def level_rises(sector: int, early: tuple[float, float], late: tuple[float, float], by: float = 30) -> Check:
    """Mean level of a motor is higher in the `late` window than the `early` one."""

    def check(tl: Timeline) -> CheckResult:
        def mean(w):
            ss = [s.levels[sector] for s in tl.belt_between(*w) if not s.signalling_fault]
            return float(np.mean(ss)) if ss else float("nan")

        a, b = mean(early), mean(late)
        return CheckResult(f"motor {sector} stronger when closer", b >= a + by, f"{a:.0f} -> {b:.0f} (need +{by:g})")

    return check


def fault_during(t0: float, t1: float, min_frac: float = 0.95) -> Check:
    def check(tl: Timeline) -> CheckResult:
        f = _frac(tl.belt_between(t0, t1), lambda s: s.signalling_fault)
        return CheckResult(f"fault pattern {t0:g}-{t1:g}s", f >= min_frac, f"{f:.0%} of samples (need {min_frac:.0%})")

    return check


def no_fault(t0: float, t1: float, max_frac: float = 0.0) -> Check:
    def check(tl: Timeline) -> CheckResult:
        f = _frac(tl.belt_between(t0, t1), lambda s: s.signalling_fault)
        return CheckResult(f"no fault {t0:g}-{t1:g}s", f <= max_frac, f"fault {f:.0%} of samples")

    return check


def fault_within(t_event: float, max_delay: float) -> Check:
    """The belt starts the fault pattern no later than max_delay after t_event."""

    def check(tl: Timeline) -> CheckResult:
        first = next((s.t for s in tl.belt if s.t >= t_event and s.signalling_fault), None)
        name = f"fault within {max_delay:g}s of t={t_event:g}"
        if first is None:
            return CheckResult(name, False, "never faulted")
        delay = first - t_event
        return CheckResult(name, delay <= max_delay, f"after {delay * 1000:.0f} ms")

    return check


def recovers_within(t_event: float, max_delay: float) -> Check:
    """After t_event the belt leaves the fault pattern within max_delay and stays out for 0.5 s."""

    def check(tl: Timeline) -> CheckResult:
        name = f"recovers within {max_delay:g}s of t={t_event:g}"
        after = [s for s in tl.belt if s.t >= t_event]
        for i, s in enumerate(after):
            if not s.signalling_fault and all(not x.signalling_fault for x in after[i : i + 100]):
                delay = s.t - t_event
                return CheckResult(name, delay <= max_delay, f"after {delay * 1000:.0f} ms")
        return CheckResult(name, False, "still faulting")

    return check


def status_reports(t0: float, t1: float, healthy: bool, min_frac: float = 0.8) -> Check:
    """The phone was told the right health state."""

    def check(tl: Timeline) -> CheckResult:
        msgs = [m for t, m in tl.statuses if t0 <= t <= t1 and m.get("type") == "status"]
        f = _frac(msgs, lambda m: bool(m.get("healthy")) == healthy)
        reasons = sorted({str(m.get("reason")) for m in msgs if not m.get("healthy")})[:2]
        return CheckResult(f"status healthy={healthy} {t0:g}-{t1:g}s", f >= min_frac,
                           f"{f:.0%} of {len(msgs)} msgs" + (f"; reasons {reasons}" if reasons else ""))  # fmt: skip

    return check


def status_unhealthy_within(t_event: float, max_delay: float, reason: str | None = None) -> Check:
    """The phone is told `healthy: false` (with `reason`, if given) no later than
    max_delay after t_event, and that verdict holds until at least max_delay later."""

    def check(tl: Timeline) -> CheckResult:
        name = f"status unhealthy within {max_delay:g}s of t={t_event:g}" + (f" ({reason!r})" if reason else "")
        msgs = [(t, m) for t, m in tl.statuses if t >= t_event and m.get("type") == "status"]
        first = next(((t, m) for t, m in msgs if not m.get("healthy")), None)
        if first is None:
            return CheckResult(name, False, f"never unhealthy in {len(msgs)} msgs")
        t, m = first
        if reason is not None:
            seen = {m2.get("reason") for t2, m2 in msgs if t <= t2 <= t + max_delay and not m2.get("healthy")}
            if reason not in seen:
                return CheckResult(name, False, f"reasons {sorted(map(str, seen))}")
        delay = t - t_event
        return CheckResult(name, delay <= max_delay, f"after {delay * 1000:.0f} ms ({m.get('reason')})")

    return check


def no_collision() -> Check:
    def check(tl: Timeline) -> CheckResult:
        return CheckResult("no collision", not tl.collisions, f"{tl.collisions}" if tl.collisions else "clear")

    return check


def counter(name: str, pred: Callable[[Any], bool], describe: str) -> Check:
    def check(tl: Timeline) -> CheckResult:
        value = tl.counters.get(name)
        return CheckResult(f"{name} {describe}", value is not None and pred(value), f"{name}={value}")

    return check


def clearance_at_end(min_m: float, max_m: float | None = None) -> Check:
    def check(tl: Timeline) -> CheckResult:
        c = tl.frames[-1].clearance_m if tl.frames else float("nan")
        ok = c >= min_m and (max_m is None or c <= max_m)
        rng = f"{min_m:g}-{max_m:g}" if max_m is not None else f">= {min_m:g}"
        return CheckResult(f"final clearance {rng} m", ok, f"{c:.2f} m")

    return check


# ------------------------------------------------------------------ metrics


def alert_metrics(tl: Timeline, danger_m: float, quiet_m: float, latency_s: float = 0.5,
                  settle_s: float = 1.0, start_s: float = 1.0) -> dict[str, Any]:  # fmt: skip
    """Ground truth vs. what the belt did, per sector. Informational.

    - danger coverage: of frames where a sector had an obstacle within danger_m,
      how often that motor was on within latency_s.
    - quiet-zone buzzing: of belt samples outside a fault, how often a motor was on
      although its sector had nothing within quiet_m for the preceding settle_s
      (alerts are allowed to fade out). Box-growth TTC alerts for approaching
      objects land here too, by design.
    """
    if not tl.frames or not tl.belt:
        return {}
    belt_t = [s.t for s in tl.belt]
    hits = total = 0
    for f in tl.frames:
        if f.t < start_s:
            continue
        for i, r in enumerate(f.sector_range_m):
            if r is not None and r <= danger_m:
                total += 1
                j0, j1 = bisect.bisect_left(belt_t, f.t), bisect.bisect_right(belt_t, f.t + latency_s)
                hits += any(not s.signalling_fault and s.levels[i] > 0 for s in tl.belt[j0:j1])
    frame_t = [f.t for f in tl.frames]
    quiet_on = quiet_total = 0
    for s in tl.belt:
        if s.t < start_s + settle_s or s.signalling_fault:
            continue
        k1 = bisect.bisect_right(frame_t, s.t)
        k0 = bisect.bisect_left(frame_t, s.t - settle_s)
        recent = tl.frames[k0:k1]
        if not recent:
            continue
        for i, lv in enumerate(s.levels):
            if all(f.sector_range_m[i] is None or f.sector_range_m[i] > quiet_m for f in recent):
                quiet_total += 1
                quiet_on += lv > 0
    return {
        "danger_coverage": round(hits / total, 3) if total else None,
        "danger_samples": total,
        "quiet_zone_buzzing": round(quiet_on / quiet_total, 3) if quiet_total else None,
        "quiet_samples": quiet_total,
    }


# ------------------------------------------------------------------ invariants (every scenario)


def never_silent_when_unhealthy(settle_s: float = 0.15, start_s: float = 0.5) -> Check:
    """Whenever the hub says it can't vouch for its output, the belt must be playing
    the fault pattern within settle_s, never silence or a stale alert."""

    def check(tl: Timeline) -> CheckResult:
        bad = []
        for h in tl.hub:
            if h.t < start_s or h.reason is None:
                continue
            # only judge samples where the hub has been unhealthy for at least settle_s
            if any(o.reason is None for o in tl.hub if h.t - settle_s <= o.t <= h.t):
                continue
            # The hub sets its reason and sends the matching command in one event-loop
            # step, so judge the belt samples on both sides of the moment `reason` was
            # read: a real violation shows on both, a state change right at h.t on one.
            before = tl.belt_between(h.t - 0.02, h.t)
            after = tl.belt_between(h.t, h.t + 0.02)
            if before and after and not before[-1].signalling_fault and not after[0].signalling_fault:
                bad.append(round(h.t, 2))
        return CheckResult("invariant: unhealthy hub -> fault pattern, never silence", not bad,
                           f"violations at t={bad[:5]}" if bad else "held")  # fmt: skip

    return check


def belt_never_idle(start_s: float = 0.5) -> Check:
    """After start-up the firmware must always be following a command or signalling
    a lost link; Idle/Dormant means silence nobody chose."""

    def check(tl: Timeline) -> CheckResult:
        bad = [s for s in tl.belt if s.t >= start_s and s.state.name in ("IDLE", "DORMANT")]
        return CheckResult("invariant: belt never idle after start", not bad,
                           f"{len(bad)} idle samples, first t={bad[0].t:.2f}" if bad else "held")  # fmt: skip

    return check


def belt_fault_is_reported(settle_s: float = 0.35, start_s: float = 0.5) -> Check:
    """The converse of never_silent_when_unhealthy: whenever the belt plays the
    fault pattern (its TTL ran out, the link dropped, or the hub sent it), the hub
    must not be telling the phone it's healthy. settle_s covers one firmware TTL
    (0.25 s) plus a belt-loop tick and sampling slack."""

    def check(tl: Timeline) -> CheckResult:
        bad = []
        for h in tl.hub:
            if h.t < start_s + settle_s or h.reason is not None:
                continue
            if any(o.reason is not None for o in tl.hub if h.t - settle_s <= o.t <= h.t):
                continue  # the hub only just became healthy; the belt follows within a command
            window = tl.belt_between(h.t - settle_s, h.t)
            if window and all(s.signalling_fault for s in window):
                bad.append(round(h.t, 2))
        return CheckResult("invariant: belt faulting -> hub not healthy", not bad,
                           f"healthy while belt faulted at t={bad[:5]}" if bad else "held")  # fmt: skip

    return check


INVARIANTS: tuple[Callable[[], Check], ...] = (never_silent_when_unhealthy, belt_never_idle, belt_fault_is_reported)
