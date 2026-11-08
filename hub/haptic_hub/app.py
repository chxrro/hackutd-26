"""The hub's main loop. Wires a frame source, perception, the haptic policy and a
belt together. Each stage runs on its own cadence:

  source      -> pushes frames into a 1-slot buffer as fast as they arrive
  perception  -> takes the newest frame, runs the model in a worker thread
  belt loop   -> at a fixed rate, sends the current command (heartbeat)
  status loop -> tells the phone what the belt is doing
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

from haptic_hub.belt.base import Belt
from haptic_hub.config import HubConfig
from haptic_hub.control import Control
from haptic_hub.haptics import HapticPolicy
from haptic_hub.latency import LatencyStats
from haptic_hub.perception.base import Perception
from haptic_hub.perception.spoken_obstacle import spoken_obstacle
from haptic_hub.protocol import BeltCommand, BeltMode
from haptic_hub.safety import SafetyMonitor
from haptic_hub.sources.base import FrameSource, LatestFrameSlot, StatusSink
from haptic_hub.types import Frame, Health, PerceptionResult

log = logging.getLogger(__name__)


class UserQuit(Exception):
    """The debug window was closed with q / Esc."""


class Hub:
    def __init__(
        self,
        cfg: HubConfig,
        source: FrameSource,
        perception: Perception,
        belt: Belt,
        status_sink: StatusSink | None = None,
        viewer: Any | None = None,  # haptic_hub.viz.DebugView
        control: Control | None = None,
        dashboard: Any | None = None,  # haptic_hub.dashboard.Dashboard
    ) -> None:
        self.cfg = cfg
        self.source = source
        self.perception = perception
        self.belt = belt
        self.status_sink = status_sink
        self.viewer = viewer
        self.slot = LatestFrameSlot()
        self.safety = SafetyMonitor(cfg.safety)
        self.policy = HapticPolicy(cfg.haptics, cfg.perception.sectors)
        self.result: PerceptionResult | None = None
        self.frame: Frame | None = None
        self.command: BeltCommand = BeltCommand.fault(cfg.perception.sectors)
        self.reason: str | None = "starting"
        self.control = control or Control(cfg, perception)
        self.dashboard = dashboard
        self.health: Health | None = None  # safety verdict alone, before pause / belt test
        self.result_frame: Frame | None = None  # the frame self.result was computed from
        if hasattr(source, "control"):  # sources/glasses.py routes hello/control messages here
            source.control = self.control
        self.latency = LatencyStats()
        self.latency_log_s = 5.0

    async def run(self) -> None:
        log.info("hub: source=%s perception=%s belt=%s", self.source.name, self.perception.name, self.belt.state)
        tasks = [
            asyncio.create_task(self.source.run(self.slot), name="source"),
            asyncio.create_task(self.belt.run(), name="belt"),
            asyncio.create_task(self._perception_loop(), name="perception"),
            asyncio.create_task(self._belt_loop(), name="belt-loop"),
            asyncio.create_task(self._status_loop(), name="status"),
            asyncio.create_task(self._latency_loop(), name="latency"),
        ]
        if self.dashboard is not None:
            tasks.append(asyncio.create_task(self.dashboard.run(self), name="dashboard"))
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:  # a stage died: surface it instead of carrying on half-working
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            await self.belt.close()

    async def _perception_loop(self) -> None:
        while True:
            frame = await self.slot.next()
            self.frame = frame
            self.latency.perception_started(frame, time.monotonic())
            try:
                result = await asyncio.to_thread(self.perception.process, frame)
            except Exception as exc:  # noqa: BLE001 - any model failure must become a fault
                log.exception("perception failed")
                self.safety.perception_failed(type(exc).__name__)
                await asyncio.sleep(0.2)
                continue
            self.result = result
            self.result_frame = frame
            self.safety.perception_ok(time.monotonic(), frame.received_at)
            self.latency.perception_done(result, time.monotonic())
            if self.viewer is not None and not self.viewer.show(frame, result, self.command, self.reason):
                raise UserQuit

    async def _belt_loop(self) -> None:
        period = 1.0 / self.cfg.haptics.send_hz
        while True:
            started = time.monotonic()
            latest = self.slot.latest
            if latest is not None:  # count arrivals even while perception is busy
                self.safety.frame_received(latest.received_at)
                self.control.note_frame(latest.received_at)  # a frame after end_session starts a new session
            health = self.safety.check(started)
            self.health = health
            command = self.policy.command(self.result, health)
            belt_fault = getattr(self.belt, "fault", None)  # e.g. "belt not acknowledging", "belt TTL expired"
            self.command, reason = self.control.apply(command, health, self.belt.state, started, belt_fault)
            if reason != self.reason:
                log.info("health: %s", "ok" if reason is None else reason)
            self.reason = reason
            await self.belt.send(self.command)
            self.latency.command_sent(time.monotonic())
            await asyncio.sleep(max(0.0, period - (time.monotonic() - started)))

    async def _status_loop(self) -> None:
        if self.status_sink is None:
            return
        while True:
            self.status_sink.publish(self.status())
            await asyncio.sleep(1.0 / self.cfg.status_hz)

    async def _latency_loop(self) -> None:
        """Hub-side stage timings in the log (and the --show window). Not sent to the phone."""
        while True:
            await asyncio.sleep(self.latency_log_s)
            summary = self.latency.summarize(time.monotonic(), self.slot.dropped)
            log.info("[%s] %s", self.source.name, summary.line())
            if self.viewer is not None:
                self.viewer.latency_line = summary.short()

    def _frame_source_field(self) -> dict[str, str]:
        """Only for the phone source (docs/protocol.md section 2). Display only:
        the belt output doesn't depend on it."""
        value = getattr(self.source, "frame_source", None)
        return {"frame_source": value} if isinstance(value, str) else {}

    def status(self) -> dict[str, Any]:
        now = time.monotonic()
        age = self.safety.frame_age(now)
        status = {
            "type": "status",
            "mode": self.command.mode.name.lower(),
            "healthy": self.reason is None,
            "reason": self.reason,
            "levels": list(self.command.levels),
            "belt": self.belt.state,
            "belt_confirmed": getattr(self.belt, "confirmed", None),
            "belt_firmware": getattr(self.belt, "firmware", None),
            "source": self.source.name,
            "inference_ms": round(self.result.inference_ms, 1) if self.result else None,
            "frame_age_ms": round(age * 1000, 1) if age is not None else None,
            "spoken_obstacle": None,
            **self._frame_source_field(),
            **self.control.status_fields(self.result, self.health, self.cfg.perception.sectors),
        }
        if (self.reason is None and self.command.mode == BeltMode.LEVELS
                and not self.control.paused and not self.control.stopped
                and self.result is not None and self.safety.check(now).ok):
            status["spoken_obstacle"] = spoken_obstacle(self.result, self.command.levels, self.cfg.perception)
        return status
