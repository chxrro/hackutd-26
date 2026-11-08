"""--record and --source replay:DIR. No models, hardware or network."""

import asyncio
import json
import threading
import time

import numpy as np

from haptic_hub.record import FRAMES_FILE, SESSION_FILE, Recorder, RecordingSource, RecordLimits, new_session_dir
from haptic_hub.sources.base import LatestFrameSlot
from haptic_hub.sources.replay import ReplaySource
from haptic_hub.types import Frame


def _frame(seq, value=0, capture_ms=None, source="fake", shape=(48, 64)):
    image = np.full((*shape, 3), value, np.uint8)
    return Frame(seq, image, time.monotonic(), capture_ms, source)


class ListSource:
    """Puts frames at fixed gaps, then stays silent."""

    name = "fake"

    def __init__(self, frames, gap_s):
        self.frames = frames
        self.gap_s = gap_s

    def describe(self):
        return {"source": self.name}

    def publish(self, status):
        self.published = status

    async def run(self, slot):
        for seq, value in self.frames:
            slot.put(_frame(seq, value, capture_ms=1_700_000_000_000 + seq))
            await asyncio.sleep(self.gap_s)


def _record(tmp_path, frames, gap_s=0.02, limits=None):
    recorder = Recorder(tmp_path, limits)
    source = RecordingSource(ListSource(frames, gap_s), recorder)
    slot = LatestFrameSlot()
    asyncio.run(source.run(slot))
    return recorder, slot


def test_session_folders_are_numbered(tmp_path):
    assert new_session_dir(tmp_path).name == "session-001"
    assert new_session_dir(tmp_path).name == "session-002"
    (tmp_path / "session-009").mkdir()
    assert new_session_dir(tmp_path).name == "session-010"


def test_recording_round_trip(tmp_path):
    recorder, slot = _record(tmp_path, [(1, 10), (2, 120), (3, 250)])
    assert slot.latest.seq == 3  # perception still got every frame
    path = recorder.path
    lines = [json.loads(x) for x in (path / FRAMES_FILE).read_text().splitlines()]
    assert [x["seq"] for x in lines] == [1, 2, 3]
    assert lines[0]["capture_ms"] == 1_700_000_000_001
    assert lines[0]["source"] == "fake" and (lines[0]["width"], lines[0]["height"]) == (64, 48)
    assert lines[0]["received_at"] < lines[1]["received_at"] < lines[2]["received_at"]
    meta = json.loads((path / SESSION_FILE).read_text())
    assert meta["frames_written"] == 3 and meta["frames_dropped_by_recorder"] == 0 and meta["source"] == "fake"

    replay = ReplaySource(path)
    assert replay.name == f"replay:{path.name}"
    got = []

    class Collect:
        def put(self, frame):
            got.append(frame)

    asyncio.run(replay.run(Collect()))
    assert [f.seq for f in got] == [1, 2, 3]
    assert [f.source for f in got] == [replay.name] * 3  # labelled as replay, not live
    assert all(f.capture_ms is None for f in got)
    for f, value in zip(got, (10, 120, 250), strict=True):
        assert f.image.shape == (48, 64, 3)
        assert abs(int(f.image.mean()) - value) <= 2  # JPEG is lossy


def test_recording_source_passes_through_name_and_publish(tmp_path):
    inner = ListSource([], 0)
    source = RecordingSource(inner, Recorder(tmp_path))
    assert source.name == "fake"
    assert hasattr(source, "publish")
    source.publish({"x": 1})
    assert inner.published == {"x": 1}


def test_replay_keeps_original_timing(tmp_path):
    path = tmp_path / "session-001"
    path.mkdir()
    import cv2

    ok, jpeg = cv2.imencode(".jpg", np.zeros((8, 8, 3), np.uint8))
    offsets = [0.0, 0.05, 0.25, 0.30]
    with (path / FRAMES_FILE).open("w") as f:
        for i, t in enumerate(offsets, start=1):
            (path / f"{i:06d}.jpg").write_bytes(jpeg.tobytes())
            f.write(json.dumps({"seq": i, "file": f"{i:06d}.jpg", "received_at": 1000.0 + t, "t": t}) + "\n")
        f.write('{"seq": 5, "file": "cut off')  # a crash mid-line must not break replay
    arrivals = []

    class Collect:
        def put(self, frame):
            arrivals.append(time.monotonic())

    asyncio.run(ReplaySource(path).run(Collect()))
    assert len(arrivals) == 4
    gaps = [b - a for a, b in zip(arrivals, arrivals[1:], strict=False)]
    for gap, want in zip(gaps, (0.05, 0.20, 0.05), strict=True):
        assert want - 0.005 <= gap <= want + 0.04, gaps


def test_slow_disk_drops_recorded_frames_instead_of_blocking(tmp_path):
    release = threading.Event()

    def stuck_writer(frame, t):
        release.wait(5)  # the disk is hung

    recorder = Recorder(tmp_path, RecordLimits(queue_frames=2), writer=stuck_writer)
    recorder.start("fake")
    slot = LatestFrameSlot()
    from haptic_hub.record import _TapSlot

    tap = _TapSlot(slot, recorder)
    started = time.monotonic()
    for seq in range(1, 101):
        tap.put(_frame(seq))
    elapsed = time.monotonic() - started
    release.set()
    recorder.close(timeout=2)
    assert elapsed < 0.1, elapsed  # never waited on the writer
    assert slot.latest.seq == 100  # perception saw every frame
    assert recorder.dropped >= 95 and recorder.queued <= 3


def test_frame_limit_and_every_kth(tmp_path):
    recorder, slot = _record(tmp_path, [(i, 0) for i in range(1, 21)], gap_s=0.001, limits=RecordLimits(max_frames=4, every=3))
    seqs = [json.loads(x)["seq"] for x in (recorder.path / FRAMES_FILE).read_text().splitlines()]
    assert seqs == [1, 4, 7, 10]
    assert recorder.stopped_reason.startswith("frame limit")
    assert slot.latest.seq == 20  # the hub kept running after recording stopped


def test_time_limit(tmp_path):
    recorder, _ = _record(tmp_path, [(i, 0) for i in range(1, 11)], gap_s=0.02, limits=RecordLimits(max_minutes=0.05 / 60))
    assert recorder.stopped_reason.startswith("time limit")
    assert 1 <= recorder.written < 10


def test_glasses_frames_are_recorded_and_status_still_flows(tmp_path):
    import socket

    import cv2
    from websockets.asyncio.client import connect

    from haptic_hub.app import Hub
    from haptic_hub.belt.sim import SimBelt
    from haptic_hub.config import HubConfig
    from haptic_hub.perception.sweep import SweepPerception
    from haptic_hub.protocol import FramePacket, encode_frame_packet
    from haptic_hub.sources.glasses import GlassesServer

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    async def scenario():
        server = GlassesServer("127.0.0.1", port)
        recorder = Recorder(tmp_path)
        source = RecordingSource(server, recorder)
        hub = Hub(HubConfig(), source, SweepPerception(5), SimBelt(5), status_sink=server)
        task = asyncio.create_task(hub.run())
        ok, jpeg = cv2.imencode(".jpg", np.zeros((96, 54, 3), np.uint8))
        statuses = []
        for _ in range(100):
            try:
                phone = await connect(f"ws://127.0.0.1:{port}")
                break
            except OSError:
                await asyncio.sleep(0.02)
        async with phone:
            for seq in range(1, 21):
                await phone.send(encode_frame_packet(FramePacket(seq, 1_700_000_000_000 + seq, 54, 96, jpeg.tobytes())))
                await asyncio.sleep(1 / 30)
                try:
                    statuses.append(json.loads(await asyncio.wait_for(phone.recv(), 0.005)))
                except TimeoutError:
                    pass
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return recorder, statuses

    recorder, statuses = asyncio.run(asyncio.wait_for(scenario(), timeout=20))
    lines = [json.loads(x) for x in (recorder.path / FRAMES_FILE).read_text().splitlines()]
    assert [x["seq"] for x in lines] == list(range(1, 21))
    assert lines[0]["source"] == "glasses" and lines[0]["capture_ms"] == 1_700_000_000_001
    assert (lines[0]["width"], lines[0]["height"]) == (54, 96)
    assert statuses and statuses[-1]["source"] == "glasses"
    assert json.loads((recorder.path / SESSION_FILE).read_text())["frames_written"] == 20
