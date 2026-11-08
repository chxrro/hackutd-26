"""Decides whether the hub can vouch for its output. If it can't, the belt gets
the fault pattern instead of silence (silence would look like a clear path)."""

from __future__ import annotations

from haptic_hub.config import SafetyConfig
from haptic_hub.types import Health


class SafetyMonitor:
    def __init__(self, cfg: SafetyConfig) -> None:
        self.cfg = cfg
        self._last_frame_at: float | None = None
        self._last_result_at: float | None = None
        self._perception_error: str | None = None

    def frame_received(self, at: float) -> None:
        self._last_frame_at = at

    def perception_ok(self, at: float) -> None:
        self._last_result_at = at
        self._perception_error = None

    def perception_failed(self, why: str) -> None:
        self._perception_error = why

    def frame_age(self, now: float) -> float | None:
        return None if self._last_frame_at is None else now - self._last_frame_at

    def check(self, now: float) -> Health:
        if self._last_frame_at is None:
            return Health(False, "waiting for camera frames")
        if (age := now - self._last_frame_at) > self.cfg.frame_stale_s:
            return Health(False, f"no camera frames for {age:.1f} s")
        if self._perception_error is not None:
            return Health(False, f"perception failing: {self._perception_error}")
        if self._last_result_at is None:
            return Health(False, "perception warming up")
        if (age := now - self._last_result_at) > self.cfg.result_stale_s:
            return Health(False, f"perception stalled for {age:.1f} s")
        return Health(True)
