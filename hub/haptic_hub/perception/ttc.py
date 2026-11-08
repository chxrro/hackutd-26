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
        robust: bool = False,
    ) -> None:
        self.window_s = window_s
        self.min_samples = min_samples
        self.forget_after_s = forget_after_s
        self.min_growth_per_s = min_growth_per_s  # relative growth below this = "not approaching"
        self.robust = robust
        self._tracks: dict[int, deque[tuple[float, float]]] = {}

    def update(self, track_id: int, t: float, height: float, *, clipped: bool = False) -> float | None:
        """Adds an observation; returns the TTC in seconds, or None if not approaching."""
        history = self._tracks.setdefault(track_id, deque())
        if self.robust:
            if clipped or not np.isfinite([t, height]).all() or height <= 0:
                history.clear()
                return None
            if history and (t <= history[-1][0] or t - history[-1][0] > 0.3
                            or abs(height / history[-1][1] - 1) > 0.35):
                history.clear()
        history.append((t, height))
        while history and t - history[0][0] > self.window_s:
            history.popleft()
        if len(history) < self.min_samples:
            return None

        ts = np.array([s[0] for s in history])
        hs = np.array([s[1] for s in history])
        if self.robust:
            return self._robust_ttc(ts, hs)
        if ts[-1] - ts[0] < 1e-3:
            return None
        slope = float(np.polyfit(ts - ts[0], hs, 1)[0])  # pixels per second
        if height <= 0 or slope / height < self.min_growth_per_s:
            return None
        return height / slope

    def _robust_ttc(self, ts: np.ndarray, hs: np.ndarray) -> float | None:
        # Inverse image height is linear for constant-speed approach. A median
        # pairwise slope resists a single noisy detection without inventing depth.
        if len(ts) < max(4, self.min_samples) or ts[-1] - ts[0] < 0.15:
            return None
        x, y = ts - ts[0], 1.0 / hs
        slopes = [(y[j] - y[i]) / (x[j] - x[i]) for i in range(len(x))
                  for j in range(i + 1, len(x)) if x[j] - x[i] >= 0.05]
        if not slopes:
            return None
        slope = float(np.median(slopes))
        intercept = float(np.median(y - slope * x))
        fitted = intercept + slope * x
        total = float(np.sum((y - np.mean(y)) ** 2))
        residual = float(np.sum((y - fitted) ** 2))
        # Reject static/jittery tracks, poor fit and a currently inconsistent box.
        if (total <= 1e-15 or 1 - residual / total < 0.8 or fitted[-1] <= 0
                or abs(y[-1] - fitted[-1]) / fitted[-1] > 0.10
                or -slope / fitted[-1] < self.min_growth_per_s):
            return None
        return float(-fitted[-1] / slope)

    def prune(self, now: float) -> None:
        stale = [tid for tid, h in self._tracks.items() if not h or now - h[-1][0] > self.forget_after_s]
        for tid in stale:
            del self._tracks[tid]

    def __len__(self) -> int:
        return len(self._tracks)
