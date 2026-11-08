"""phone_status.frame_source (docs/protocol.md): the hub must never present mock
glasses, the test pattern or an undeclared phone as the real glasses. No models,
hardware or network beyond loopback."""

import asyncio
import json
import logging
import time

import cv2
import numpy as np
import pytest
from websockets.asyncio.client import connect

from haptic_hub.app import Hub
from haptic_hub.belt.sim import SimBelt
from haptic_hub.config import HubConfig
from haptic_hub.dashboard import Dashboard, describe_source
from haptic_hub.perception.sweep import SweepPerception
from haptic_hub.protocol import FramePacket, encode_frame_packet
from haptic_hub.record import FRAMES_FILE, SESSION_FILE, Recorder, RecordingSource
from haptic_hub.sources.base import LatestFrameSlot
from haptic_hub.sources.glasses import GlassesServer, parse_frame_source
from test_end_to_end import FakeSource, _free_port

_JPEG = cv2.imencode(".jpg", np.zeros((96, 54, 3), np.uint8))[1].tobytes()


def _packet(seq):
    return encode_frame_packet(FramePacket(seq, 1_700_000_000_000 + seq, 54, 96, _JPEG))


def _status(stream="streaming", **extra):
    return json.dumps({"type": "phone_status", "stream": stream, **extra})


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("glasses", "glasses"),
        ("mock", "mock"),
        ("test_pattern", "test_pattern"),
        (None, "unknown"),  # an old phone that doesn't send it
        ("Glasses", "unknown"),
        ("replay", "unknown"),  # a value from a newer phone
        ("", "unknown"),
        (1, "unknown"),
        (["glasses"], "unknown"),
    ],
)
def test_parse_frame_source_never_defaults_to_glasses(raw, expected):
    assert parse_frame_source(raw) == expected


def test_server_tracks_the_declaration_per_connection_and_tags_frames(caplog):
    server = GlassesServer("127.0.0.1", 0)
    slot = LatestFrameSlot()
    server._slot = slot
    a, b = object(), object()  # stand-ins for two phone connections
    assert server.frame_source == "unknown" and server.describe()["frame_source"] == "unknown"

    server._on_frame(_packet(1), a)  # frames before any phone_status
    assert slot.latest.frame_source == "unknown" and slot.latest.source == "glasses"

    with caplog.at_level(logging.INFO, logger="haptic_hub.sources.glasses"):
        server._on_text(_status(frame_source="mock"), a)
        for seq in range(2, 12):
            server._on_frame(_packet(seq), a)
        assert server.frame_source == "mock" and slot.latest.frame_source == "mock"

        server._on_frame(_packet(12), b)  # another phone that never declared
        assert server.frame_source == "unknown" and slot.latest.frame_source == "unknown"

        server._on_text(_status(frame_source="glasses"), b)
        server._on_frame(_packet(13), b)
        assert server.frame_source == "glasses" and slot.latest.frame_source == "glasses"

        server._on_text(_status(), b)  # a phone_status without the field resets it
        assert server.frame_source == "unknown"
        server._on_text(_status(frame_source="hologram"), b)
        assert server.frame_source == "unknown"

    changes = [r.getMessage() for r in caplog.records if r.getMessage().startswith("frame source:")]
    assert changes == [
        "frame source: mock, SIMULATED, NOT the glasses (phone_status)",  # once, not once per frame
        "frame source: UNKNOWN, not confirmed as the glasses (frames)",
        "frame source: glasses (phone_status)",
        "frame source: UNKNOWN, not confirmed as the glasses (phone_status)",
    ]
    assert any("unrecognized frame_source 'hologram'" in r.getMessage() for r in caplog.records)


async def _run_phone(server, hub, messages, frames=25):
    """Connects as a phone, sends `messages` then frames, returns the statuses it got."""
    task = asyncio.create_task(hub.run())
    statuses = []
    for _ in range(100):
        try:
            phone = await connect(f"ws://127.0.0.1:{server.port}")
            break
        except OSError:
            await asyncio.sleep(0.02)
    async with phone:
        for m in messages:
            await phone.send(m)
        for seq in range(1, frames + 1):
            await phone.send(_packet(seq))
            await asyncio.sleep(1 / 30)
            try:
                statuses.append(json.loads(await asyncio.wait_for(phone.recv(), 0.005)))
            except TimeoutError:
                pass
        deadline = time.monotonic() + 0.5  # a few more statuses (the hub sends ~5 Hz forever)
        while (left := deadline - time.monotonic()) > 0:
            try:
                statuses.append(json.loads(await asyncio.wait_for(phone.recv(), left)))
            except TimeoutError:
                break
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return statuses


@pytest.mark.parametrize(
    "messages, expected",
    [
        ([_status("starting", frame_source="mock")], "mock"),
        ([_status("starting", frame_source="test_pattern")], "test_pattern"),
        ([_status("starting", frame_source="glasses")], "glasses"),
        ([_status("starting")], "unknown"),  # an old phone
        ([], "unknown"),  # frames without any phone_status
    ],
)
def test_frame_source_round_trips_to_the_phone_status(messages, expected):
    hello = json.dumps({"type": "hello", "client": "test", "version": 1})

    async def scenario():
        server = GlassesServer("127.0.0.1", _free_port())
        hub = Hub(HubConfig(), server, SweepPerception(5), SimBelt(5), status_sink=server)
        statuses = await _run_phone(server, hub, [hello, *messages])
        return server, statuses

    server, statuses = asyncio.run(asyncio.wait_for(scenario(), timeout=20))
    assert statuses, "no status from the hub"
    assert statuses[-1]["source"] == "glasses" and statuses[-1]["frame_source"] == expected
    assert server.frame_source == "unknown"  # reset once the phone left


def test_frame_source_doesnt_change_the_belt_output():
    def commands(frame_source):
        server = GlassesServer("127.0.0.1", 0)
        server._slot = LatestFrameSlot()
        server._on_text(_status(frame_source=frame_source), None)
        hub = Hub(HubConfig(), server, SweepPerception(5), SimBelt(5))
        frame = None
        out = []
        for seq in range(1, 6):
            server._on_frame(_packet(seq), None)
            frame = server._slot.latest
            result = hub.perception.process(frame)
            out.append(hub.policy.command(result, hub.safety.check(time.monotonic())))
        return out

    assert commands("mock") == commands("glasses") == commands(None)


def test_status_has_no_frame_source_for_other_sources():
    hub = Hub(HubConfig(), FakeSource(seconds=1), SweepPerception(5), SimBelt(5))
    assert "frame_source" not in hub.status()


@pytest.mark.parametrize(
    "frame_source, verified, needle",
    [
        ("glasses", None, "Ray-Ban glasses (via the phone)"),
        ("mock", False, "SIMULATED"),
        ("test_pattern", False, "SIMULATED"),
        ("unknown", False, "UNKNOWN"),
        (None, False, "UNKNOWN"),
    ],
)
def test_dashboard_labels_anything_but_glasses(frame_source, verified, needle):
    d = describe_source("glasses", frame_source)
    assert needle in d["label"]
    assert d.get("verified") is verified
    assert d["frame_source"] == (frame_source or "unknown")
    if frame_source != "glasses":
        assert "Ray-Ban glasses (via the phone)" not in d["label"]


def test_dashboard_label_unchanged_for_other_sources():
    d = describe_source("webcam:0", "glasses")  # a stray value can't relabel a webcam
    assert "NOT the glasses" in d["label"] and "frame_source" not in d and "verified" not in d


def test_dashboard_state_uses_the_servers_frame_source():
    server = GlassesServer("127.0.0.1", 0)
    server._on_text(_status(frame_source="mock"), None)
    hub = Hub(HubConfig(), server, SweepPerception(5), SimBelt(5))
    state = Dashboard(_free_port()).state(hub)
    assert state["status"]["frame_source"] == "mock"
    assert state["source"]["label"].startswith("SIMULATED") and state["source"]["verified"] is False
    assert state["source"]["details"]["frame_source"] == "mock"


def test_recording_keeps_the_frame_source(tmp_path):
    async def scenario():
        server = GlassesServer("127.0.0.1", _free_port())
        recorder = Recorder(tmp_path)
        source = RecordingSource(server, recorder)
        hub = Hub(HubConfig(), source, SweepPerception(5), SimBelt(5), status_sink=server)
        hello = json.dumps({"type": "hello", "client": "test", "version": 1})
        statuses = await _run_phone(server, hub, [hello, _status("starting", frame_source="test_pattern")], frames=10)
        return recorder, statuses

    recorder, statuses = asyncio.run(asyncio.wait_for(scenario(), timeout=20))
    assert statuses and statuses[-1]["frame_source"] == "test_pattern"  # passes through RecordingSource
    lines = [json.loads(x) for x in (recorder.path / FRAMES_FILE).read_text().splitlines()]
    assert len(lines) == 10 and {x["frame_source"] for x in lines} == {"test_pattern"}
    meta = json.loads((recorder.path / SESSION_FILE).read_text())
    assert meta["frame_sources"] == {"test_pattern": 10}


def test_recording_of_other_sources_has_null_frame_source(tmp_path):
    from test_record_replay import _record

    recorder, _ = _record(tmp_path, [(1, 10), (2, 20)])
    lines = [json.loads(x) for x in (recorder.path / FRAMES_FILE).read_text().splitlines()]
    assert [x["frame_source"] for x in lines] == [None, None]
    assert "frame_sources" not in json.loads((recorder.path / SESSION_FILE).read_text())
