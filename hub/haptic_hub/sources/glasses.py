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

from haptic_hub.control import redact, ws_logger
from haptic_hub.protocol import ProtocolError, decode_frame_packet
from haptic_hub.sources.base import LatestFrameSlot
from haptic_hub.types import Frame

log = logging.getLogger(__name__)

MAX_MESSAGE_BYTES = 4 * 1024 * 1024

# docs/protocol.md section 1, phone_status.frame_source. Anything else (missing,
# misspelt, a future value) is "unknown", never "glasses".
FRAME_SOURCES = frozenset({"glasses", "mock", "test_pattern"})
UNKNOWN_FRAME_SOURCE = "unknown"


def parse_frame_source(value: Any) -> str:
    return value if isinstance(value, str) and value in FRAME_SOURCES else UNKNOWN_FRAME_SOURCE


class GlassesServer:
    """Frame source *and* status sink: the phone sends frames, we send status back."""

    name = "glasses"

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self._clients: set[ServerConnection] = set()
        self._slot: LatestFrameSlot | None = None
        self.phone_stream_state: str | None = None
        # What each connection last declared in phone_status (absent: unknown).
        self._declared: dict[ServerConnection | None, str] = {}
        # The declaration behind the newest frame (or newest phone_status).
        self.frame_source: str = UNKNOWN_FRAME_SOURCE
        self.frames_received = 0
        self.bad_messages = 0
        self.control: Any | None = None  # haptic_hub.control.Control, set by the Hub

    async def run(self, slot: LatestFrameSlot) -> None:
        self._slot = slot
        async with serve(self._handle, self.host, self.port, max_size=MAX_MESSAGE_BYTES, logger=ws_logger("glasses")) as server:
            log.info("waiting for the phone on ws://%s:%d", self.host, self.port)
            await server.serve_forever()

    def describe(self) -> dict[str, Any]:
        return {
            "source": self.name,
            "phones": len(self._clients),
            "phone_stream": self.phone_stream_state,
            "frame_source": self.frame_source,
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
                    self._on_frame(message, conn)
                else:
                    self._on_text(message, conn)
        except ConnectionClosed:
            pass
        finally:
            self._clients.discard(conn)
            self._declared.pop(conn, None)
            if not self._clients:
                self.phone_stream_state = None
                self._set_frame_source(UNKNOWN_FRAME_SOURCE, "no phone connected")
            log.info("phone disconnected (%s)", conn.remote_address)

    def _on_frame(self, data: bytes, conn: ServerConnection | None = None) -> None:
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
        frame_source = self._declared.get(conn, UNKNOWN_FRAME_SOURCE)
        self._set_frame_source(frame_source, "frames")
        assert self._slot is not None
        self._slot.put(Frame(packet.seq, image, received_at, packet.capture_ms, self.name, frame_source))

    def _on_text(self, text: str, conn: ServerConnection | None = None) -> None:
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            self._reject("non-JSON text message")
            return
        if not isinstance(msg, dict):
            self._reject("text message is not a JSON object")
            return
        kind = msg.get("type")
        if kind == "hello":
            log.info("phone says hello: %s", redact(msg))  # never log the control code
            if self.control is not None:
                self.control.on_client_message(conn, msg)
        elif kind == "control" and self.control is not None:
            self.control.on_client_message(conn, msg)
        elif kind == "phone_status":
            self.phone_stream_state = msg.get("stream")
            log.info("phone glasses stream: %s", self.phone_stream_state)
            raw = msg.get("frame_source")
            declared = parse_frame_source(raw)
            if declared == UNKNOWN_FRAME_SOURCE and raw is not None:
                log.warning("phone sent an unrecognized frame_source %.40r; treating it as unknown", raw)
            self._declared[conn] = declared
            self._set_frame_source(declared, "phone_status")

    def _set_frame_source(self, value: str, why: str) -> None:
        """Logs once per change, never per frame."""
        if value == self.frame_source:
            return
        self.frame_source = value
        if value == "glasses":
            log.info("frame source: glasses (%s)", why)
        elif value == UNKNOWN_FRAME_SOURCE:
            log.warning("frame source: UNKNOWN, not confirmed as the glasses (%s)", why)
        else:
            log.warning("frame source: %s, SIMULATED, NOT the glasses (%s)", value, why)

    def _reject(self, why: str) -> None:
        self.bad_messages += 1
        if self.bad_messages <= 5 or self.bad_messages % 100 == 0:
            log.warning("%s (total bad: %d)", why, self.bad_messages)
