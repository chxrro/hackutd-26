"""Time-to-collision from how fast a tracked object's bounding box grows.

For an object approaching at constant speed, its image height h grows such that
TTC ~= h / (dh/dt). No depth is needed, but it's only an estimate: head motion,
box jitter and partial occlusion all add noise. It's fitted over a short window
to smooth that.
"""

from __future__ import annotations

from collections import deque

import numpy as np


class TtcEstimator:
    def __init__(
        self,
        window_s: float = 0.6,
        min_samples: int = 3,
        forget_after_s: float = 1.0,
        min_growth_per_s: float = 0.05,
    ) -> None:
        self.window_s = window_s
        self.min_samples = min_samples
        self.forget_after_s = forget_after_s
        self.min_growth_per_s = min_growth_per_s  # relative growth below this = "not approaching"
        self._tracks: dict[int, deque[tuple[float, float]]] = {}

    def update(self, track_id: int, t: float, height: float) -> float | None:
        """Adds an observation; returns the TTC in seconds, or None if not approaching."""
        history = self._tracks.setdefault(track_id, deque())
        history.append((t, height))
        while history and t - history[0][0] > self.window_s:
            history.popleft()
        if len(history) < self.min_samples:
            return None

        ts = np.array([s[0] for s in history])
        hs = np.array([s[1] for s in history])
        if ts[-1] - ts[0] < 1e-3:
            return None
        slope = float(np.polyfit(ts - ts[0], hs, 1)[0])  # pixels per second
        if height <= 0 or slope / height < self.min_growth_per_s:
            return None
        return height / slope

    def prune(self, now: float) -> None:
        stale = [tid for tid, h in self._tracks.items() if not h or now - h[-1][0] > self.forget_after_s]
        for tid in stale:
            del self._tracks[tid]

    def __len__(self) -> int:
        return len(self._tracks)
