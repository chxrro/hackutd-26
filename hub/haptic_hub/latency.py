"""Per-stage latency on the hub's own clock (time.monotonic()), summarized every
few seconds in the log and the --show window. Never sent to the phone: the
status JSON is a fixed contract (docs/protocol.md).

Stages for one frame:

  receive ──queue──▶ perception start ──infer──▶ perception done ──to belt──▶ belt command sent

- queue:  time the frame sat in the 1-slot buffer before perception took it
- infer:  the perception's own `inference_ms`
- to belt: perception done -> the first belt command sent after it (the belt
  loop ticks at send_hz, so this averages about half a tick). This is when the
  command was handed to the BLE stack, not when a motor moved.
- hub total: receive -> that belt command
- capture->recv: the sender's `capture_ms` against the hub's wall clock. The
  two clocks are NOT synchronized, so this includes their offset. Informational.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from haptic_hub.types import Frame, PerceptionResult


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile (p in 0..100). None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


@dataclass
class StageSummary:
    window_s: float
    frames_in: int  # frames that reached the hub (processed + dropped)
    results: int
    dropped: int  # replaced in the 1-slot buffer before perception took them
    input_fps: float
    perception_fps: float
    stages: dict[str, tuple[float | None, float | None]] = field(default_factory=dict)  # name -> (median, p90) ms

    def line(self) -> str:
        parts = [
            f"{self.window_s:.0f}s: in {self.input_fps:.1f} fps, perception {self.perception_fps:.1f} fps, dropped {self.dropped}"
        ]
        for name, (p50, p90) in self.stages.items():
            if p50 is None:
                continue
            label = "capture->recv (unsynced clocks, info only)" if name == "capture_recv" else name.replace("_", " ")
            parts.append(f"{label} {p50:.0f}/{p90:.0f}")
        return "latency ms p50/p90 | " + " | ".join(parts)

    def short(self) -> str:
        """Compact version for the debug window."""
        s = self.stages

        def m(name: str) -> str:
            p50 = s.get(name, (None, None))[0]
            return "-" if p50 is None else f"{p50:.0f}"

        return f"{self.perception_fps:.1f}fps drop {self.dropped}  q {m('queue')} inf {m('infer')} belt {m('to_belt')} tot {m('hub_total')} ms"


class LatencyStats:
    """Fed from the hub's event loop only (no locking)."""

    STAGES = ("queue", "infer", "to_belt", "hub_total", "capture_recv")

    def __init__(self, now: float | None = None) -> None:
        self._window_start = time.monotonic() if now is None else now
        self._samples: dict[str, list[float]] = {k: [] for k in self.STAGES}
        self._results = 0
        self._dropped_at_start: int | None = None
        self._pending: tuple[float, float] | None = None  # (frame received_at, result done at)
        self.last: StageSummary | None = None

    def perception_started(self, frame: Frame, at: float, wall_now: float | None = None) -> None:
        self._samples["queue"].append((at - frame.received_at) * 1000)
        if frame.capture_ms is not None:
            wall = time.time() if wall_now is None else wall_now
            received_wall_ms = (wall - (at - frame.received_at)) * 1000
            self._samples["capture_recv"].append(received_wall_ms - frame.capture_ms)

    def perception_done(self, result: PerceptionResult, at: float) -> None:
        self._results += 1
        self._samples["infer"].append(result.inference_ms)
        self._pending = (result.frame_received_at, at)

    def command_sent(self, at: float) -> None:
        """The first command after a result is the one that reflects it."""
        if self._pending is None:
            return
        received_at, done_at = self._pending
        self._pending = None
        self._samples["to_belt"].append((at - done_at) * 1000)
        self._samples["hub_total"].append((at - received_at) * 1000)

    def summarize(self, now: float, dropped_total: int) -> StageSummary:
        """Closes the current window and starts a new one. `dropped_total` is
        the slot's running count."""
        if self._dropped_at_start is None:
            self._dropped_at_start = 0
        dropped = max(0, dropped_total - self._dropped_at_start)
        window = max(1e-9, now - self._window_start)
        frames_in = len(self._samples["queue"]) + dropped
        summary = StageSummary(
            window_s=window,
            frames_in=frames_in,
            results=self._results,
            dropped=dropped,
            input_fps=frames_in / window,
            perception_fps=self._results / window,
            stages={k: (percentile(v, 50), percentile(v, 90)) for k, v in self._samples.items()},
        )
        self._window_start = now
        self._dropped_at_start = dropped_total
        self._results = 0
        for v in self._samples.values():
            v.clear()
        self.last = summary
        return summary
