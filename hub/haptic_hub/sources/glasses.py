"""WebSocket server that receives glasses frames from the iPhone app (meta/)."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import cv2
import numpy as np
from websockets.asyncio.server import ServerConnection, broadcast, serve
from websockets.exceptions import ConnectionClosed

from haptic_hub.protocol import ProtocolError, decode_frame_packet
from haptic_hub.sources.base import LatestFrameSlot
from haptic_hub.types import Frame

log = logging.getLogger(__name__)

MAX_MESSAGE_BYTES = 4 * 1024 * 1024


class GlassesServer:
    """Frame source *and* status sink: the phone sends frames, we send status back."""

    name = "glasses"

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self._clients: set[ServerConnection] = set()
        self._slot: LatestFrameSlot | None = None
        self.phone_stream_state: str | None = None
        self.frames_received = 0
        self.bad_messages = 0

    async def run(self, slot: LatestFrameSlot) -> None:
        self._slot = slot
        async with serve(self._handle, self.host, self.port, max_size=MAX_MESSAGE_BYTES) as server:
            log.info("waiting for the phone on ws://%s:%d", self.host, self.port)
            await server.serve_forever()

    def describe(self) -> dict[str, Any]:
        return {
            "source": self.name,
            "phones": len(self._clients),
            "phone_stream": self.phone_stream_state,
            "frames": self.frames_received,
            "bad_messages": self.bad_messages,
        }

    def publish(self, status: dict[str, Any]) -> None:
        if self._clients:
            broadcast(self._clients, json.dumps(status))

    async def _handle(self, conn: ServerConnection) -> None:
        self._clients.add(conn)
        log.info("phone connected from %s", conn.remote_address)
        try:
            async for message in conn:
                if isinstance(message, bytes):
                    self._on_frame(message)
                else:
                    self._on_text(message)
        except ConnectionClosed:
            pass
        finally:
            self._clients.discard(conn)
            if not self._clients:
                self.phone_stream_state = None
            log.info("phone disconnected (%s)", conn.remote_address)

    def _on_frame(self, data: bytes) -> None:
        received_at = time.monotonic()
        try:
            packet = decode_frame_packet(data)
        except ProtocolError as exc:
            self._reject(f"bad frame packet: {exc}")
            return
        image = cv2.imdecode(np.frombuffer(packet.jpeg, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            self._reject("frame JPEG didn't decode")
            return
        self.frames_received += 1
        assert self._slot is not None
        self._slot.put(Frame(packet.seq, image, received_at, packet.capture_ms, self.name))

    def _on_text(self, text: str) -> None:
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            self._reject("non-JSON text message")
            return
        kind = msg.get("type")
        if kind == "hello":
            log.info("phone says hello: %s", msg)
        elif kind == "phone_status":
            self.phone_stream_state = msg.get("stream")
            log.info("phone glasses stream: %s", self.phone_stream_state)

    def _reject(self, why: str) -> None:
        self.bad_messages += 1
        if self.bad_messages <= 5 or self.bad_messages % 100 == 0:
            log.warning("%s (total bad: %d)", why, self.bad_messages)
