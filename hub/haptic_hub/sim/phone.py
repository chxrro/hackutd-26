"""A stand-in for the iPhone app (meta/): captures simulated glasses frames and
sends them to the hub over the real WebSocket protocol (docs/protocol.md), and
records the status messages the hub sends back.

Mirrors meta/'s FrameForwarder defaults: at most 15 fps, JPEG quality ~0.6, one
frame in flight. Network faults can be injected through PhoneFaults.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from haptic_hub.protocol import FramePacket, encode_frame_packet
from haptic_hub.sim.oracle import Truth, TruthStore
from haptic_hub.sim.render import Render

log = logging.getLogger(__name__)


@dataclass
class PhoneFaults:
    frozen: bool = False  # glasses stream stalls: connected, but no frames
    disconnected: bool = False  # Wi-Fi / app drop: WebSocket closed
    garbage_every: int = 0  # send a malformed message after every N frames (0 = never)
    latency_s: float = 0.0  # one-way network delay added to each frame
    jitter_s: float = 0.0  # +- uniform jitter on top of latency


@dataclass
class SentFrame:
    seq: int
    t_capture: float  # scenario time the frame was rendered
    t_sent: float  # scenario time it left the phone


@dataclass
class SimPhone:
    url: str
    capture: Callable[[], tuple[float, Render]]  # blocking; returns (scenario time, render)
    truth: TruthStore
    t0: float
    fps: float = 15.0
    jpeg_quality: int = 60
    faults: PhoneFaults = field(default_factory=PhoneFaults)
    on_frame: Callable[[int, float, Render], None] | None = None
    sent: list[SentFrame] = field(default_factory=list)
    statuses: list[tuple[float, dict[str, Any]]] = field(default_factory=list)
    garbage_sent: int = 0
    connects: int = 0

    def __post_init__(self) -> None:
        self._seq = 0
        self._rng = np.random.default_rng(1)

    def _now(self) -> float:
        return time.monotonic() - self.t0

    async def run(self) -> None:
        while True:
            if self.faults.disconnected:
                await asyncio.sleep(0.02)
                continue
            try:
                conn = await connect(self.url, max_size=None, open_timeout=2)
            except OSError:
                await asyncio.sleep(0.05)
                continue
            self.connects += 1
            try:
                await self._session(conn)
            except ConnectionClosed:
                log.info("sim phone: connection closed")
            finally:
                await conn.close()

    async def _session(self, conn: ClientConnection) -> None:
        await conn.send(json.dumps({"type": "hello", "client": "sim", "version": 1}))
        await conn.send(json.dumps({"type": "phone_status", "stream": "streaming"}))
        receiver = asyncio.create_task(self._receive(conn))
        try:
            period = 1.0 / self.fps
            frozen_reported = False
            while not self.faults.disconnected:
                started = time.monotonic()
                if self.faults.frozen:
                    if not frozen_reported:
                        await conn.send(json.dumps({"type": "phone_status", "stream": "paused"}))
                        frozen_reported = True
                    await asyncio.sleep(period)
                    continue
                if frozen_reported:
                    await conn.send(json.dumps({"type": "phone_status", "stream": "streaming"}))
                    frozen_reported = False
                await self._send_frame(conn)
                await asyncio.sleep(max(0.0, period - (time.monotonic() - started)))
        finally:
            receiver.cancel()
            with contextlib.suppress(asyncio.CancelledError, ConnectionClosed):
                await receiver

    async def _send_frame(self, conn: ClientConnection) -> None:
        t_capture, render = await asyncio.to_thread(self.capture)
        capture_ms = int(time.time() * 1000)  # stamped before the network, like meta/'s forwarder
        self._seq = (self._seq + 1) & 0xFFFFFFFF
        seq = self._seq
        ok, jpeg = await asyncio.to_thread(
            cv2.imencode, ".jpg", render.image, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality]
        )
        if not ok:
            raise RuntimeError("JPEG encode failed")
        self.truth.put(seq, Truth(render.depth, render.boxes))
        if self.on_frame is not None:
            self.on_frame(seq, t_capture, render)
        delay = self.faults.latency_s + (self._rng.uniform(-1, 1) * self.faults.jitter_s)
        if delay > 0:
            await asyncio.sleep(delay)  # one frame in flight, like meta/'s forwarder
        h, w = render.image.shape[:2]
        packet = FramePacket(seq, capture_ms, w, h, jpeg.tobytes())
        await conn.send(encode_frame_packet(packet))
        self.sent.append(SentFrame(seq, t_capture, self._now()))
        if self.faults.garbage_every and seq % self.faults.garbage_every == 0:
            # Alternate a bad header and a good header around a broken JPEG.
            bad = b"XX\x01\x00" + bytes(40) if self.garbage_sent % 2 == 0 else encode_frame_packet(
                FramePacket(seq, 0, w, h, b"\xff\xd8not a jpeg")
            )
            await conn.send(bad)
            self.garbage_sent += 1

    async def _receive(self, conn: ClientConnection) -> None:
        async for message in conn:
            if isinstance(message, str):
                with contextlib.suppress(json.JSONDecodeError):
                    self.statuses.append((self._now(), json.loads(message)))
