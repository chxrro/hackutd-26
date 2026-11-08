"""Features that only meet once their PRs are merged together."""

import asyncio
import json
import socket
import time

from websockets.asyncio.client import connect

from haptic_hub.app import Hub
from haptic_hub.belt.sim import SimBelt
from haptic_hub.config import HubConfig
from haptic_hub.perception.sweep import SweepPerception
from haptic_hub.record import RecordingSource, Recorder
from haptic_hub.sources.glasses import GlassesServer


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_phone_control_still_works_while_recording(tmp_path):
    """--record wraps the glasses server; the hub's control layer must still reach
    the wrapped server, or the phone's pairing and controls are silently ignored."""

    async def scenario():
        port = _free_port()
        server = GlassesServer("127.0.0.1", port)
        source = RecordingSource(server, Recorder(tmp_path))
        hub = Hub(HubConfig(), source, SweepPerception(5), SimBelt(5), status_sink=server)
        assert server.control is hub.control
        task = asyncio.create_task(hub.run())
        for _ in range(100):
            try:
                phone = await connect(f"ws://127.0.0.1:{port}")
                break
            except OSError:
                await asyncio.sleep(0.02)
        async with phone:
            hello = {"type": "hello", "client": "test", "version": 1, "control_code": hub.control.code}
            await phone.send(json.dumps(hello))
            await phone.send(json.dumps({"type": "control", "id": 7, "action": "get"}))
            end, ack = time.monotonic() + 3, None
            while ack is None and time.monotonic() < end:
                msg = json.loads(await asyncio.wait_for(phone.recv(), end - time.monotonic()))
                ack = msg if msg.get("type") == "ack" else None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return ack

    ack = asyncio.run(asyncio.wait_for(scenario(), 20))
    assert ack is not None and ack["id"] == 7 and ack["ok"], ack
