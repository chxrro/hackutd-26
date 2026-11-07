"""Fake perception for bench-testing the belt end to end without any model:
a virtual obstacle sweeps left -> right and back. Never use it on a walk."""

from __future__ import annotations

import time

from haptic_hub.types import Frame, PerceptionResult, SectorRisk


class SweepPerception:
    name = "sweep"

    def __init__(self, sectors: int = 5, period_s: float = 6.0) -> None:
        self.sectors = sectors
        self.period_s = period_s

    def process(self, frame: Frame) -> PerceptionResult:
        phase = (time.monotonic() % self.period_s) / self.period_s  # 0..1
        center = (1 - abs(2 * phase - 1)) * (self.sectors - 1)  # 0 -> n-1 -> 0
        sectors = tuple(
            SectorRisk(risk=max(0.0, 1.0 - abs(i - center)), label="sweep") for i in range(self.sectors)
        )
        time.sleep(0.02)  # pretend to do some work
        return PerceptionResult(frame.seq, frame.received_at, sectors, inference_ms=20.0)

