"""Laptop dashboard (--dashboard): one page served by the hub on 127.0.0.1 only.

  GET /            the page (static/, plain HTML/CSS/JS, no build step)
  GET /app.js, /style.css
  WS  /ws          hub -> page: {"type": "state", ...} JSON, then the preview as a binary JPEG
                   page -> hub: the same "control" requests as the phone (docs/protocol.md)

The page runs on the operator's own machine and shows the control code, so it
is treated as paired. It is protected against other web pages instead: the
server only answers Host 127.0.0.1/localhost and refuses WebSocket handshakes
from any other browser Origin (DNS rebinding, cross-site WebSocket hijacking).

Never blocks the hub: drawing and JPEG encoding run in a worker thread, at most
`fps` per second and only while a page is open, and a page that hasn't taken the
previous update is skipped (frames are dropped, never queued).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from importlib import resources
from typing import Any

import cv2
from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from haptic_hub.control import (
    ALERT_RANGES,
    BELT_TEST_REASON,
    PAUSED_REASON,
    RESUME_REASON,
    Control,
    belt_link_reason,
    ws_logger,
)
from haptic_hub.protocol import BeltCommand
from haptic_hub.types import Frame, PerceptionResult

log = logging.getLogger(__name__)

DEFAULT_PORT = 8780
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
SECURITY_HEADERS = [
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "DENY"),
    (
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' blob:; connect-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'",
    ),
]

SOURCE_LABELS = {
    "glasses": ("Ray-Ban glasses (via the phone)", True),
    "webcam": ("Laptop webcam: NOT the glasses", True),
    "video": ("Video file replay: NOT live, NOT the glasses", False),
    "replay": ("Recorded session replay: NOT live", False),
    "stream": ("Network video stream: NOT the glasses", True),
}


def describe_source(name: str) -> dict[str, Any]:
    kind = name.partition(":")[0]
    label, live = SOURCE_LABELS.get(kind, (f"Unknown source '{kind}'", False))
    return {"name": name, "kind": kind, "label": label, "live": live, "glasses": kind == "glasses"}


def banner(status: dict[str, Any]) -> dict[str, str]:
    """The one-line verdict at the top of the page: level ok | paused | test | fault."""
    reason, paused, belt = status.get("reason"), status.get("paused", False), status.get("belt", "")
    if reason == BELT_TEST_REASON:
        return {"level": "test", "title": "Belt test running", "detail": "Motors sweep left to right. These vibrations are not obstacles."}
    if reason == PAUSED_REASON:
        return {
            "level": "paused",
            "title": "Alerts paused",
            "detail": "The belt played the pause cue and gives a short reminder pulse while paused. "
            "Silence does not mean the path is clear.",
        }
    if reason == RESUME_REASON:
        return {"level": "test", "title": "Resuming alerts", "detail": "The belt plays the resume cue. These vibrations are not obstacles."}
    if reason is not None:
        if paused and belt_link_reason(belt) == reason:
            detail = "Alerts are paused too. Commands are not reaching the belt."
        elif paused:
            detail = "Alerts are paused too, but the belt plays the fault pattern. Do not rely on it."
        else:
            detail = "The belt plays the fault pattern. Do not rely on it."
        return {"level": "fault", "title": f"FAULT: {reason}", "detail": detail}
    if (link := belt_link_reason(belt)) is not None:
        return {"level": "fault", "title": f"FAULT: {link}", "detail": "Commands are not reaching the belt. The wearer feels nothing."}
    return {"level": "ok", "title": "Alerts active", "detail": "Silence means no alert, not a clear path."}


def preview_jpeg(
    frame: Frame, result: PerceptionResult | None, cmd: BeltCommand, width: int, quality: int
) -> bytes | None:
    """Frame with person/object boxes, small. Runs in a worker thread."""
    from haptic_hub.viz import render

    if result is not None:
        img = render(frame, result, cmd, None, width, panel=False, banner=False)
    else:
        h0, w0 = frame.image.shape[:2]
        img = cv2.resize(frame.image, (width, max(1, round(h0 * width / w0))), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else None


class Dashboard:
    def __init__(
        self,
        port: int = DEFAULT_PORT,
        fps: float = 8.0,
        width: int = 480,
        quality: int = 65,
        phone_address: str | None = None,
    ) -> None:
        self.host = "127.0.0.1"  # never exposed to the network: it shows the control code
        self.port = port
        self.fps = fps
        self.width = width
        self.quality = quality
        self.phone_address = phone_address
        self._clients: dict[ServerConnection, _Page] = {}
        self._frame_key: tuple[int, float, bool] | None = None
        self._jpeg: bytes | None = None
        self.frames_sent = 0
        self.frames_dropped = 0

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    async def run(self, hub: Any) -> None:
        hosts = {f"{h}:{self.port}" for h in ("127.0.0.1", "localhost")}
        origins = [f"http://{h}" for h in sorted(hosts)] + [None]  # None: non-browser local clients

        def process_request(conn: ServerConnection, request: Request) -> Response | None:
            if request.headers.get("Host") not in hosts:
                return _response(403, "text/plain; charset=utf-8", b"forbidden host\n")
            path = request.path.split("?", 1)[0]
            if path == "/ws":
                return None  # WebSocket handshake; websockets checks the Origin
            if path not in STATIC:
                return _response(404, "text/plain; charset=utf-8", b"not found\n")
            name, ctype = STATIC[path]
            return _response(200, ctype, resources.files(__package__).joinpath("static", name).read_bytes())

        async def handler(conn: ServerConnection) -> None:
            await self._handle(conn, hub)

        async with serve(handler, self.host, self.port, process_request=process_request, origins=origins, max_size=64 * 1024, logger=ws_logger("dashboard")):
            log.info("dashboard on %s (this machine only)", self.url)
            await self._push_loop(hub)

    async def _handle(self, conn: ServerConnection, hub: Any) -> None:
        self._clients[conn] = _Page()
        log.info("dashboard page connected")
        try:
            await conn.send(json.dumps(self.state(hub)))
            async for message in conn:
                if not isinstance(message, str):
                    continue
                try:
                    msg = json.loads(message)
                except ValueError:
                    continue
                if isinstance(msg, dict) and msg.get("type") == "control":
                    hub.control.reply_later(conn, msg, paired=True)  # same control layer as the phone
        except ConnectionClosed:
            pass
        finally:
            page = self._clients.pop(conn, None)
            if page is not None and page.task is not None:
                page.task.cancel()

    async def _push_loop(self, hub: Any) -> None:
        period = 1.0 / self.fps
        while True:
            started = time.monotonic()
            if self._clients:
                await self._tick(hub)
            await asyncio.sleep(max(0.0, period - (time.monotonic() - started)))

    async def _tick(self, hub: Any) -> None:
        frame, result = self._preview_input(hub)
        key = None if frame is None else (frame.seq, frame.received_at, result is not None)
        if key is not None and key != self._frame_key:
            self._frame_key = key
            try:
                self._jpeg = await asyncio.to_thread(preview_jpeg, frame, result, hub.command, self.width, self.quality)
            except Exception:  # noqa: BLE001 - a bad preview must not stop the dashboard
                log.exception("dashboard preview failed")
                self._jpeg = None
        text = json.dumps(self.state(hub))
        for conn, page in list(self._clients.items()):
            if page.task is not None and not page.task.done():
                self.frames_dropped += 1  # that page is behind: skip it, don't queue
                continue
            jpeg = self._jpeg if page.frame_key != self._frame_key else None  # only what it hasn't seen
            page.frame_key = self._frame_key
            page.task = asyncio.create_task(self._send(conn, text, jpeg))

    def _preview_input(self, hub: Any) -> tuple[Frame | None, PerceptionResult | None]:
        """Boxes only while perception is current; otherwise the newest raw frame."""
        if self._preview_has_boxes(hub):
            return hub.result_frame, hub.result
        return hub.slot.latest, None

    async def _send(self, conn: ServerConnection, text: str, jpeg: bytes | None) -> None:
        with contextlib.suppress(ConnectionClosed):
            await conn.send(text)
            if jpeg is not None:
                await conn.send(jpeg)
                self.frames_sent += 1

    def state(self, hub: Any) -> dict[str, Any]:
        status = hub.status()
        classifier = None
        classified = hub.control.classified
        if classified is not None:
            cloud = classified.cloud
            classifier = {"backend": "local"}
            if cloud is not None:
                now = time.monotonic()
                classifier = cloud.status(now)
                if (why := cloud.stale_reason(now)) is not None:  # same fallback as classify.py
                    classifier = {**classifier, "stale": True, "error": why}
        source = describe_source(hub.source.name)
        with contextlib.suppress(Exception):
            source["details"] = hub.source.describe()
        return {
            "type": "state",
            "status": status,
            "banner": banner(status),
            "source": source,
            "belt": {"state": hub.belt.state, "simulated": hub.belt.state == "simulated"},
            "perception": hub.perception.name,
            "classifier": classifier,
            "options": {
                "classifier": classified is not None,
                "nim": hub.control.cloud_available,
                "belt_test_s": Control.belt_test_duration(len(status["levels"])),
                "alert_ranges": {k: {"far_m": v.far_m, "ttc_far_s": v.ttc_far_s} for k, v in ALERT_RANGES.items()},
            },
            "pairing": {
                "code": hub.control.code,
                "phone_address": self.phone_address,
                "paired_phones": hub.control.paired_clients,
            },
            "preview": {"live_boxes": self._preview_has_boxes(hub)},
        }

    @staticmethod
    def _preview_has_boxes(hub: Any) -> bool:
        return hub.result is not None and hub.result_frame is not None and hub.health is not None and hub.health.ok


class _Page:
    """One open dashboard page: its in-flight send, and the preview it last got."""

    def __init__(self) -> None:
        self.task: asyncio.Task[None] | None = None
        self.frame_key: tuple[int, float, bool] | None = None


def _response(status: int, content_type: str, body: bytes) -> Response:
    reason = {200: "OK", 403: "Forbidden", 404: "Not Found"}[status]
    headers = Headers([("Content-Type", content_type), ("Content-Length", str(len(body))), *SECURITY_HEADERS])
    return Response(status, reason, headers, body)
