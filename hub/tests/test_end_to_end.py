"""Runs the real hub loop with fake perception and a simulated belt, and the real
WebSocket server with a fake phone."""

import asyncio
import json
import socket
import time

import cv2
import numpy as np
from websockets.asyncio.client import connect

from haptic_hub.app import Hub
from haptic_hub.belt.sim import SimBelt
from haptic_hub.config import HubConfig
from haptic_hub.perception.sweep import SweepPerception
from haptic_hub.protocol import BeltMode, FramePacket, encode_frame_packet
from haptic_hub.sources.glasses import GlassesServer
from haptic_hub.types import Frame


class FakeSource:
    name = "fake"

    def __init__(self, seconds):
        self.seconds = seconds

    def describe(self):
        return {"source": self.name}

    async def run(self, slot):
        seq = 0
        end = time.monotonic() + self.seconds
        while time.monotonic() < end:
            seq += 1
            slot.put(Frame(seq, np.zeros((64, 64, 3), np.uint8), time.monotonic(), source=self.name))
            await asyncio.sleep(1 / 30)
        await asyncio.Event().wait()  # then the camera goes silent


def test_hub_drives_belt_then_faults_when_frames_stop():
    cfg = HubConfig()
    cfg.safety.frame_stale_s = 0.3
    belt = SimBelt(5)
    hub = Hub(cfg, FakeSource(seconds=0.8), SweepPerception(5), belt)
    modes = []

    async def scenario():
        task = asyncio.create_task(hub.run())
        for _ in range(30):
            await asyncio.sleep(0.05)
            modes.append(belt.last.mode)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
    assert BeltMode.LEVELS in modes[:16], modes
    assert modes[-1] == BeltMode.FAULT, modes  # camera stopped -> fault, not silence
    assert belt.sent > 10


class SlowPerception(SweepPerception):
    """Every result takes `delay` s, so each describes a frame that old."""

    def __init__(self, delay):
        super().__init__(5)
        self.delay = delay

    def process(self, frame):
        time.sleep(self.delay)
        return super().process(frame)


def test_slow_perception_faults_instead_of_playing_old_results():
    cfg = HubConfig()
    cfg.safety.result_max_age_s = 0.3
    belt = SimBelt(5)
    # Each result is 0.25 s old on arrival and ~0.5 s old before the next one:
    # never "stalled" (result_stale_s = 1.5), but too old to vouch for.
    hub = Hub(cfg, FakeSource(seconds=60), SlowPerception(0.25), belt)
    modes, reasons = [], set()

    async def scenario():
        task = asyncio.create_task(hub.run())
        await asyncio.sleep(0.6)  # first result in
        for _ in range(30):
            await asyncio.sleep(0.05)
            modes.append(belt.last.mode)
            reasons.add(hub.reason)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
    assert BeltMode.FAULT in modes, modes
    assert any(r and r.startswith("perception result is") for r in reasons), reasons


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _connect_with_retry(url):
    for _ in range(100):
        try:
            return await connect(url)
        except OSError:
            await asyncio.sleep(0.02)
    raise TimeoutError(url)


def test_glasses_server_receives_frames_and_sends_status():
    async def scenario():
        port = _free_port()
        server = GlassesServer("127.0.0.1", port)
        hub = Hub(HubConfig(), server, SweepPerception(5), SimBelt(5), status_sink=server)
        task = asyncio.create_task(hub.run())

        ok, jpeg = cv2.imencode(".jpg", np.zeros((96, 54, 3), np.uint8))
        assert ok
        status = None
        phone = await _connect_with_retry(f"ws://127.0.0.1:{port}")
        async with phone:
            await phone.send(json.dumps({"type": "hello", "client": "test", "version": 1}))
            await phone.send(json.dumps({"type": "phone_status", "stream": "streaming"}))
            await asyncio.sleep(0.05)
            assert server.phone_stream_state == "streaming"
            for seq in range(1, 40):
                await phone.send(encode_frame_packet(FramePacket(seq, 0, 54, 96, jpeg.tobytes())))
                await asyncio.sleep(1 / 30)
                try:
                    msg = json.loads(await asyncio.wait_for(phone.recv(), 0.005))
                    if msg.get("healthy"):
                        status = msg
                except TimeoutError:
                    pass
            await phone.send(b"garbage")
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.05)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert server.frames_received >= 30
        assert server.bad_messages == 1
        assert server.phone_stream_state is None  # cleared after the phone left
        assert status is not None, "never got a healthy status back"
        assert status["type"] == "status" and status["source"] == "glasses" and len(status["levels"]) == 5

    asyncio.run(asyncio.wait_for(scenario(), timeout=20))
