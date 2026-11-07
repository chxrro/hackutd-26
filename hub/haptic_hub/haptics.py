"""Turns per-sector risk + system health into the command the belt should play."""

from __future__ import annotations

from haptic_hub.config import HapticsConfig
from haptic_hub.protocol import BeltCommand, BeltMode
from haptic_hub.types import Health, PerceptionResult


class HapticPolicy:
    """Rising risk is applied immediately (warnings must not lag); falling risk
    fades out exponentially so motors don't flicker on noisy frames."""

    def __init__(self, cfg: HapticsConfig, motors: int) -> None:
        self.cfg = cfg
        self.motors = motors
        self._smoothed = [0.0] * motors
        self._last_seq: int | None = None

    def reset(self) -> None:
        self._smoothed = [0.0] * self.motors
        self._last_seq = None

    def command(self, result: PerceptionResult | None, health: Health) -> BeltCommand:
        if not health.ok or result is None:
            self.reset()
            return BeltCommand.fault(self.motors, self.cfg.ttl_ms)

        if len(result.sectors) != self.motors:
            raise ValueError(f"perception gave {len(result.sectors)} sectors for {self.motors} motors")

        if result.frame_seq != self._last_seq:  # only smooth once per new result
            self._last_seq = result.frame_seq
            a = self.cfg.release
            for i, sector in enumerate(result.sectors):
                raw = min(1.0, max(0.0, sector.risk))
                self._smoothed[i] = max(raw, a * self._smoothed[i] + (1 - a) * raw)

        levels = tuple(self._to_level(r) for r in self._smoothed)
        mode = BeltMode.LEVELS if any(levels) else BeltMode.OFF
        return BeltCommand(mode, levels, self.cfg.ttl_ms)

    def _to_level(self, risk: float) -> int:
        if risk < self.cfg.deadband:
            return 0
        span = (risk - self.cfg.deadband) / (1 - self.cfg.deadband)
        return round(self.cfg.min_level + span * (self.cfg.max_level - self.cfg.min_level))
