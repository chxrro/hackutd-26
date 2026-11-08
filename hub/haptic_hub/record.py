"""Opt-in session recording (`haptic-hub run ... --record`).

Wraps any frame source: every frame the source puts into the hub's 1-slot
buffer is also offered to a recorder. The recorder never blocks the event loop.
It hands frames to a writer thread through a small bounded queue, and if the
disk can't keep up it drops the recorded copy (perception still gets the frame).

A session folder looks like:

  recordings/session-003/
    session.json      what, when, bounds; final counts are added on close
    frames.jsonl      one line per saved frame
    000001.jpg ...

Recordings can contain faces and places. Keep them out of git (recordings/ is
ignored), get consent from people in view, and delete them when done.
"""

from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2

from haptic_hub.sources.base import LatestFrameSlot
from haptic_hub.types import Frame

log = logging.getLogger(__name__)

DEFAULT_DIR = Path(__file__).resolve().parents[1] / "recordings"  # hub/recordings
FRAMES_FILE = "frames.jsonl"
SESSION_FILE = "session.json"


@dataclass
class RecordLimits:
    max_minutes: float = 10.0
    max_frames: int = 6000  # ~5 min at 20 fps; ~60 KB each for glasses frames
    every: int = 1  # save every k-th frame the source produces
    queue_frames: int = 16  # frames waiting for the disk before we start dropping
    jpeg_quality: int = 90


def new_session_dir(root: Path) -> Path:
    """Creates the next numbered folder, session-001, session-002, ..."""
    root.mkdir(parents=True, exist_ok=True)
    taken = [int(m.group(1)) for p in root.iterdir() if (m := re.fullmatch(r"session-(\d+)", p.name))]
    n = max(taken, default=0) + 1
    while True:
        path = root / f"session-{n:03d}"
        try:
            path.mkdir()  # exclusive: two hubs can't grab the same number
            return path
        except FileExistsError:
            n += 1


class Recorder:
    """Thread-safe enough for its use: `offer` is called from the event loop
    only; the writer thread only reads from the queue."""

    def __init__(self, root: Path | str = DEFAULT_DIR, limits: RecordLimits | None = None, writer=None) -> None:
        self.root = Path(root)
        self.limits = limits or RecordLimits()
        self.path: Path | None = None
        self.offered = 0  # frames seen from the source
        self.queued = 0  # frames handed to the writer
        self.written = 0  # frames on disk
        self.dropped = 0  # frames skipped because the writer was behind
        self.stopped_reason: str | None = None
        self._write = writer or self._write_frame  # tests swap in a slow writer
        self._queue: queue.Queue[tuple[Frame, float] | None] = queue.Queue(maxsize=max(1, self.limits.queue_frames))
        self._thread: threading.Thread | None = None
        self._started_at: float | None = None
        self._jsonl = None
        self._meta: dict[str, Any] = {}

    @property
    def active(self) -> bool:
        return self._thread is not None and self.stopped_reason is None

    def start(self, source_name: str) -> Path:
        self.path = new_session_dir(self.root)
        self._started_at = time.monotonic()
        lim = self.limits
        self._meta = {
            "format": 1,
            "source": source_name,
            "started_wall": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "max_minutes": lim.max_minutes,
            "max_frames": lim.max_frames,
            "every": lim.every,
        }
        self._write_meta()
        self._jsonl = (self.path / FRAMES_FILE).open("a", encoding="utf-8")
        self._thread = threading.Thread(target=self._drain, name="recorder", daemon=True)
        self._thread.start()
        log.warning(
            "RECORDING ON: saving %s frames to %s (stops after %g min or %d frames%s). "
            "Footage may show people; get consent and don't commit it.",
            source_name,
            self.path,
            lim.max_minutes,
            lim.max_frames,
            f", every {lim.every}th frame" if lim.every > 1 else "",
        )
        return self.path

    def offer(self, frame: Frame) -> None:
        """Called for every frame. Never blocks."""
        if not self.active:
            return
        now = time.monotonic()
        assert self._started_at is not None
        if now - self._started_at >= self.limits.max_minutes * 60:
            self._stop(f"time limit ({self.limits.max_minutes:g} min)")
            return
        if self.queued >= self.limits.max_frames:
            self._stop(f"frame limit ({self.limits.max_frames})")
            return
        self.offered += 1
        if (self.offered - 1) % max(1, self.limits.every):
            return
        try:
            self._queue.put_nowait((frame, now - self._started_at))
        except queue.Full:
            self.dropped += 1
            if self.dropped in (1, 10) or self.dropped % 100 == 0:
                log.warning("recorder: disk is behind, dropped %d recorded frames (perception unaffected)", self.dropped)
            return
        self.queued += 1

    def close(self, timeout: float = 5.0) -> None:
        """Stops taking frames, lets the writer finish what's queued (up to
        `timeout`), then writes the final counts. Blocking: call off the loop."""
        if self._thread is None:
            return
        if self.stopped_reason is None:
            self.stopped_reason = "hub stopped"
        self._thread_join(timeout)
        self._thread = None
        if self._jsonl is not None:
            self._jsonl.close()
            self._jsonl = None
        self._meta.update(
            frames_written=self.written,
            frames_dropped_by_recorder=self.dropped,
            frames_seen=self.offered,
            stopped=self.stopped_reason,
            duration_s=round(time.monotonic() - (self._started_at or 0.0), 3),
        )
        self._write_meta()
        log.warning("recording stopped (%s): %d frames in %s, %d dropped", self.stopped_reason, self.written, self.path, self.dropped)

    def _thread_join(self, timeout: float) -> None:
        assert self._thread is not None
        deadline = time.monotonic() + timeout
        while True:  # the sentinel may not fit while the queue is full
            try:
                self._queue.put(None, timeout=max(0.01, deadline - time.monotonic()))
                break
            except queue.Full:
                if time.monotonic() >= deadline:
                    break
        self._thread.join(max(0.0, deadline - time.monotonic()))
        if self._thread.is_alive():
            log.warning("recorder: writer still busy after %.0f s; the last frames may be missing", timeout)

    def _stop(self, why: str) -> None:
        self.stopped_reason = why
        log.warning("RECORDING STOPPED: %s reached; the hub keeps running without recording", why)
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass  # close() will deliver it

    def _drain(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            frame, t = item
            try:
                self._write(frame, t)
            except Exception:  # noqa: BLE001 - a full disk must not take down the hub
                log.exception("recorder: couldn't write frame %d", frame.seq)

    def _write_frame(self, frame: Frame, t: float) -> None:
        assert self.path is not None and self._jsonl is not None
        n = self.written + 1
        name = f"{n:06d}.jpg"
        ok, jpeg = cv2.imencode(".jpg", frame.image, [cv2.IMWRITE_JPEG_QUALITY, self.limits.jpeg_quality])
        if not ok:
            raise RuntimeError("JPEG encode failed")
        (self.path / name).write_bytes(jpeg.tobytes())
        h, w = frame.image.shape[:2]
        line = {
            "seq": frame.seq,
            "file": name,
            "received_at": round(frame.received_at, 6),  # hub time.monotonic(), seconds
            "t": round(t, 6),  # seconds since recording started (hub clock)
            "capture_ms": frame.capture_ms,  # sender's clock, NOT synchronized with the hub
            "source": frame.source,
            "width": w,
            "height": h,
        }
        self._jsonl.write(json.dumps(line) + "\n")
        self._jsonl.flush()  # a crash still leaves a readable session
        self.written = n

    def _write_meta(self) -> None:
        assert self.path is not None
        (self.path / SESSION_FILE).write_text(json.dumps(self._meta, indent=2) + "\n")


class _TapSlot:
    """Looks like the hub's slot to the source. Each put goes to the real slot
    first (perception never waits on recording), then to the recorder."""

    def __init__(self, slot: LatestFrameSlot, recorder: Recorder) -> None:
        self._slot = slot
        self._recorder = recorder

    def put(self, frame: Frame) -> None:
        self._slot.put(frame)
        self._recorder.offer(frame)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._slot, name)


class RecordingSource:
    """Wraps any FrameSource without changing it. Name, status and `publish`
    (the glasses server is also the status sink) pass through."""

    def __init__(self, inner: Any, recorder: Recorder) -> None:
        self.inner = inner
        self.recorder = recorder
        self.name = inner.name

    def describe(self) -> dict[str, Any]:
        r = self.recorder
        return {**self.inner.describe(), "recording": str(r.path), "recorded": r.written, "record_dropped": r.dropped}

    async def run(self, slot: LatestFrameSlot) -> None:
        import asyncio

        self.recorder.start(self.inner.name)
        try:
            await self.inner.run(_TapSlot(slot, self.recorder))
            if self.recorder.stopped_reason is None:
                self.recorder.stopped_reason = "source ended"
        finally:
            await asyncio.to_thread(self.recorder.close)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)
