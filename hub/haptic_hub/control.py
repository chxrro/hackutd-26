"""Runtime settings that a paired phone or the laptop dashboard can change while
the hub runs (docs/protocol.md, "Control").

One Control object owns the settings and is the only place they change:

  paused       belt plays the pause cue, then a low reminder pulse every 15 s;
               status says ``healthy: false, reason: "alerts paused"``. Resuming
               plays the resume cue (cues.py).
  belt_test    a bounded left-to-right sweep (<= 3 s), refused while paused
  stopped      the wearer pressed Stop (``end_session``, paired only): the belt
               plays the session-ended cue once, then stays quiet; status says
               ``healthy: false, reason: "stopped by wearer"``. The next frame, or
               a ``phone_status`` with ``stream: "starting"``, starts a new session
               and the resume cue plays once the hub is healthy again.
  alert_range  presets for how early alerts start (far_m / ttc_far_s)
  classifier   swap the cloud person/object backend in ClassifiedPerception
  record       stub until session recording lands

Hardware and input faults always win over pause, on the belt (fault pattern) and
in the status ``reason``, so a paused hub never hides "no camera frames". Pausing
is explicit and felt; it is never a silent clear-path signal.

A deliberate stop is the one exception for input faults: after ``end_session``
the hub expects frames to stop, so "no camera frames" doesn't play the fault
pattern. A phone that goes away *without* ``end_session`` (crash, Wi-Fi, app
backgrounded) still gets the fault pattern. Belt link problems still win over
the stop in ``reason``.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import secrets
import time
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from haptic_hub import cues
from haptic_hub.config import HubConfig, PerceptionConfig
from haptic_hub.perception.classify import ClassifiedPerception
from haptic_hub.perception.geometry import box_sectors
from haptic_hub.protocol import BeltCommand, BeltMode
from haptic_hub.types import Health, PerceptionResult

log = logging.getLogger(__name__)

PAUSED_REASON = "alerts paused"
BELT_TEST_REASON = "belt test"
RESUME_REASON = "resuming alerts"
STOPPED_REASON = "stopped by wearer"
NOT_PAIRED = "not paired"


@dataclass(frozen=True)
class AlertRange:
    far_m: float  # depth risk starts below this distance
    ttc_far_s: float  # tracked-object risk starts below this time to collision


_DEFAULTS = PerceptionConfig()
# None of these are validated against measured distances. "normal" is exactly
# today's PerceptionConfig defaults; near/far move only where alerts *start*
# (near_m / ttc_near_s, where risk is full, stay as configured).
ALERT_RANGES: dict[str, AlertRange] = {
    "near": AlertRange(far_m=1.5, ttc_far_s=2.0),
    "normal": AlertRange(far_m=_DEFAULTS.far_m, ttc_far_s=_DEFAULTS.ttc_far_s),
    "far": AlertRange(far_m=3.5, ttc_far_s=4.0),
}

BELT_TEST_STEP_S = 0.4  # each motor in turn, left to right
BELT_TEST_LEVEL = 200
BELT_TEST_MAX_S = 3.0  # hard bound, whatever the motor count

SETTING_KEYS = ("classifier", "alert_range", "record")


def new_control_code() -> str:
    """16 lowercase hex characters (64 random bits)."""
    return secrets.token_hex(8)


def redact(msg: dict[str, Any]) -> dict[str, Any]:
    """Copy of a client message that is safe to log."""
    return {k: ("<redacted>" if k == "control_code" else v) for k, v in msg.items()}


def ws_logger(name: str) -> logging.Logger:
    """Logger for a hub WebSocket server. Capped at INFO even with -v: websockets
    logs every frame's payload at DEBUG, and those carry the control code."""
    logger = logging.getLogger(f"websockets.server.{name}")
    logger.setLevel(logging.INFO)
    return logger


def belt_link_reason(belt_state: str) -> str | None:
    if belt_state == "disconnected":
        return "belt disconnected"
    if belt_state == "connecting":
        return "belt connecting"
    return None


class Control:
    def __init__(
        self,
        cfg: HubConfig,
        perception: Any = None,
        cloud_factory: Callable[[], Any] | None = None,
        code: str | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """cloud_factory builds a fresh cloud backend (e.g. NimLabeler.from_config); pass
        it only when the hub was started with a cloud endpoint configured. It may block
        on the network, so it runs in a worker thread."""
        self.cfg = cfg
        cues.check_reminder_s(cfg.haptics.pause_reminder_s)
        self.classified = perception if isinstance(perception, ClassifiedPerception) else None
        self._cloud_factory = cloud_factory
        self.code = code or new_control_code()
        self._clock = clock
        self.paused = False
        self.record = False
        self.alert_range = self._matching_range()
        self._test_started: float | None = None
        # Cue timelines start on the belt loop's clock, at the first tick that can play them.
        self._pause_started: float | None = None  # this quiet paused stretch (cue, then reminders)
        self._resume_pending = False
        self._resume_started: float | None = None
        # The wearer ended the session (end_session). Only a frame received after
        # _stopped_at, or phone_status "starting", ends it.
        self.stopped = False
        self._stopped_at: float | None = None
        self._stop_cue_started: float | None = None
        self.belt_state: str | None = None  # kept current by the hub's belt loop
        self.belt_fault: str | None = None  # likewise (Belt.fault)
        self._lock = asyncio.Lock()
        self._paired: weakref.WeakSet[Any] = weakref.WeakSet()
        self._tasks: set[asyncio.Task[Any]] = set()

    # -- settings ----------------------------------------------------------

    @property
    def classifier(self) -> str:
        return "nim" if self.classified is not None and self.classified.cloud is not None else "local"

    @property
    def cloud_available(self) -> bool:
        return self.classified is not None and (self._cloud_factory is not None or self.classified.cloud is not None)

    def settings(self) -> dict[str, Any]:
        return {
            "classifier": self.classifier,
            "alert_range": self.alert_range,
            "record": self.record,
            "paused": self.paused,
        }

    def _matching_range(self) -> str:
        p = self.cfg.perception
        for name, preset in ALERT_RANGES.items():
            if (p.far_m, p.ttc_far_s) == (preset.far_m, preset.ttc_far_s):
                return name
        log.warning("perception thresholds match no alert_range preset; reporting 'custom'")
        return "custom"

    # -- requests ----------------------------------------------------------

    def is_code(self, code: Any) -> bool:
        return isinstance(code, str) and hmac.compare_digest(code.encode(), self.code.encode())

    async def handle(self, msg: dict[str, Any], paired: bool) -> dict[str, Any]:
        """One control request -> its ack. Requests are applied one at a time, in order."""
        async with self._lock:
            error = await self._dispatch(msg) if paired else NOT_PAIRED
        if error is not None and error != NOT_PAIRED:
            log.info("control %s refused: %s", msg.get("action"), error)
        return {"type": "ack", "id": msg.get("id"), "ok": error is None, "error": error, "settings": self.settings()}

    async def _dispatch(self, msg: dict[str, Any]) -> str | None:
        action = msg.get("action")
        if action == "get":
            return None
        if action == "pause":
            if not self.paused:
                log.info("control: alerts paused")
                self._pause_started, self._resume_pending = None, False
            self.paused, self._test_started = True, None
            return None
        if action == "resume":
            if self.paused:
                log.info("control: alerts resumed")
                self._resume_pending, self._resume_started = True, None
            self.paused = False
            return None
        if action == "end_session":
            self._end_session()
            return None
        if action == "belt_test":
            return self._start_belt_test()
        if action == "set":
            return await self._set(msg.get("settings"))
        return "unknown action"

    def _end_session(self) -> None:
        """The wearer pressed Stop. Idempotent: a repeat doesn't replay the cue.
        Clears pause too, so the next session starts with alerts on."""
        if not self.stopped:
            log.info("control: session ended by the wearer; expecting frames to stop")
            self._stop_cue_started = None
        self.stopped, self._stopped_at = True, self._clock()
        self.paused, self._test_started = False, None
        self._pause_started, self._resume_pending, self._resume_started = None, False, None

    def _start_session(self, why: str) -> None:
        if not self.stopped:
            return
        log.info("control: new session (%s)", why)
        self.stopped, self._stopped_at, self._stop_cue_started = False, None, None
        # Alerts are live again: the resume cue plays once the hub is healthy (a
        # fault, e.g. "waiting for camera frames", plays first if frames lag).
        self._resume_pending, self._resume_started = True, None

    def note_frame(self, received_at: float) -> None:
        """The hub's belt loop reports the newest frame's receive time (same clock as
        Control's). A frame that arrived after end_session starts a new session; one
        from before it doesn't, so frames already in flight can't undo a stop."""
        if self.stopped and self._stopped_at is not None and received_at > self._stopped_at:
            self._start_session("camera frames resumed")

    def _start_belt_test(self) -> str | None:
        if self.paused:
            return "refused while paused"
        if self.belt_test_running():
            return "belt test already running"
        if self.belt_state is not None and belt_link_reason(self.belt_state) is not None:
            return "belt not connected"
        if self.belt_fault is not None:
            return self.belt_fault
        self._test_started = self._clock()
        log.info("control: belt test")
        return None

    async def _set(self, settings: Any) -> str | None:
        if not isinstance(settings, dict) or not settings:
            return "settings must be a non-empty object"
        if unknown := [k for k in settings if k not in SETTING_KEYS]:
            return f"unknown setting: {', '.join(map(str, unknown))}"
        # Validate everything before changing anything: ok:false means nothing changed.
        classifier = settings.get("classifier", self.classifier)
        if classifier not in ("local", "nim"):
            return "classifier must be 'local' or 'nim'"
        alert_range = settings.get("alert_range", self.alert_range)
        if "alert_range" in settings and alert_range not in ALERT_RANGES:
            return f"alert_range must be one of {', '.join(ALERT_RANGES)}"
        if "record" in settings and not isinstance(settings["record"], bool):
            return "record must be true or false"
        if settings.get("record") is True:
            return "recording not available"
        if classifier != self.classifier:
            if (error := await self._switch_classifier(classifier)) is not None:
                return error
        if alert_range != self.alert_range:
            preset = ALERT_RANGES[alert_range]
            p = self.cfg.perception  # the same object perception reads on every frame
            p.far_m, p.ttc_far_s = preset.far_m, preset.ttc_far_s
            self.alert_range = alert_range
            log.info("control: alert_range %s (far_m %.1f, ttc_far_s %.1f)", alert_range, preset.far_m, preset.ttc_far_s)
        return None

    async def _switch_classifier(self, target: str) -> str | None:
        if self.classified is None:
            return "classifier not available (this perception has no detector)"
        if target == "local":
            old, self.classified.cloud = self.classified.cloud, None
            if old is not None and hasattr(old, "close"):
                old.close()
            log.info("control: classifier local")
            return None
        if self._cloud_factory is None:
            return "cloud classifier not configured"
        try:  # a fresh backend each time, so an old "no reply since" doesn't mark it stale at once
            cloud = await asyncio.to_thread(self._cloud_factory)
        except Exception as exc:  # noqa: BLE001 - reported to the client, nothing changes
            return f"cloud classifier unavailable: {exc}"
        self.classified.cloud = cloud
        log.info("control: classifier nim")
        return None

    # -- WebSocket clients (phone via sources/glasses.py, dashboard) -------

    def on_client_message(self, conn: Any, msg: dict[str, Any]) -> None:
        """hello / control / phone_status text from a phone connection. Never blocks: a control
        request is handled in a task that sends the ack to that connection only."""
        kind = msg.get("type")
        if kind == "phone_status":
            # Any connection may *end* a stop: that only brings back alerts and,
            # without frames, the fault pattern. Starting a stop needs pairing.
            if msg.get("stream") == "starting":
                self._start_session("phone started a new run")
            return
        if conn is None:
            return
        if kind == "hello":
            if self.is_code(msg.get("control_code")):
                self._paired.add(conn)
                log.info("phone paired for control")
            else:
                if "control_code" in msg:
                    log.warning("phone sent a wrong control code; control stays locked")
                self._paired.discard(conn)
        elif kind == "control":
            self.reply_later(conn, msg, paired=conn in self._paired)

    def reply_later(self, conn: Any, msg: dict[str, Any], paired: bool) -> None:
        task = asyncio.get_running_loop().create_task(self._reply(conn, msg, paired))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _reply(self, conn: Any, msg: dict[str, Any], paired: bool) -> None:
        ack = await self.handle(msg, paired)
        try:
            await conn.send(json.dumps(ack))
        except Exception:  # noqa: BLE001 - the client left; nothing to tell it
            log.debug("control ack not delivered", exc_info=True)

    @property
    def paired_clients(self) -> int:
        return len(self._paired)

    # -- belt loop ---------------------------------------------------------

    def belt_test_running(self, now: float | None = None) -> bool:
        if self._test_started is None:
            return False
        now = self._clock() if now is None else now
        return now - self._test_started < self.belt_test_duration(self.cfg.perception.sectors)

    @staticmethod
    def belt_test_duration(motors: int) -> float:
        return min(BELT_TEST_MAX_S, motors * BELT_TEST_STEP_S)

    def _belt_test_command(self, now: float, motors: int, ttl_ms: int) -> BeltCommand | None:
        if self._test_started is None:
            return None
        elapsed = now - self._test_started
        duration = self.belt_test_duration(motors)
        if not 0 <= elapsed < duration:
            self._test_started = None
            return None
        motor = min(motors - 1, int(elapsed / duration * motors))
        return BeltCommand(BeltMode.LEVELS, tuple(BELT_TEST_LEVEL if i == motor else 0 for i in range(motors)), ttl_ms)

    def apply(
        self, cmd: BeltCommand, health: Health, belt_state: str, now: float, belt_fault: str | None = None
    ) -> tuple[BeltCommand, str | None]:
        """The policy's command and the safety verdict -> what the belt actually gets,
        and the status reason (None = healthy).

        belt_fault is the belt's own report that it isn't confirming commands while
        its link is up (Belt.fault, e.g. "belt not acknowledging").

        Reason precedence: input/perception faults (ignored while stopped by the
        wearer), then the belt link (disconnected or connecting), then the belt not
        acknowledging, then stopped by wearer, then pause / resume cue. A belt test
        gives way to belt problems only; nothing else changed there."""
        self.belt_state, self.belt_fault = belt_state, belt_fault
        motors, ttl = len(cmd.levels), cmd.ttl_ms
        link = belt_link_reason(belt_state)
        unconfirmed = belt_fault if link is None else None
        if (link or unconfirmed) is not None and self._test_started is not None:
            log.info("control: belt test stopped (%s)", link or unconfirmed)
            self._test_started = None
        if (test := self._belt_test_command(now, motors, ttl)) is not None:
            self._resume_started = None  # a pending resume cue plays after the test
            return test, BELT_TEST_REASON  # these vibrations are not obstacles
        if not health.ok and not self.stopped:
            # A real fault wins over pause and cues: the fault pattern goes out.
            # While paused, the pause cue plays again once the fault clears, so the
            # wearer knows the silence that follows is the pause, not "fault over".
            self._pause_started = self._resume_started = None
            return (BeltCommand.fault(motors, ttl) if self.paused else cmd), health.reason
        if link is not None:  # the belt can't hear us; its firmware plays the fault itself
            self._pause_started = self._resume_started = self._stop_cue_started = None
            return BeltCommand.off(motors, ttl), link
        if unconfirmed is not None:
            # Writes may still get through now and then: if one does, it must be the
            # fault pattern, matching what the status says.
            self._pause_started = self._resume_started = self._stop_cue_started = None
            return BeltCommand.fault(motors, ttl), unconfirmed
        if self.stopped:
            # Deliberate: the session-ended cue once (again after a belt problem
            # clears, like the pause cue), then quiet. Never presented as healthy.
            if self._stop_cue_started is None:
                self._stop_cue_started = now
            levels = cues.play(cues.session_end_cue(motors), now - self._stop_cue_started) or (0,) * motors
            return _levels(levels, ttl), STOPPED_REASON
        if self.paused:
            if self._pause_started is None:
                self._pause_started = now
            levels = cues.paused_levels(now - self._pause_started, motors, self.cfg.haptics.pause_reminder_s)
            return _levels(levels, ttl), PAUSED_REASON
        if self._resume_pending:
            if self._resume_started is None:
                self._resume_started = now
            if (levels := cues.play(cues.resume_cue(motors), now - self._resume_started)) is not None:
                return _levels(levels, ttl), RESUME_REASON  # these vibrations are not obstacles
            self._resume_pending, self._resume_started = False, None
        return cmd, None

    # -- status ------------------------------------------------------------

    def status_fields(self, result: PerceptionResult | None, health: Health | None, motors: int) -> dict[str, Any]:
        """The optional status additions from docs/protocol.md."""
        fresh = result is not None and health is not None and health.ok and not self.stopped
        return {
            "paused": self.paused,
            "stopped": self.stopped,
            "settings": self.settings(),
            "sectors": [{"kind": k} for k in sector_kinds(result if fresh else None, motors, self.cfg.perception.min_box_height)],
        }


def _levels(levels: tuple[int, ...], ttl_ms: int) -> BeltCommand:
    return BeltCommand(BeltMode.LEVELS if any(levels) else BeltMode.OFF, levels, ttl_ms)


def sector_kinds(result: PerceptionResult | None, motors: int, min_box_height: float = 0.0) -> list[str | None]:
    """Kind of the largest labelled box covering each sector (box size stands in for
    "nearest"; no distance is measured). For speech and display only."""
    kinds: list[str | None] = [None] * motors
    if result is None or not (classified := result.debug.get("classified")):
        return kinds
    w, h = result.debug.get("infer_size", (0, 0))
    if not w or not h:
        return kinds
    best = [0.0] * motors
    for c in classified:
        box = c.box
        if box.height / h < min_box_height:
            continue
        for s in box_sectors(box.x1, box.x2, w, motors):
            if box.height > best[s]:
                best[s], kinds[s] = box.height, c.kind
    return kinds
