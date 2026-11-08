"""Laptop dashboard: HTTP + WebSocket endpoints over loopback, host/origin checks,
the health banner, source labels, and frame dropping. No models, no hardware."""

import asyncio
import json
import time

import numpy as np
import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from haptic_hub.app import Hub
from haptic_hub.belt.sim import SimBelt
from haptic_hub.config import HubConfig
from haptic_hub.dashboard import Dashboard, _Page, banner, describe_source, preview_jpeg
from haptic_hub.perception.sweep import SweepPerception
from haptic_hub.protocol import BeltCommand
from haptic_hub.types import Frame
from test_end_to_end import FakeSource, _free_port


async def _http(port, path, host=None):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"GET {path} HTTP/1.1\r\nHost: {host or f'127.0.0.1:{port}'}\r\nConnection: close\r\n\r\n".encode())
    await writer.drain()
    raw = await asyncio.wait_for(reader.read(), 5)
    writer.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.decode().split("\r\n")
    headers = dict(line.split(": ", 1) for line in lines[1:])
    return int(lines[0].split()[1]), headers, body


async def _with_dashboard(scenario, seconds=30):
    port = _free_port()
    dash = Dashboard(port, fps=20)
    hub = Hub(HubConfig(), FakeSource(seconds=seconds), SweepPerception(5), SimBelt(5), dashboard=dash)
    task = asyncio.create_task(hub.run())
    for _ in range(100):  # wait for the listener
        try:
            await _http(port, "/")
            break
        except OSError:
            await asyncio.sleep(0.02)
    try:
        return await scenario(port, hub, dash)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def test_http_serves_page_and_assets_with_security_headers():
    async def scenario(port, hub, dash):
        return [await _http(port, p) for p in ("/", "/app.js", "/style.css", "/nope", "/../cli.py")]

    (s1, h1, b1), (s2, h2, _), (s3, h3, _), (s4, _, _), (s5, _, _) = asyncio.run(_with_dashboard(scenario))
    assert s1 == 200 and h1["Content-Type"].startswith("text/html") and b"<title>Haptic Hub</title>" in b1
    assert b'aria-live="assertive"' in b1
    assert "default-src 'self'" in h1["Content-Security-Policy"] and h1["X-Frame-Options"] == "DENY"
    assert s2 == 200 and "javascript" in h2["Content-Type"]
    assert s3 == 200 and h3["Content-Type"].startswith("text/css")
    assert s4 == 404 and s5 == 404


def test_http_refuses_foreign_host_header():
    async def scenario(port, hub, dash):
        return await _http(port, "/", host=f"evil.example:{port}"), await _http(port, "/", host=f"localhost:{port}")

    (bad, _, body), (good, _, _) = asyncio.run(_with_dashboard(scenario))
    assert bad == 403 and b"Haptic Hub" not in body  # DNS rebinding can't read the code
    assert good == 200


def test_dashboard_binds_loopback_only():
    assert Dashboard().host == "127.0.0.1" and Dashboard().port == 8780


def test_ws_streams_state_and_preview_and_takes_control():
    async def scenario(port, hub, dash):
        async with connect(f"ws://127.0.0.1:{port}/ws", origin=f"http://127.0.0.1:{port}") as ws:
            first = json.loads(await asyncio.wait_for(ws.recv(), 2))
            jpeg, state = None, None
            end = time.monotonic() + 3
            while time.monotonic() < end and (jpeg is None or state is None or state["banner"]["level"] != "ok"):
                msg = await asyncio.wait_for(ws.recv(), 2)
                if isinstance(msg, bytes):
                    jpeg = msg
                else:
                    state = json.loads(msg)
            await ws.send(json.dumps({"type": "control", "id": 5, "action": "pause"}))
            ack = None
            paused_state = None
            end = time.monotonic() + 3
            while time.monotonic() < end and (ack is None or paused_state is None):
                msg = await asyncio.wait_for(ws.recv(), 2)
                if isinstance(msg, str):
                    m = json.loads(msg)
                    if m["type"] == "ack":
                        ack = m
                    elif m["status"]["paused"]:
                        paused_state = m
            return first, jpeg, state, ack, paused_state, hub.control.code

    first, jpeg, state, ack, paused, code = asyncio.run(_with_dashboard(scenario))
    assert first["type"] == "state"
    assert jpeg is not None and jpeg[:2] == b"\xff\xd8"  # a JPEG preview
    assert state["banner"]["level"] == "ok", state["banner"]
    assert state["pairing"]["code"] == code
    assert state["source"]["kind"] == "fake" and state["belt"]["simulated"] is True
    assert set(state["status"]) >= {"mode", "healthy", "reason", "levels", "belt", "paused", "settings", "sectors"}
    assert state["options"]["alert_ranges"]["normal"] == {"far_m": 2.5, "ttc_far_s": 3.0}
    assert state["options"]["belt_test_s"] <= 3.0
    assert ack == {"type": "ack", "id": 5, "ok": True, "error": None, "settings": {**ack["settings"], "paused": True}}
    assert paused["banner"]["level"] == "paused" and paused["status"]["mode"] in ("off", "levels")  # quiet or pause cue


def test_ws_refuses_cross_site_origin():
    async def scenario(port, hub, dash):
        with pytest.raises(InvalidStatus) as err:
            await connect(f"ws://127.0.0.1:{port}/ws", origin="http://evil.example")
        return err.value.response.status_code, hub.control.paused

    status, paused = asyncio.run(_with_dashboard(scenario))
    assert status == 403 and paused is False


def test_slow_page_gets_frames_dropped_not_queued():
    async def scenario():
        dash = Dashboard(_free_port())
        hub = Hub(HubConfig(), FakeSource(seconds=1), SweepPerception(5), SimBelt(5))
        hub.slot.put(Frame(1, np.zeros((48, 64, 3), np.uint8), time.monotonic(), source="fake"))
        never = asyncio.get_running_loop().create_future()  # a send that never completes
        page = _Page()
        page.task = asyncio.ensure_future(never)
        dash._clients[object()] = page
        for _ in range(5):
            await dash._tick(hub)
        never.cancel()
        return dash.frames_dropped, dash.frames_sent

    dropped, sent = asyncio.run(scenario())
    assert dropped == 5 and sent == 0


def test_preview_without_result_is_the_raw_frame():
    frame = Frame(1, np.zeros((360, 640, 3), np.uint8), 0.0, source="webcam:0")
    jpeg = preview_jpeg(frame, None, BeltCommand.off(5), 320, 60)
    assert jpeg[:2] == b"\xff\xd8"


@pytest.mark.parametrize(
    "status, level, title",
    [
        ({"reason": None, "paused": False, "belt": "connected"}, "ok", "Alerts active"),
        ({"reason": "alerts paused", "paused": True, "belt": "connected"}, "paused", "Alerts paused"),
        ({"reason": "belt test", "paused": False, "belt": "simulated"}, "test", "Belt test running"),
        ({"reason": "stopped by wearer", "paused": False, "stopped": True, "belt": "connected"}, "stopped", "Stopped by wearer"),
        ({"reason": "belt disconnected", "stopped": True, "belt": "disconnected"}, "fault", "FAULT: belt disconnected"),
        ({"reason": "no camera frames for 1.2 s", "paused": False, "belt": "connected"}, "fault", "FAULT: no camera frames for 1.2 s"),
        ({"reason": "no camera frames for 1.2 s", "paused": True, "belt": "connected"}, "fault", "FAULT: no camera frames for 1.2 s"),
        ({"reason": "belt disconnected", "paused": True, "belt": "disconnected"}, "fault", "FAULT: belt disconnected"),
        ({"reason": "resuming alerts", "paused": False, "belt": "connected"}, "test", "Resuming alerts"),
        ({"reason": None, "paused": False, "belt": "disconnected"}, "fault", "FAULT: belt disconnected"),
        ({"reason": "belt disconnected", "paused": False, "belt": "disconnected"}, "fault", "FAULT: belt disconnected"),
        ({"reason": "belt not acknowledging", "paused": False, "belt": "connected"}, "fault", "FAULT: belt not acknowledging"),
        ({"reason": "belt not acknowledging", "paused": True, "belt": "connected"}, "fault", "FAULT: belt not acknowledging"),
        ({"reason": "belt TTL expired", "paused": False, "belt": "connected"}, "fault", "FAULT: belt TTL expired"),
        ({"reason": "belt playing fault pattern", "paused": True, "belt": "connected"}, "fault", "FAULT: belt playing fault pattern"),
        ({"reason": "belt not applying commands", "paused": False, "belt": "connected"}, "fault", "FAULT: belt not applying commands"),
        ({"reason": "belt status stale", "paused": False, "belt": "connected"}, "fault", "FAULT: belt status stale"),
    ],
)
def test_banner(status, level, title):
    b = banner(status)
    assert (b["level"], b["title"]) == (level, title) and b["detail"]


def test_banner_never_calls_silence_a_clear_path():
    for b in (banner({"reason": None, "belt": "simulated"}), banner({"reason": "alerts paused", "paused": True}),
              banner({"reason": "stopped by wearer", "stopped": True})):
        assert "not a clear path" in b["detail"] or "not mean the path is clear" in b["detail"]


@pytest.mark.parametrize(
    "name, kind, glasses",
    [("glasses", "glasses", True), ("webcam:0", "webcam", False), ("video:walk.mp4", "video", False), ("replay:s1", "replay", False)],
)
def test_source_labels(name, kind, glasses):
    d = describe_source(name)
    assert d["kind"] == kind and d["glasses"] is glasses
    if not glasses:
        assert "NOT" in d["label"]


def test_cloud_card_shows_stale_fallback():
    from haptic_hub.perception.classify import ClassifiedPerception
    from test_control import FakeCloud, FakeInner

    class StaleCloud(FakeCloud):
        def stale_reason(self, now):
            return "cloud classifier: no labels for 3.4 s (timeout)"

    hub = Hub(HubConfig(), FakeSource(seconds=1), ClassifiedPerception(FakeInner(), StaleCloud()), SimBelt(5))
    c = Dashboard(_free_port()).state(hub)["classifier"]
    assert c["backend"] == "nim" and c["stale"] is True and c["error"].startswith("cloud classifier: no labels")
