"""Frame-source interface and the single-slot buffer between sources and perception."""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from haptic_hub.types import Frame


class LatestFrameSlot:
    """Holds only the newest frame. Perception is slower than the camera, so
    older frames are dropped instead of queued (queuing would add latency)."""

    def __init__(self) -> None:
        self._frame: Frame | None = None
        self._event = asyncio.Event()
        self.dropped = 0

    def put(self, frame: Frame) -> None:
        if self._event.is_set():  # the previous frame was never consumed
            self.dropped += 1
        self._frame = frame
        self._event.set()

    @property
    def latest(self) -> Frame | None:
        return self._frame

    async def next(self) -> Frame:
        """Waits for a frame that hasn't been returned yet, then returns it."""
        await self._event.wait()
        self._event.clear()
        assert self._frame is not None
        return self._frame


class FrameSource(Protocol):
    name: str

    async def run(self, slot: LatestFrameSlot) -> None:
        """Produce frames into `slot` until cancelled."""

    def describe(self) -> dict[str, Any]:
        """Small JSON-safe dict for logs and status."""


class StatusSink(Protocol):
    def publish(self, status: dict[str, Any]) -> None:
        """Send a status update to whoever is listening. Must not block."""
