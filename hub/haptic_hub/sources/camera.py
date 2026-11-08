"""Frame sources read through OpenCV: a webcam, a video file, or a live network
stream (RTSP / HTTP MJPEG / anything FFmpeg can open).

Results from a webcam or file are NOT results from the glasses. The status
message reports the source, so a demo can't silently pass one off as the other.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

import cv2

from haptic_hub.sources.base import LatestFrameSlot
from haptic_hub.types import Frame

log = logging.getLogger(__name__)


class OpenCVSource:
    """Live feeds (webcam, stream) are read as fast as they arrive and reopened
    if they drop. Files are played at their native frame rate and looped."""

    RECONNECT_DELAY_S = 1.0

    def __init__(self, target: int | str, kind: str, loop_video: bool = True) -> None:
        if kind not in ("webcam", "video", "stream"):
            raise ValueError(f"unknown OpenCV source kind {kind!r}")
        self._target = target
        self.kind = kind
        self.live = kind != "video"
        self.name = f"{kind}:{target}"
        self._loop_video = loop_video
        self._stop = threading.Event()
        self.frames = 0
        self.reconnects = 0

    @classmethod
    def webcam(cls, index: int = 0) -> OpenCVSource:
        return cls(index, "webcam")

    @classmethod
    def video(cls, path: str, loop: bool = True) -> OpenCVSource:
        return cls(path, "video", loop)

    @classmethod
    def stream(cls, url: str) -> OpenCVSource:
        return cls(url, "stream")

    def describe(self) -> dict[str, Any]:
        return {"source": self.name, "frames": self.frames, "reconnects": self.reconnects}

    async def run(self, slot: LatestFrameSlot) -> None:
        loop = asyncio.get_running_loop()
        self._stop.clear()
        try:
            await asyncio.to_thread(self._read_forever, loop, slot)
        finally:
            self._stop.set()

    def stop(self) -> None:
        self._stop.set()

    def _open(self) -> cv2.VideoCapture | None:
        cap = cv2.VideoCapture(self._target)
        if self.live:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # newest frame, not a backlog
        if cap.isOpened():
            return cap
        cap.release()
        return None

    def _read_forever(self, loop: asyncio.AbstractEventLoop, slot: LatestFrameSlot) -> None:
        seq = 0
        while not self._stop.is_set():
            cap = self._open()
            if cap is None:
                if not self.live:
                    raise RuntimeError(f"couldn't open {self.name}")
                log.warning("couldn't open %s; retrying in %.0f s", self.name, self.RECONNECT_DELAY_S)
                self._stop.wait(self.RECONNECT_DELAY_S)
                continue

            frame_period = 0.0 if self.live else 1.0 / (cap.get(cv2.CAP_PROP_FPS) or 30.0)
            log.log(logging.INFO if seq == 0 else logging.DEBUG, "reading frames from %s", self.name)
            try:
                while not self._stop.is_set():
                    started = time.monotonic()
                    ok, image = cap.read()
                    if not ok:
                        break
                    seq += 1
                    self.frames += 1
                    frame = Frame(seq, image, time.monotonic(), int(time.time() * 1000), self.name)
                    loop.call_soon_threadsafe(slot.put, frame)
                    if frame_period:  # play files at their native speed
                        self._stop.wait(max(0.0, frame_period - (time.monotonic() - started)))
            finally:
                cap.release()

            if self._stop.is_set():
                return
            if not self.live:
                if not self._loop_video:
                    log.warning("%s ended", self.name)
                    return
                continue  # reopen = rewind
            self.reconnects += 1
            log.warning("%s dropped; reconnecting", self.name)
            self._stop.wait(self.RECONNECT_DELAY_S)
