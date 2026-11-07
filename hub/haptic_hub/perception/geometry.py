"""Pure-numpy helpers that map images, depth maps and boxes onto belt sectors.

Sector 0 is the left edge of the image (the wearer's left), sector n-1 the right.
"""

from __future__ import annotations

import math

import numpy as np


def sector_edges(width: int, n: int) -> list[tuple[int, int]]:
    """Pixel column ranges [x0, x1) for n equal vertical strips."""
    edges = np.linspace(0, width, n + 1).round().astype(int)
    return [(int(edges[i]), int(edges[i + 1])) for i in range(n)]


def depth_sector_nearest(
    depth_m: np.ndarray,
    n: int,
    band_top: float,
    band_bottom: float,
    percentile: float,
) -> list[float | None]:
    """Approximate nearest distance per sector, from a metric depth map.

    Only rows in [band_top, band_bottom) of the image height are used. A low
    percentile instead of the minimum, so a few bad pixels don't trigger an alert.
    Returns None for a sector with no valid depth values.
    """
    if depth_m.ndim != 2:
        raise ValueError(f"depth map must be 2-D, got shape {depth_m.shape}")
    h, w = depth_m.shape
    y0, y1 = int(h * band_top), max(int(h * band_bottom), int(h * band_top) + 1)
    band = depth_m[y0:y1]
    out: list[float | None] = []
    for x0, x1 in sector_edges(w, n):
        values = band[:, x0:x1]
        values = values[np.isfinite(values) & (values > 0)]
        out.append(float(np.percentile(values, percentile)) if values.size else None)
    return out


def linear_risk(value: float | None, full_at: float, zero_at: float) -> float:
    """1.0 when value <= full_at, 0.0 when value >= zero_at, linear in between."""
    if value is None or not math.isfinite(value):
        return 0.0
    if value <= full_at:
        return 1.0
    if value >= zero_at:
        return 0.0
    return (zero_at - value) / (zero_at - full_at)


def box_sectors(x1: float, x2: float, width: int, n: int) -> list[int]:
    """Indices of the sectors a box's horizontal extent overlaps."""
    lo, hi = max(0.0, min(x1, x2)), min(float(width), max(x1, x2))
    if hi <= lo:
        return []
    return [i for i, (s0, s1) in enumerate(sector_edges(width, n)) if lo < s1 and hi > s0]
