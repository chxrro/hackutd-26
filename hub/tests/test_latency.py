"""Per-stage latency stats (haptic_hub/latency.py)."""

import asyncio

import numpy as np

from haptic_hub.latency import LatencyStats, percentile
from haptic_hub.types import Frame, PerceptionResult


def test_percentile_nearest_rank():
    assert percentile([], 50) is None
    assert percentile([7.0], 90) == 7.0
    values = [float(x) for x in range(1, 11)]  # 1..10
    assert percentile(values, 50) == 5.0
    assert percentile(values, 90) == 9.0
    assert percentile(values, 100) == 10.0
    assert percentile(list(reversed(values)), 90) == 9.0  # order doesn't matter


def _frame(received_at, capture_ms=None):
    return Frame(1, np.zeros((2, 2, 3), np.uint8), received_at, capture_ms, "fake")


def _result(received_at, inference_ms):
    return PerceptionResult(1, received_at, (), inference_ms)


def test_stage_math_and_window():
    stats = LatencyStats(now=100.0)
    # frame 1: received 100.00, taken 100.01, done 100.11, belt 100.14
    stats.perception_started(_frame(100.00), at=100.01)
    stats.perception_done(_result(100.00, 95.0), at=100.11)
    stats.command_sent(at=100.14)
    stats.command_sent(at=100.20)  # later heartbeats don't count again
    # frame 2: received 100.50, taken 100.53, done 100.63, belt 100.70
    stats.perception_started(_frame(100.50), at=100.53)
    stats.perception_done(_result(100.50, 100.0), at=100.63)
    stats.command_sent(at=100.70)

    s = stats.summarize(now=102.0, dropped_total=6)
    assert s.window_s == 2.0
    assert s.results == 2 and s.dropped == 6 and s.frames_in == 8
    assert s.perception_fps == 1.0 and s.input_fps == 4.0
    q50, q90 = s.stages["queue"]
    assert round(q50) == 10 and round(q90) == 30
    assert s.stages["infer"] == (95.0, 100.0)
    assert [round(x) for x in s.stages["to_belt"]] == [30, 70]
    assert [round(x) for x in s.stages["hub_total"]] == [140, 200]
    assert s.stages["capture_recv"] == (None, None)
    assert "capture->recv" not in s.line()
    assert "perception 1.0 fps" in s.line() and "dropped 6" in s.line()

    # next window starts empty and counts drops from where the last one ended
    s2 = stats.summarize(now=104.0, dropped_total=9)
    assert s2.dropped == 3 and s2.results == 0 and s2.perception_fps == 0.0
    assert s2.stages["queue"] == (None, None)


def test_capture_to_receive_is_marked_unsynchronized():
    stats = LatencyStats(now=0.0)
    # hub wall clock 1_000_000.000 s when perception starts, 20 ms after receipt;
    # the sender stamped 1_000_000_000 - 200 ms (its own clock)
    stats.perception_started(_frame(50.0, capture_ms=999_999_800), at=50.02, wall_now=1_000_000.0)
    s = stats.summarize(now=1.0, dropped_total=0)
    p50, _ = s.stages["capture_recv"]
    assert round(p50) == 180
    assert "unsynced clocks, info only" in s.line()


def test_hub_reports_latency_summary():
    from test_end_to_end import FakeSource

    from haptic_hub.app import Hub
    from haptic_hub.belt.sim import SimBelt
    from haptic_hub.config import HubConfig
    from haptic_hub.perception.sweep import SweepPerception

    class Viewer:
        latency_line = None

        def show(self, *args):
            return True

    viewer = Viewer()
    hub = Hub(HubConfig(), FakeSource(seconds=5), SweepPerception(5), SimBelt(5), viewer=viewer)
    hub.latency_log_s = 0.5

    async def scenario():
        task = asyncio.create_task(hub.run())
        await asyncio.sleep(0.8)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
    s = hub.latency.last
    assert s is not None and s.results > 5
    assert s.stages["to_belt"][0] is not None and 0 <= s.stages["to_belt"][0] < 1000 / hub.cfg.haptics.send_hz + 30
    assert viewer.latency_line and "fps" in viewer.latency_line
    assert "latency" not in str(hub.status())  # the phone's status contract is unchanged
