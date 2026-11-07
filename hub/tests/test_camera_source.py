"""OpenCV sources against a real (generated) video file."""

import asyncio

import cv2
import numpy as np
import pytest

from haptic_hub.sources.base import LatestFrameSlot
from haptic_hub.sources.camera import OpenCVSource


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / "clip.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 30, (64, 48))
    assert writer.isOpened()
    for i in range(10):
        writer.write(np.full((48, 64, 3), i * 20, np.uint8))
    writer.release()
    return str(path)


async def _collect(source, n, timeout=5.0):
    slot = LatestFrameSlot()
    task = asyncio.create_task(source.run(slot))
    seqs = []
    try:
        async with asyncio.timeout(timeout):
            while len(seqs) < n:
                seqs.append((await slot.next()).seq)
    finally:
        source.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    return seqs


def test_video_file_plays_and_loops(clip):
    source = OpenCVSource.video(clip)
    seqs = asyncio.run(_collect(source, 15))  # more than the 10 frames in the clip
    assert seqs == sorted(seqs) and seqs[-1] >= 15
    assert source.frames >= 15 and source.name.startswith("video:")


def test_video_file_without_loop_ends(clip):
    source = OpenCVSource.video(clip, loop=False)
    slot = LatestFrameSlot()
    asyncio.run(asyncio.wait_for(source.run(slot), 5))
    assert source.frames == 10


def test_missing_file_raises():
    with pytest.raises(RuntimeError):
        asyncio.run(OpenCVSource.video("/nonexistent/clip.mp4").run(LatestFrameSlot()))


def test_unreachable_stream_keeps_retrying_instead_of_crashing():
    source = OpenCVSource.stream("http://127.0.0.1:9/nothing.mjpg")
    source.RECONNECT_DELAY_S = 0.05

    async def scenario():
        task = asyncio.create_task(source.run(LatestFrameSlot()))
        await asyncio.sleep(0.5)
        assert not task.done()  # still trying
        source.stop()
        await asyncio.wait_for(task, 5)

    asyncio.run(scenario())
