"""Turns per-sector risk + system health into the command the belt should play."""

from __future__ import annotations

import math

from haptic_hub.config import HapticsConfig
from haptic_hub.protocol import BeltCommand, BeltMode
from haptic_hub.types import Health, PerceptionResult


class HapticPolicy:
    """Baseline applies rising risk immediately and fades falling risk per frame.

    Opt-in stable mode confirms moderate onsets across fresh results, bypasses
    confirmation for high risk, and fades using elapsed frame time. Neither
    profile is a validated collision-warning policy.
    """

    def __init__(self, cfg: HapticsConfig, motors: int) -> None:
        self.cfg = cfg
        self.motors = motors
        self._smoothed = [0.0] * motors
        self._last_seq: int | None = None
        self._last_time: float | None = None
        self._pending: list[float | None] = [None] * motors
        self._counts = [0] * motors
        self._active = [False] * motors
        self.decisions = ["quiet"] * motors
        if cfg.profile not in {"baseline", "stable"}:
            raise ValueError("unknown alert profile")
        if cfg.profile == "stable" and (not 0 <= cfg.confirm_s <= 0.5 or cfg.confirm_frames < 2
                or not 0 < cfg.confirm_max_gap_s <= 1 or not cfg.deadband < cfg.urgent_risk <= 1
                or not 0 < cfg.release_tau_s <= 1):
            raise ValueError("invalid stable alert settings")

    def reset(self) -> None:
        self._smoothed = [0.0] * self.motors
        self._last_seq = None
        self._last_time = None
        self._pending = [None] * self.motors
        self._counts = [0] * self.motors
        self._active = [False] * self.motors
        self.decisions = ["quiet"] * self.motors

    def command(self, result: PerceptionResult | None, health: Health) -> BeltCommand:
        if not health.ok or result is None:
            self.reset()
            return BeltCommand.fault(self.motors, self.cfg.ttl_ms)

        if len(result.sectors) != self.motors:
            raise ValueError(f"perception gave {len(result.sectors)} sectors for {self.motors} motors")
        if not math.isfinite(result.frame_received_at) or any(not math.isfinite(s.risk) for s in result.sectors):
            self.reset()
            return BeltCommand.fault(self.motors, self.cfg.ttl_ms)

        if result.frame_seq != self._last_seq:  # only smooth once per new result
            now = result.frame_received_at
            dt = now - self._last_time if self._last_time is not None else 0.0
            if self.cfg.profile == "stable" and self._last_time is not None and (
                dt <= 0 or dt > self.cfg.confirm_max_gap_s or result.frame_seq < self._last_seq
            ):
                self.reset()
                dt = 0.0
            self._last_seq = result.frame_seq
            self._last_time = now
            a = math.exp(-dt / self.cfg.release_tau_s) if self.cfg.profile == "stable" else self.cfg.release
            for i, sector in enumerate(result.sectors):
                raw = min(1.0, max(0.0, sector.risk))
                if self.cfg.profile == "stable":
                    raw = self._confirmed(i, raw, now)
                else:
                    self.decisions[i] = "immediate" if raw >= self.cfg.deadband else "quiet"
                self._smoothed[i] = max(raw, a * self._smoothed[i] + (1 - a) * raw)

        levels = tuple(self._to_level(r) for r in self._smoothed)
        mode = BeltMode.LEVELS if any(levels) else BeltMode.OFF
        return BeltCommand(mode, levels, self.cfg.ttl_ms)

    def _confirmed(self, i: int, risk: float, now: float) -> float:
        cfg = self.cfg
        if risk < cfg.deadband:
            self._active[i] = False
            self._pending[i] = None
            self._counts[i] = 0
            self.decisions[i] = "quiet"
            return 0.0
        if risk >= cfg.urgent_risk or self._active[i]:
            self._active[i] = True
            self.decisions[i] = "urgent" if risk >= cfg.urgent_risk else "confirmed"
            return risk
        if self._pending[i] is None:
            self._pending[i] = now
        self._counts[i] += 1
        if self._counts[i] >= cfg.confirm_frames and now - self._pending[i] + 1e-9 >= cfg.confirm_s:
            self._active[i] = True
            self.decisions[i] = "confirmed"
            return risk
        self.decisions[i] = "confirming"
        return 0.0

    def _to_level(self, risk: float) -> int:
        if risk < self.cfg.deadband:
            return 0
        span = (risk - self.cfg.deadband) / (1 - self.cfg.deadband)
        return round(self.cfg.min_level + span * (self.cfg.max_level - self.cfg.min_level))
