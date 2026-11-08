"""Time-to-collision from how fast a tracked object's bounding box grows.

For an object at distance Z approaching at constant speed v, its image height is
h = k / Z, so the *inverse* height u = 1/h = Z / k falls linearly in time and
reaches zero at contact. Fitting a line to u over a short window gives

    TTC = u(now) / (-du/dt)

with no lag for a constant-speed approach. (Fitting h itself, as before, measures
the growth rate at the middle of the window and reads late, roughly by half the
window, because h grows faster and faster as the object nears.)

Box jitter makes the slope noisy. The estimate is gated on fit quality:
- at least `min_samples` observations spanning `min_span_s`;
- the closing rate used is the fitted one minus `sigma` standard errors (from the
  fit's residuals, never below what `noise_px` of height noise would give), so
  a noisy or kinked fit reads as farther away instead of producing a false alert;
- if the most recent half of the window shows a slower approach with
  `change_sigma` confidence (the object slowed or stopped), the closing rate is
  capped there, so the alert doesn't linger for a whole window.

No depth is needed, but it's only an estimate: head motion, partial occlusion and
the detector's box changing shape all add error that this model can't see.
"""

from __future__ import annotations

import math
from collections import deque

import numpy as np


class TtcEstimator:
    def __init__(
        self,
        window_s: float = 1.0,
        min_samples: int = 5,
        forget_after_s: float = 1.0,
        min_growth_per_s: float = 0.05,
        min_span_s: float = 0.25,
        sigma: float = 1.0,
        noise_px: float = 1.0,
        change_sigma: float = 1.5,
    ) -> None:
        if min_samples < 3:
            raise ValueError("a line fit with residuals needs at least 3 samples")
        self.window_s = window_s
        self.min_samples = min_samples
        self.forget_after_s = forget_after_s
        self.min_growth_per_s = min_growth_per_s  # relative growth below this = "not approaching"
        self.min_span_s = min_span_s
        self.sigma = sigma
        self.noise_px = noise_px
        self.change_sigma = change_sigma
        self._tracks: dict[int, deque[tuple[float, float]]] = {}
        self.last_fit: dict[int, dict[str, float]] = {}  # per track, for debugging

    @classmethod
    def from_config(cls, cfg) -> TtcEstimator:
        """cfg is a config.PerceptionConfig."""
        return cls(
            window_s=cfg.ttc_window_s,
            min_samples=cfg.ttc_min_samples,
            min_span_s=cfg.ttc_min_span_s,
            sigma=cfg.ttc_sigma,
            noise_px=cfg.ttc_noise_px,
            change_sigma=cfg.ttc_change_sigma,
        )

    def update(self, track_id: int, t: float, height: float) -> float | None:
        """Adds an observation; returns the TTC in seconds, or None if not (reliably) approaching."""
        history = self._tracks.setdefault(track_id, deque())
        if height > 0:
            history.append((t, height))
        while history and t - history[0][0] > self.window_s:
            history.popleft()
        self.last_fit.pop(track_id, None)
        if len(history) < self.min_samples or history[-1][0] - history[0][0] < self.min_span_s:
            return None

        ts = np.array([s[0] for s in history]) - history[-1][0]  # now = 0
        hs = np.array([s[1] for s in history])
        s_floor = self.noise_px / hs[-1] ** 2  # std of 1/h that noise_px of height error gives now
        full = _fit_inverse_height(ts, hs, s_floor)
        if full is None:
            return None
        slope, u_now, se, _ = full
        closing = -slope - self.sigma * se
        # Change-point check: if the most recent half of the window shows, with
        # change_sigma confidence, a slower approach than the whole window (the object
        # slowed or stopped), cap the closing rate there so the alert doesn't linger.
        recent = ts >= -self.window_s / 2
        capped = False
        if recent.sum() >= 4:
            part = _fit_inverse_height(ts[recent], hs[recent], max(s_floor, full[3]))
            if part is not None:
                upper = -part[0] + self.change_sigma * part[2]
                if upper < closing:
                    closing, u_now, capped = upper, part[1], True
        self.last_fit[track_id] = {"slope": slope, "se": se, "u_now": u_now, "n": float(len(ts)), "capped": float(capped)}
        if closing <= 0 or u_now <= 0:
            return None
        if closing / u_now < self.min_growth_per_s:
            return None
        return u_now / closing

    def prune(self, now: float) -> None:
        stale = [tid for tid, h in self._tracks.items() if not h or now - h[-1][0] > self.forget_after_s]
        for tid in stale:
            del self._tracks[tid]
            self.last_fit.pop(tid, None)

    def __len__(self) -> int:
        return len(self._tracks)


def _fit_inverse_height(ts: np.ndarray, hs: np.ndarray, s_min: float) -> tuple[float, float, float, float] | None:
    """Weighted line fit of u = 1/h against t (t = 0 is now).

    A fixed pixel error in h is an error of about noise/h^2 in u, so samples are
    weighted by (h / h_now)^4. Returns (du/dt, fitted u now, standard error of du/dt,
    residual std at h_now), with the residual std never taken below s_min.
    """
    if len(ts) < 3:
        return None
    u = 1.0 / hs
    w = (hs / hs[-1]) ** 4
    sw = float(w.sum())
    t_bar, u_bar = float(w @ ts) / sw, float(w @ u) / sw
    sxx = float(w @ (ts - t_bar) ** 2)
    if sxx <= 1e-12:
        return None
    slope = float(w @ ((ts - t_bar) * (u - u_bar))) / sxx
    resid = u - (u_bar + slope * (ts - t_bar))
    s_fit = max(math.sqrt(float(w @ resid**2) / (len(ts) - 2)), s_min)
    return slope, u_bar - slope * t_bar, s_fit / math.sqrt(sxx), s_fit
