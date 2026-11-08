"""Data passed between hub stages. No logic and no heavy imports."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass(frozen=True)
class Frame:
    seq: int
    image: np.ndarray  # BGR, HxWx3, uint8
    received_at: float  # hub time.monotonic() when the frame arrived
    capture_ms: int | None = None  # sender's wall clock, informational only
    source: str = "unknown"
    # Phone frames only: what the phone said produced them ("glasses" | "mock" |
    # "test_pattern" | "unknown"; docs/protocol.md). None for every other source.
    frame_source: str | None = None


@dataclass(frozen=True)
class SectorRisk:
    """Collision risk for one horizontal slice of the view (one belt motor)."""

    risk: float  # 0 = nothing to report, 1 = maximum urgency
    nearest_m: float | None = None  # estimated distance; unvalidated monocular estimate
    ttc_s: float | None = None  # estimated time to collision from box growth
    label: str | None = None  # detector class driving the risk, if any


@dataclass(frozen=True)
class PerceptionResult:
    frame_seq: int
    frame_received_at: float
    sectors: tuple[SectorRisk, ...]
    inference_ms: float
    # Optional data for the debug window (depth map, boxes). Never sent anywhere.
    debug: dict[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class Health:
    ok: bool
    reason: str | None = None
