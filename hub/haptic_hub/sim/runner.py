"""Runs one scenario through the whole system, in real time:

  World --render--> SimPhone --WebSocket (docs/protocol.md)--> GlassesServer
     ^                                                          |
     |                                     Hub: perception -> HapticPolicy + SafetyMonitor
     |                                                          |
     +---- (optional) the wearer reacts <-- EmulatedBelt <-- BLE command bytes
                                            (real esp32/lib/belt_core logic)

Everything between the phone socket and the belt firmware is the production hub
code. Only the camera, the radio and the motors are simulated.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
import socket
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from haptic_hub.app import Hub
from haptic_hub.config import HubConfig
from haptic_hub.sim.checks import INVARIANTS, CheckResult, FrameSample, HubSample, Timeline, alert_metrics
from haptic_hub.sim.firmware import EmulatedBelt
from haptic_hub.sim.oracle import OraclePerception, TruthStore
from haptic_hub.sim.phone import SimPhone
from haptic_hub.sim.render import Render, Renderer, sector_ground_truth
from haptic_hub.sim.scenarios import Scenario
from haptic_hub.sources.glasses import GlassesServer

log = logging.getLogger(__name__)


@dataclass
class Report:
    scenario: str
    perception: str
    duration_s: float
    checks: list[CheckResult]
    metrics: dict[str, Any]
    counters: dict[str, Any]
    timeline: Timeline = field(repr=False)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def summary(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "perception": self.perception,
            "passed": self.passed,
            "checks": [asdict(c) for c in self.checks],
            "metrics": self.metrics,
            "counters": self.counters,
        }


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_perception(kind: str, cfg: HubConfig, truth: TruthStore, scenario: Scenario):
    """Builds the same chain `haptic-hub run` does: perception (plus optical-flow
    motion when cfg.perception.motion == "flow"), then the person/object
    classifier wrapper (local backend; the sim never calls the cloud)."""
    from haptic_hub.perception.classify import ClassifiedPerception

    if kind == "oracle":
        inner = OraclePerception(cfg.perception, truth, scenario.camera.width, scenario.noise)
        if cfg.perception.motion == "flow":
            from haptic_hub.perception.flow import FlowMotionPerception

            p = cfg.perception
            inner = FlowMotionPerception(inner, p.flow, p.sectors, p.ttc_near_s, p.ttc_far_s)
    else:
        from haptic_hub.perception import build_perception  # real models: needs `uv sync --extra vision`

        inner = build_perception(kind, cfg.perception)
    return inner if kind == "sweep" else ClassifiedPerception(inner)


def _unwrap(perc: Any) -> list[Any]:
    """perc and every perception it wraps (classifier, flow, ...), outermost first."""
    chain = []
    while perc is not None and perc not in chain:
        chain.append(perc)
        perc = getattr(perc, "inner", None)
    return chain


def _oracle(perc: Any) -> OraclePerception | None:
    """The OraclePerception inside any wrappers (for fault injection)."""
    return next((p for p in _unwrap(perc) if isinstance(p, OraclePerception)), None)


def _reset_tracking(perc: Any) -> None:
    """A reused real-model perception must not carry tracks over from the last scenario."""
    from haptic_hub.perception.ttc import TtcEstimator

    if hasattr(perc, "_ttc"):
        perc._ttc = TtcEstimator()
    motion = getattr(perc, "motion", None)
    if motion is not None and type(motion).__name__ == "FlowMotion":  # fresh optical-flow state per scenario
        perc.motion = type(motion)(motion.cfg, motion.sectors, motion.ttc_near_s, motion.ttc_far_s)
    model = getattr(getattr(perc, "_tracker", None), "_model", None)
    if model is not None:
        model.predictor = None  # ultralytics rebuilds it (and ByteTrack) on the next call


async def run_scenario(
    scenario: Scenario,
    perception: str | Any = "oracle",
    record: Path | None = None,
    show: bool = False,
    motion: str = "off",
) -> Report:
    cfg = HubConfig()
    cfg.perception.motion = motion
    if scenario.configure is not None:
        scenario.configure(cfg)
    world = scenario.build()
    renderer = Renderer(scenario.camera)
    truth = TruthStore()
    timeline = Timeline()

    if isinstance(perception, str):
        kind = perception
        perc = await asyncio.to_thread(make_perception, kind, cfg, truth, scenario)
    else:  # an already-built perception (lets callers reuse loaded models across scenarios)
        perc, kind = perception, getattr(perception, "name", "custom")
    for layer in _unwrap(perc):
        _reset_tracking(layer)
    oracle = _oracle(perc)

    port = _free_port()
    t0 = time.monotonic()
    belt = EmulatedBelt(cfg.perception.sectors, t0=t0)
    belt.samples = timeline.belt
    server = GlassesServer("127.0.0.1", port)
    hub = Hub(cfg, server, perc, belt, status_sink=server)
    hub.latency_log_s = 1e9  # one latency window for the whole scenario (read at the end)
    lock = threading.Lock()
    latest: dict[str, Any] = {}

    def now() -> float:
        return time.monotonic() - t0

    def belt_feel() -> tuple[tuple[int, ...] | None, bool]:
        s = belt.latest
        return (None, False) if s is None else (s.levels, s.signalling_fault)

    def capture() -> tuple[float, Render]:
        with lock:
            t = now()
            world.step(t, *belt_feel())
            snap = copy.deepcopy(world)
        rendered = renderer.render(snap)
        ranges = sector_ground_truth(snap, scenario.camera, cfg.perception.sectors)
        labeled = {
            ob.name or f"obj{k}": round(((ob.center[0] - snap.walker.x) ** 2 + (ob.center[1] - snap.walker.y) ** 2) ** 0.5, 3)
            for k, ob in enumerate(snap.obstacles) if ob.label  # type: ignore[attr-defined]
        }  # fmt: skip
        latest["frame"] = (t, rendered, snap, ranges)
        latest["sample"] = FrameSample(t, -1, snap.pose(), ranges, snap.clearance(), labeled)
        return t, rendered

    def on_frame(seq: int, t: float, _r: Render) -> None:
        sample = latest["sample"]
        sample.seq = seq
        timeline.frames.append(sample)

    # A paired phone, like the app with its control code, unless the scenario says otherwise.
    code = None if any(f.kind == "user_stop_unpaired" for f in scenario.faults) else hub.control.code
    phone = SimPhone(f"ws://127.0.0.1:{port}", capture, truth, t0, fps=scenario.fps, on_frame=on_frame,
                     control_code=code)  # fmt: skip
    phone.statuses = timeline.statuses

    async def world_loop() -> None:
        while True:
            with lock:
                world.step(now(), *belt_feel())
            await asyncio.sleep(0.01)

    async def fault_loop() -> None:
        while True:
            t = now()
            active = {f.kind: f for f in scenario.faults if f.start <= t < f.end}
            phone.faults.frozen = "camera_freeze" in active
            phone.faults.disconnected = "phone_disconnect" in active
            phone.faults.user_stopped = "user_stop" in active or "user_stop_unpaired" in active
            phone.faults.garbage_every = int(active["garbage"].value) if "garbage" in active else 0
            phone.faults.latency_s = active["latency"].value if "latency" in active else 0.0
            phone.faults.jitter_s = active["latency"].value / 2 if "latency" in active else 0.0
            belt.faults.drop_writes = "belt_drop" in active
            belt.faults.disconnected = "belt_disconnect" in active
            if oracle is not None:
                oracle.fail = "injected model crash" if "perception_crash" in active else None
                oracle.stall_s = active["perception_stall"].value if "perception_stall" in active else 0.0
            await asyncio.sleep(0.01)

    async def hub_log_loop() -> None:
        while True:
            r = hub.result
            timeline.hub.append(HubSample(now(), hub.command.mode.name.lower(), hub.command.levels, hub.reason,
                                          tuple(s.risk for s in r.sectors) if r else None))  # fmt: skip
            await asyncio.sleep(0.05)

    async def recorder_loop() -> None:
        from haptic_hub.sim.viz import SimView

        view = SimView(scenario, record, show, cfg)
        try:
            while True:
                frame = latest.get("frame")
                if frame is not None:
                    _, rendered, snap, ranges = frame
                    status = timeline.statuses[-1][1] if timeline.statuses else None
                    if not view.draw(now(), rendered, snap, ranges, belt.latest, hub.result, status):
                        return  # window closed: keep running the scenario, stop drawing
                await asyncio.sleep(1 / 15)
        finally:
            view.close()

    tasks = [
        asyncio.create_task(hub.run(), name="hub"),
        asyncio.create_task(phone.run(), name="phone"),
        asyncio.create_task(world_loop(), name="world"),
        asyncio.create_task(fault_loop(), name="faults"),
        asyncio.create_task(hub_log_loop(), name="hub-log"),
    ]
    if record is not None or show:
        tasks.append(asyncio.create_task(recorder_loop(), name="recorder"))
    try:
        done, _ = await asyncio.wait(tasks, timeout=scenario.duration, return_when=asyncio.FIRST_EXCEPTION)
        for task in done:  # something crashed: surface it
            if not task.cancelled() and task.exception() is not None and task.get_name() != "recorder":
                raise task.exception()  # type: ignore[misc]
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    timeline.collisions = list(world.collisions)
    timeline.counters = {
        "frames_sent": len(phone.sent),
        "frames_received": server.frames_received,
        "bad_messages": server.bad_messages,
        "garbage_sent": phone.garbage_sent,
        "phone_connects": phone.connects,
        "end_session_sent": phone.stops,
        "end_session_ok": sum(1 for a in phone.acks if a.get("ok") and a.get("id") is not None),
        "end_session_refused": sum(1 for a in phone.acks if not a.get("ok")),
        "frames_dropped_by_hub": hub.slot.dropped,
        "belt_writes": belt.writes,
        "belt_writes_lost": belt.lost_writes,
        "belt_writes_rejected": belt.rejected,
        "belt_reconnects": belt.reconnects,
        "status_msgs": len(timeline.statuses),
        "traveled_m": round(world.traveled, 2),
    }
    infer = [m.get("inference_ms") for _, m in timeline.statuses if m.get("inference_ms") is not None]
    ages = [m.get("frame_age_ms") for _, m in timeline.statuses if m.get("healthy") and m.get("frame_age_ms") is not None]
    metrics = alert_metrics(timeline, danger_m=scenario.danger_m, quiet_m=scenario.quiet_m)
    metrics["inference_ms_median"] = round(sorted(infer)[len(infer) // 2], 1) if infer else None
    # Hub-side stage timings from haptic_hub.latency (same process and clock as the sim phone,
    # so capture->receive is meaningful here; on real hardware the phone clock differs).
    stages = hub.latency.summarize(time.monotonic(), hub.slot.dropped).stages
    metrics["latency_ms"] = {k: [None if x is None else round(x, 1) for x in v] for k, v in stages.items()}
    metrics["frame_age_ms_median"] = round(sorted(ages)[len(ages) // 2], 1) if ages else None
    checks = [check(timeline) for check in [*scenario.checks, *(make() for make in INVARIANTS)]]
    return Report(scenario.name, getattr(perc, "name", kind), scenario.duration, checks, metrics, timeline.counters, timeline)
