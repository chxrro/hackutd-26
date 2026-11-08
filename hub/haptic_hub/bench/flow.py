"""The bench checks, in dependency order.

  connect          find the belt, connect, read info (version, motor count)
  motor-map        each motor alone, left to right; operator confirms position
  levels           one motor through a ramp of levels; operator confirms
  ttl              send, then stop: the belt must stop within the TTL and play
                   the fault pattern (timed from the serial log and/or the
                   belt's BLE status notifications, when available)
  write-timing     N paced BLE writes; time for the write call to return
  fault-command    mode=fault heartbeats, what the hub sends when it can't vouch
                   (with BLE status: the belt must report mode fault; the feel
                   stays operator-confirmed)
  fault-disconnect drop BLE mid-alert: belt must stop and play the fault pattern
  reconnect        the belt advertises again and the hub can reconnect
  belt-log         (with an observer) no rejected commands, no reboots

Everything the belt does is bounded: every command carries a TTL, buzzes last
about a second, and while the operator is answering a prompt the bench keeps
sending `off` so the belt stays quiet instead of timing out into the fault
pattern. Bench use only: belt on the table or in a hand, not worn on a walk.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from typing import Any, Protocol

from haptic_hub.belt.sim import render
from haptic_hub.bench.links import BenchLinkError, ConnectInfo, EventLog, effective_ttl_ms
from haptic_hub.bench.report import ERROR, FAIL, PASS, SKIPPED, UNCONFIRMED, CheckResult, timing_stats
from haptic_hub.config import HapticsConfig
from haptic_hub.protocol import BELT_PROTOCOL_VERSION, BeltCommand, BeltMode

CHECK_ORDER = (
    "connect",
    "motor-map",
    "levels",
    "ttl",
    "write-timing",
    "fault-command",
    "fault-disconnect",
    "reconnect",
    "belt-log",
)
SELECTABLE = ("motor-map", "levels", "ttl", "write-timing", "fault-command", "fault-disconnect")

# Default pins in esp32/include/config.h, far left to far right. Only used to
# print a suggestion; the firmware is the source of truth.
DEFAULT_FIRMWARE_PINS = (25, 26, 27, 32, 33)


class BenchAborted(Exception):
    """The operator quit (q) or stdin closed."""


def position_names(n: int) -> list[str]:
    if n == 5:
        return ["far left", "left", "centre", "right", "far right"]
    names = [f"position {i} from the left" for i in range(n)]
    if n >= 1:
        names[0] = "far left"
    if n >= 2:
        names[-1] = "far right"
    if n % 2 == 1 and n >= 3:
        names[n // 2] = "centre"
    return names


# ---------------------------------------------------------------- operator


class Operator(Protocol):
    assumed: bool  # True when answers are not from a person (--yes)

    def say(self, text: str) -> None: ...

    async def ask(self, question: str, choices: str = "ynr") -> str: ...

    async def ask_text(self, question: str, default: str) -> str: ...


async def _read_line(prompt: str) -> str:
    """input() on a daemon thread, so Ctrl-C never leaves the process stuck on it."""
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[str] = loop.create_future()

    def worker() -> None:
        try:
            value: str | BaseException = input(prompt)
        except BaseException as exc:  # noqa: BLE001 - handed to the event loop
            value = exc

        def deliver() -> None:
            if fut.done():
                return
            if isinstance(value, BaseException):
                fut.set_exception(value)
            else:
                fut.set_result(value)

        with suppress(RuntimeError):  # loop already closed
            loop.call_soon_threadsafe(deliver)

    threading.Thread(target=worker, name="bench-input", daemon=True).start()
    return await fut


class ConsoleOperator:
    assumed = False

    def say(self, text: str) -> None:
        print(text, flush=True)

    async def ask(self, question: str, choices: str = "ynr") -> str:
        legend = {"y": "y=yes", "n": "n=no", "r": "r=repeat"}
        hint = " / ".join(legend.get(c, c) for c in choices) + " / q=quit"
        while True:
            answer = (await self._line(f"{question} [{hint}] ")).strip().lower()[:1]
            if answer == "q":
                raise BenchAborted("operator quit")
            if answer and answer in choices:
                return answer

    async def ask_text(self, question: str, default: str) -> str:
        answer = (await self._line(f"{question} ")).strip()
        if answer.lower() == "q":
            raise BenchAborted("operator quit")
        return answer or default

    async def _line(self, prompt: str) -> str:
        try:
            return await _read_line(prompt)
        except EOFError as exc:
            raise BenchAborted("no operator input (stdin closed); use --yes for a scripted run") from exc


class AutoOperator:
    """--yes: every question is answered 'yes' without asking. Checks that need a
    person's observation are then reported as UNCONFIRMED, never PASS."""

    assumed = True

    def __init__(self, echo: Callable[[str], None] | None = print) -> None:
        self._echo = echo

    def say(self, text: str) -> None:
        if self._echo:
            self._echo(text)

    async def ask(self, question: str, choices: str = "ynr") -> str:
        self.say(f"{question} -> y (assumed, --yes)")
        return "y"

    async def ask_text(self, question: str, default: str) -> str:
        self.say(f"{question} -> {default!r} (assumed, --yes)")
        return default


# ---------------------------------------------------------------- settings


def _hub_haptics() -> HapticsConfig:
    return HapticsConfig()


@dataclass
class BenchSettings:
    motors: int = 5
    level: int = 200  # buzz level for the motor map / TTL / disconnect checks
    buzz_s: float = 1.0
    gap_s: float = 0.4
    ramp: tuple[int, ...] = (0, 30, 60, 90, 120, 150, 180, 210, 255)
    ramp_motor: int | None = None  # default: the centre motor
    ramp_step_s: float = 1.0
    ttls_ms: tuple[int, ...] = (250, 1000)
    ttl_tolerance_ms: float = 250.0  # allowed lateness of the observed link-lost (BLE + serial latency)
    ttl_hold_s: float = 1.0
    observe_s: float = 2.5  # after STOP / DISCONNECTED: long enough for two fault double-pulses
    fault_s: float = 4.5  # mode=fault heartbeats: a bit over two 2 s fault periods
    fault_applied_ms: float = 500.0  # with BLE status: the belt must report mode fault this soon
    writes: int = 100
    send_hz: float = field(default_factory=lambda: _hub_haptics().send_hz)
    ttl_ms: int = field(default_factory=lambda: _hub_haptics().ttl_ms)  # TTL on every heartbeat
    hub_min_level: int = field(default_factory=lambda: _hub_haptics().min_level)
    scan_timeout_s: float = 10.0
    only: frozenset[str] | None = None  # None = every check

    def __post_init__(self) -> None:
        if not 1 <= self.motors <= 8:
            raise ValueError("motors must be 1..8")
        for lv in (self.level, *self.ramp):
            if not 0 <= lv <= 255:
                raise ValueError(f"level {lv} outside 0..255")
        if self.ramp_motor is not None and not 0 <= self.ramp_motor < self.motors:
            raise ValueError(f"ramp motor must be 0..{self.motors - 1}")
        if any(not 0 < t <= 0xFFFF for t in self.ttls_ms):
            raise ValueError("TTLs must be 1..65535 ms")
        if self.writes < 1:
            raise ValueError("writes must be >= 1")
        if self.only is not None:
            unknown = set(self.only) - set(SELECTABLE)
            if unknown:
                raise ValueError(f"unknown check(s): {', '.join(sorted(unknown))}; choose from {', '.join(SELECTABLE)}")

    @property
    def centre(self) -> int:
        return self.motors // 2

    def wants(self, name: str) -> bool:
        return self.only is None or name in self.only


# ---------------------------------------------------------------- the bench


@dataclass
class HoldResult:
    writes: int
    last_write_done: float | None  # time.monotonic() when the last write returned


class Bench:
    def __init__(self, link: Any, operator: Operator, settings: BenchSettings, observer: EventLog | None = None) -> None:
        self.link = link
        self.op = operator
        self.s = settings
        self.observer = observer
        self.results: list[CheckResult] = []
        self.connection: ConnectInfo | None = None
        self.aborted: str | None = None
        self._last_picture: str | None = None

    # ------------------------------------------------------------ helpers

    def _one_hot(self, motor: int, level: int, ttl_ms: int | None = None) -> BeltCommand:
        levels = tuple(level if i == motor else 0 for i in range(self.s.motors))
        mode = BeltMode.LEVELS if level else BeltMode.OFF
        return BeltCommand(mode, levels, self.s.ttl_ms if ttl_ms is None else ttl_ms)

    def _off(self) -> BeltCommand:
        return BeltCommand.off(self.s.motors, self.s.ttl_ms)

    def _show(self, cmd: BeltCommand) -> None:
        if self.link.simulated:
            picture = render(cmd)
            if picture != self._last_picture:
                self.op.say(f"    (sim belt {picture})")
                self._last_picture = picture

    async def hold(self, cmd: BeltCommand, seconds: float) -> HoldResult:
        """Re-send `cmd` at the hub's heartbeat rate for `seconds` (at least once)."""
        self._show(cmd)
        period = 1.0 / self.s.send_hz
        n = max(1, round(seconds * self.s.send_hz))
        next_at = time.monotonic()
        last = None
        for _ in range(n):
            await self.link.write(cmd)
            last = time.monotonic()
            next_at += period
            await asyncio.sleep(max(0.0, next_at - time.monotonic()))
        return HoldResult(n, last)

    @asynccontextmanager
    async def keepalive(self):
        """Send `off` heartbeats while waiting on the operator, so the belt stays
        quiet instead of timing out into the fault pattern mid-question."""

        async def loop() -> None:
            off = self._off()
            while True:
                if self.link.is_connected:
                    with suppress(BenchLinkError):
                        await self.link.write(off)
                await asyncio.sleep(1.0 / self.s.send_hz)

        task = asyncio.create_task(loop())
        self._last_picture = None
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def confirm(self, question: str, stimulus: Callable[[], Awaitable[None]]) -> tuple[str, int]:
        """Run the stimulus, ask y/n/r; 'r' runs it again. Returns (answer, attempts)."""
        attempts = 0
        while True:
            await stimulus()
            attempts += 1
            async with self.keepalive():
                answer = await self.op.ask(question)
            if answer != "r":
                return answer, attempts

    def _human_status(self, answer: str) -> tuple[str, str]:
        if answer == "n":
            return FAIL, "operator"
        if self.op.assumed:
            return UNCONFIRMED, "operator (assumed: --yes)"
        return PASS, "operator"

    def _observer_label(self) -> str:
        return self.observer.source if self.observer else "none"

    def _status_log(self) -> EventLog | None:
        """The belt's BLE status notifications as "mode" events (None: older firmware)."""
        return getattr(self.link, "status_log", None)

    def _evidence_label(self) -> str:
        sources = [log.source for log in (self.observer, self._status_log()) if log is not None]
        return " + ".join(sources) if sources else "none"

    def _log_since(self, mark: int, t0: float) -> None:
        if self.observer is None:
            return
        for ev in self.observer.since(mark):
            if ev.kind != "other":
                self.op.say(f"    belt log +{(ev.t - t0) * 1000:5.0f} ms  {ev.line}")

    # ------------------------------------------------------------ checks

    async def check_connect(self) -> CheckResult:
        self.op.say(f"Looking for the belt (up to {self.s.scan_timeout_s:.0f} s) ...")
        try:
            info = await self.link.connect(self.s.scan_timeout_s)
        except BenchLinkError as exc:
            return CheckResult("connect", FAIL, str(exc))
        self.connection = info
        data = {k: v for k, v in vars(info).items()}
        problems = []
        if info.protocol_version != BELT_PROTOCOL_VERSION:
            problems.append(f"belt speaks protocol v{info.protocol_version}, hub v{BELT_PROTOCOL_VERSION}")
        if info.motor_count != self.s.motors:
            problems.append(f"belt reports {info.motor_count} motors, bench expects {self.s.motors}")
        rssi = f"rssi {info.rssi} dBm" if info.rssi is not None else "rssi n/a"
        scan = f"scan {info.scan_s:.2f} s, " if info.scan_s is not None else ""
        status = "status notify" if info.status else "no status characteristic (older firmware)"
        summary = (f"{info.name or '?'} {info.address}, {rssi}, {scan}connect {info.connect_s:.2f} s, v{info.protocol_version}, "
                   f"{info.motor_count} motors, build {info.build or 'unknown'}, {status}")  # fmt: skip
        if problems:
            return CheckResult("connect", FAIL, "; ".join(problems), data=data, notes=[summary])
        return CheckResult("connect", PASS, summary, data=data)

    async def check_motor_map(self) -> CheckResult:
        names = position_names(self.s.motors)
        self.op.say(f"\nMOTOR MAP: each motor buzzes alone for {self.s.buzz_s:.1f} s, left to right. Hold or wear the belt on the bench.")
        entries = []
        for i in range(self.s.motors):

            async def stimulus(i: int = i) -> None:
                self.op.say(f"  buzzing motor {i} (M{i}), expected at the {names[i]} ...")
                await self.hold(self._one_hot(i, self.s.level), self.s.buzz_s)
                await self.hold(self._off(), self.s.gap_s)

            answer, attempts = await self.confirm(f"  Motor {i}: did you feel it at the {names[i]}?", stimulus)
            felt: int | None = i
            if answer == "n":
                async with self.keepalive():
                    raw = await self.op.ask_text(
                        f"  Where did you feel motor {i}? Position 0-{self.s.motors - 1} (0 = far left), or '-' for nothing:", "-"
                    )
                felt = int(raw) if raw.isdigit() and int(raw) < self.s.motors else None
            entries.append({"motor": i, "expected": names[i], "answer": answer, "felt_at": felt, "attempts": attempts})

        mismatches = [e for e in entries if e["answer"] == "n"]
        data: dict[str, Any] = {"level": self.s.level, "buzz_s": self.s.buzz_s, "motors": entries}
        notes = []
        if mismatches:
            felt_all = [e["felt_at"] for e in entries]
            if None not in felt_all and sorted(felt_all) == list(range(self.s.motors)):
                order = [0] * self.s.motors
                for e in entries:
                    order[e["felt_at"]] = e["motor"]
                data["suggested_index_order"] = order
                notes.append(
                    f"fix: in esp32/include/config.h list kMotorPins in this order of current indices {order}"
                    + (
                        f", i.e. {{{', '.join(str(DEFAULT_FIRMWARE_PINS[j]) for j in order)}}} if it still has the default pins"
                        if self.s.motors == len(DEFAULT_FIRMWARE_PINS)
                        else ""
                    )
                    + ", or swap the motor wires"
                )
            silent = [e["motor"] for e in mismatches if e["felt_at"] is None]
            if silent:
                notes.append(f"not felt at all: motor(s) {silent}; check wiring/driver, try `m <i>` on the serial console")
            parts = [f"M{e['motor']} felt at {e['felt_at'] if e['felt_at'] is not None else 'nowhere'}" for e in mismatches]
            return CheckResult("motor-map", FAIL, f"{len(mismatches)} mismatch(es): " + ", ".join(parts), "operator", data, notes)
        status, by = self._human_status("y")
        return CheckResult("motor-map", status, f"all {self.s.motors} motors at their expected positions", by, data)

    async def check_levels(self) -> CheckResult:
        motor = self.s.centre if self.s.ramp_motor is None else self.s.ramp_motor
        self.op.say(f"\nLEVELS: motor {motor} steps through {list(self.s.ramp)} ({self.s.ramp_step_s:.1f} s each; 0 = off).")

        async def stimulus() -> None:
            for lv in self.s.ramp:
                self.op.say(f"  level {lv}")
                await self.hold(self._one_hot(motor, lv), self.s.ramp_step_s)
            await self.hold(self._off(), self.s.gap_s)

        answer, attempts = await self.confirm("  Did it get stronger at each step (except where too weak to feel)?", stimulus)
        async with self.keepalive():
            raw = await self.op.ask_text("  Lowest level you could feel (a number from the list, '-' for none)?", "-")
        lowest = int(raw) if raw.isdigit() else None
        data = {"motor": motor, "ramp": list(self.s.ramp), "answer": answer, "lowest_felt": lowest, "attempts": attempts,
                "hub_min_level": self.s.hub_min_level}
        notes = ["the firmware caps duty at kMaxDuty (esp32/include/config.h); levels above it feel the same"]
        status, by = self._human_status(answer)
        if answer == "n":
            return CheckResult("levels", FAIL, "intensity did not rise with the level", by, data, notes)
        if lowest is not None and lowest > self.s.hub_min_level:
            notes.append(f"raise HapticsConfig.min_level in hub/haptic_hub/config.py to at least {lowest}")
            return CheckResult(
                "levels", FAIL, f"weakest felt level {lowest} is above the hub's weakest alert level {self.s.hub_min_level}",
                by, data, notes,
            )
        felt = f"lowest felt {lowest}" if lowest is not None else "lowest felt not recorded"
        return CheckResult("levels", status, f"ramp {self.s.ramp[0]}..{self.s.ramp[-1]} on motor {motor}, {felt}", by, data, notes)

    async def check_ttl(self) -> CheckResult:
        self.op.say("\nTTL FAILSAFE: the centre motor buzzes, then the hub stops sending. The belt must stop by itself.")
        runs = []
        for ttl in self.s.ttls_ms:
            eff = effective_ttl_ms(ttl)
            cmd = self._one_hot(self.s.centre, self.s.level, ttl_ms=ttl)
            measured: list[float | None] = []
            measured_status: list[float | None] = []

            async def stimulus(ttl: int = ttl, eff: int = eff, cmd: BeltCommand = cmd, measured: list = measured,
                               measured_status: list = measured_status) -> None:  # fmt: skip
                clamp = f" (firmware clamps it to {eff} ms)" if eff != ttl else ""
                self.op.say(f"  TTL {ttl} ms{clamp}: buzzing for {self.s.ttl_hold_s:.1f} s, then STOP. Expect it to stop ~{eff} ms after STOP, then the fault double-pulse.")
                result = await self.hold(cmd, self.s.ttl_hold_s)
                t_stop = result.last_write_done or time.monotonic()
                mark = self.observer.mark() if self.observer else 0
                status = self._status_log()
                smark = status.mark() if status else 0
                self.op.say("  STOP (hub silent)")
                self._last_picture = None
                ms = None
                if self.observer is not None:
                    ev = await self.observer.wait_for("state", "link-lost", since=mark, timeout=eff / 1000 + 1.5)
                    ms = None if ev is None else (ev.t - t_stop) * 1000
                    self._log_since(mark, t_stop)
                measured.append(ms)
                if status is not None:  # event times are arrival times, so waiting after the serial log is fine
                    ev = await status.wait_for("mode", "expired", since=smark, timeout=eff / 1000 + 1.5)
                    measured_status.append(None if ev is None else (ev.t - t_stop) * 1000)
                    for e in status.since(smark):
                        self.op.say(f"    status  +{(e.t - t_stop) * 1000:5.0f} ms  {e.line}")
                remaining = t_stop + max(self.s.observe_s, eff / 1000 + 0.3) - time.monotonic()
                if remaining > 0:
                    await asyncio.sleep(remaining)

            answer, attempts = await self.confirm(
                f"  Did the motor stop about {eff / 1000:.2g} s after STOP, then the fault double-pulse start?", stimulus
            )
            ms = measured[-1]
            sms = measured_status[-1] if measured_status else None
            timed = []
            if self.observer is not None:
                timed.append(ms)
            if self._status_log() is not None:
                timed.append(sms)
            auto = None
            if timed:
                auto = all(m is not None and eff - 25 <= m <= eff + self.s.ttl_tolerance_ms for m in timed)
            runs.append({
                "ttl_ms": ttl, "effective_ttl_ms": eff, "answer": answer, "attempts": attempts,
                "observed_link_lost_ms": None if ms is None else round(ms, 1),
                "all_observed_ms": [None if m is None else round(m, 1) for m in measured],
                "status_expired_ms": None if sms is None else round(sms, 1),
                "all_status_expired_ms": [None if m is None else round(m, 1) for m in measured_status],
                "auto_pass": auto,
            })

        observed = self.observer is not None or self._status_log() is not None
        data = {"runs": runs, "tolerance_ms": self.s.ttl_tolerance_ms, "observer": self._evidence_label(),
                "measured_as": "hub's last write returning -> belt logging 'state -> link-lost' (serial) and/or the belt's "
                               "status notification 'mode -> expired' arriving (BLE); includes BLE delivery and serial latency"}  # fmt: skip
        notes = []
        if not observed:
            notes.append("no observer: expiry not timed. This firmware has no BLE status characteristic; pass --serial PORT "
                         "to time it from the serial log")  # fmt: skip
        human_fail = any(r["answer"] == "n" for r in runs)
        auto_fail = any(r["auto_pass"] is False for r in runs)
        parts = []
        for r in runs:
            if self.observer is not None or not observed:
                v = r["observed_link_lost_ms"]
                obs = f"{v:.0f} ms" if v is not None else ("not seen" if self.observer else "not timed")
            else:
                v = r["status_expired_ms"]
                obs = f"{v:.0f} ms (status)" if v is not None else "not seen in status"
            parts.append(f"{r['ttl_ms']}->{obs}")
        summary = "TTL " + ", ".join(parts)
        if auto_fail or human_fail:
            why = []
            if auto_fail:
                why.append(f"link-lost / expired not reported within {self.s.ttl_tolerance_ms:.0f} ms of the TTL")
            if human_fail:
                why.append("operator: motor did not stop / no fault pattern")
            return CheckResult("ttl", FAIL, summary + "; " + "; ".join(why), "operator + " + self._evidence_label(), data, notes)
        if observed:
            by = self._evidence_label() + (" (operator assumed: --yes)" if self.op.assumed else " + operator")
            return CheckResult("ttl", PASS, summary, by, data, notes)
        status, by = self._human_status("y")
        return CheckResult("ttl", status, summary, by, data, notes)

    async def check_write_timing(self) -> CheckResult:
        n = self.s.writes
        self.op.say(f"\nBLE WRITE TIMING: {n} 'off' writes at {self.s.send_hz:g} Hz, without and with response. Silent.")
        off = self._off()
        period = 1.0 / self.s.send_hz
        data: dict[str, Any] = {
            "writes_per_mode": n, "send_hz": self.s.send_hz,
            "measured_as": "time for the BLE write call to return on the hub; NOT motor latency",
            "without_response_means": "handed to the OS Bluetooth stack (what the hub uses)",
            "with_response_means": "the belt's GATT server acknowledged the write",
        }
        mark = self.observer.mark() if self.observer else 0
        ok = True
        summaries = []
        for response in (False, True):
            times: list[float] = []
            failures = 0
            next_at = time.monotonic()
            for _ in range(n):
                started = time.perf_counter()
                try:
                    await self.link.write(off, response=response)
                    times.append((time.perf_counter() - started) * 1000)
                except BenchLinkError:
                    failures += 1
                    if not self.link.is_connected:
                        break
                next_at += period
                await asyncio.sleep(max(0.0, next_at - time.monotonic()))
            stats = timing_stats(times)
            stats["failures"] = failures
            key = "with_response" if response else "without_response"
            data[key] = stats
            gate_ok = failures == 0 and stats["p90_ms"] is not None and stats["p90_ms"] <= period * 1000
            ok = ok and gate_ok
            label = "resp" if response else "no-resp"
            if stats["n"]:
                summaries.append(f"{label} median {stats['median_ms']:.1f} / p90 {stats['p90_ms']:.1f} ms ({failures} failed)")
            else:
                summaries.append(f"{label}: no successful writes ({failures} failed)")
        rejected = [e.line for e in self.observer.since(mark) if e.kind == "rejected"] if self.observer else []
        data["rejected_by_belt"] = rejected
        data["pass_rule"] = f"no failed writes and p90 <= one heartbeat ({period * 1000:.1f} ms); a bench rule from send_hz, not a validated requirement"
        summary = "write time " + "; ".join(summaries)
        if rejected:
            ok = False
            summary += f"; belt rejected {len(rejected)} command(s)"
        return CheckResult("write-timing", PASS if ok else FAIL, summary, "automatic", data)

    async def check_fault_command(self) -> CheckResult:
        self.op.say(f"\nFAULT PATTERN (command): the hub sends mode=fault for {self.s.fault_s:.1f} s, as it does when it can't vouch for itself.")

        status_log = self._status_log()
        applied: list[float | None] = []

        async def stimulus() -> None:
            smark = status_log.mark() if status_log else 0
            t0 = time.monotonic()
            await self.hold(BeltCommand.fault(self.s.motors, self.s.ttl_ms), self.s.fault_s)
            if status_log is not None:
                ev = next((e for e in status_log.since(smark) if e.kind == "mode" and e.value == "fault"), None)
                applied.append(None if ev is None else (ev.t - t0) * 1000)
                for e in status_log.since(smark):
                    self.op.say(f"    status  +{(e.t - t0) * 1000:5.0f} ms  {e.line}")
            await self.hold(self._off(), self.s.gap_s)

        answer, attempts = await self.confirm("  Did ALL motors double-pulse, about every 2 s?", stimulus)
        status, by = self._human_status(answer)
        data: dict[str, Any] = {"seconds": self.s.fault_s, "answer": answer, "attempts": attempts}
        if status_log is None:
            notes = ["this firmware has no BLE status characteristic, and its serial log shows a fault command only from "
                     "2026-11-08 on ('[belt] mode -> fault'), so this one is operator-only"]  # fmt: skip
            summary = "double pulse on all motors" if answer == "y" else "operator did not feel the fault pattern"
            return CheckResult("fault-command", status, summary, by, data, notes)
        ms = applied[-1] if applied else None
        data |= {"status_fault_ms": None if ms is None else round(ms, 1), "within_ms": self.s.fault_applied_ms,
                 "measured_as": "first fault write -> the belt's status notification 'mode -> fault' arriving"}  # fmt: skip
        notes = ["the status shows the firmware applied the fault command; only the operator can say the pattern was felt"]
        seen = ms is not None and ms <= self.s.fault_applied_ms
        by = "operator + " + status_log.source if not self.op.assumed else f"{status_log.source} + operator (assumed: --yes)"
        if not seen:
            why = "belt status never reported mode fault" if ms is None else f"belt reported mode fault after {ms:.0f} ms"
            return CheckResult("fault-command", FAIL, why + ("; operator did not feel it" if answer == "n" else ""), by, data, notes)
        felt = "double pulse on all motors" if answer == "y" else "operator did not feel the fault pattern"
        return CheckResult("fault-command", status, f"{felt}; belt reported mode fault after {ms:.0f} ms", by, data, notes)

    async def check_fault_disconnect(self) -> CheckResult:
        self.op.say("\nFAULT PATTERN (link lost): the centre motor buzzes, then the hub drops BLE without a goodbye (like a crash or walking out of range).")
        measured: list[dict[str, Any]] = []

        async def stimulus() -> None:
            if not self.link.is_connected:
                self.op.say("  reconnecting first ...")
                await self.link.connect(self.s.scan_timeout_s)
            await self.hold(self._one_hot(self.s.centre, self.s.level), self.s.ttl_hold_s)
            mark = self.observer.mark() if self.observer else 0
            t0 = time.monotonic()
            await self.link.disconnect()
            call_ms = (time.monotonic() - t0) * 1000
            self.op.say("  DISCONNECTED")
            self._last_picture = None
            entry: dict[str, Any] = {"disconnect_call_ms": round(call_ms, 1)}
            if self.observer is not None:
                d = await self.observer.wait_for("disconnected", since=mark, timeout=3.0)
                lost = await self.observer.wait_for("state", "link-lost", since=mark, timeout=0.5 if d else 0.1)
                entry["belt_saw_disconnect_ms"] = None if d is None else round((d.t - t0) * 1000, 1)
                entry["link_lost_ms"] = None if lost is None else round((lost.t - t0) * 1000, 1)
                self._log_since(mark, t0)
            measured.append(entry)
            remaining = t0 + self.s.observe_s - time.monotonic()
            if remaining > 0:
                await asyncio.sleep(remaining)

        answer, attempts = await self.confirm(
            "  After DISCONNECTED, did the motor stop and ALL motors start the double pulse?", stimulus
        )
        last = measured[-1]
        data = {"answer": answer, "attempts": attempts, "observer": self._observer_label(), **last, "all": measured}
        notes = ["the belt keeps playing the fault pattern for 30 s after a lost link, then goes silent; that is by design"]
        auto_ok = None
        if self.observer is not None:
            auto_ok = last.get("belt_saw_disconnect_ms") is not None and last.get("link_lost_ms") is not None
        if answer == "n" or auto_ok is False:
            why = "operator: no stop / fault pattern" if answer == "n" else "belt log never showed disconnect + link-lost"
            return CheckResult("fault-disconnect", FAIL, why, "operator + " + self._observer_label(), data, notes)
        if auto_ok:
            by = self._observer_label() + (" (operator assumed: --yes)" if self.op.assumed else " + operator")
            return CheckResult("fault-disconnect", PASS,
                               f"belt saw the drop after {last['belt_saw_disconnect_ms']:.0f} ms, link-lost at {last['link_lost_ms']:.0f} ms",
                               by, data, notes)
        status, by = self._human_status(answer)
        return CheckResult("fault-disconnect", status, "stopped and played the fault pattern", by, data, notes)

    async def check_reconnect(self) -> CheckResult:
        if self.link.is_connected:
            return CheckResult("reconnect", SKIPPED, "link was not dropped")
        self.op.say("\nRECONNECT: the belt should advertise again after a drop, so a restarted hub can find it.")
        t0 = time.monotonic()
        status_log = self._status_log()
        smark = status_log.mark() if status_log else 0
        try:
            info = await self.link.connect(self.s.scan_timeout_s)
            total = time.monotonic() - t0
            await self.hold(self._off(), 0.5)  # quiets the fault pattern the drop started
        except BenchLinkError as exc:
            return CheckResult("reconnect", FAIL, f"could not reconnect: {exc}")
        data = {"total_s": round(total, 3), "scan_s": info.scan_s, "connect_s": info.connect_s, "rssi": info.rssi}
        summary = f"reconnected in {total:.2f} s"
        status_log = self._status_log()  # the link may only now have one
        if status_log is not None:
            first = next((e.value for e in status_log.since(smark) if e.kind == "mode"), None)
            data["status_mode_on_reconnect"] = first  # expected: "disconnected" (the drop) or "dormant"
            if first is not None:
                summary += f"; belt status on reconnect: {first}"
        return CheckResult("reconnect", PASS, summary, "automatic", data)

    def check_belt_log(self, settle_until: float | None) -> CheckResult:
        assert self.observer is not None
        events = self.observer.events
        rejected = [e.line for e in events if e.kind == "rejected"]
        reboots = [e.line for e in events if e.kind == "boot" and (settle_until is None or e.t > settle_until)]
        errors = [e.line for e in events if e.kind == "error"]
        states = [e.value for e in events if e.kind == "state"]
        data = {"source": self.observer.source, "lines": len(events), "rejected": rejected, "reboots_during_run": reboots,
                "errors": errors, "state_changes": states}
        problems = []
        if rejected:
            problems.append(f"{len(rejected)} rejected command(s)")
        if reboots:
            problems.append(f"belt rebooted {len(reboots)}x during the run (brownout when motors start?)")
        if errors:
            problems.append(f"log read error: {errors[0]}")
        if problems:
            return CheckResult("belt-log", FAIL, "; ".join(problems), self.observer.source, data)
        return CheckResult("belt-log", PASS, f"{len(events)} lines, no rejected commands, no reboots", self.observer.source, data)

    # ------------------------------------------------------------ run

    async def run(self, settle_until: float | None = None) -> list[CheckResult]:
        steps: list[tuple[str, Callable[[], Awaitable[CheckResult]]]] = [
            ("motor-map", self.check_motor_map),
            ("levels", self.check_levels),
            ("ttl", self.check_ttl),
            ("write-timing", self.check_write_timing),
            ("fault-command", self.check_fault_command),
            ("fault-disconnect", self.check_fault_disconnect),
        ]
        try:
            connect = await self.check_connect()
            self.results.append(connect)
            if connect.status != PASS:
                for name, _ in steps:
                    if self.s.wants(name):
                        self.results.append(CheckResult(name, SKIPPED, "needs a working connection"))
                return self.results
            dropped = False
            for name, step in steps:
                if not self.s.wants(name):
                    continue
                if not self.link.is_connected:
                    try:
                        await self.link.connect(self.s.scan_timeout_s)
                    except BenchLinkError as exc:
                        self.results.append(CheckResult(name, SKIPPED, f"belt link lost and reconnect failed: {exc}"))
                        continue
                try:
                    self.results.append(await step())
                except BenchLinkError as exc:
                    self.results.append(CheckResult(name, ERROR, f"BLE error mid-check: {exc}"))
                if name == "fault-disconnect":
                    dropped = True
            if dropped:
                self.results.append(await self.check_reconnect())
        except BenchAborted as exc:
            self.aborted = str(exc)
        except asyncio.CancelledError:
            self.aborted = "interrupted (Ctrl-C)"
        finally:
            if self.aborted:
                done = {r.name for r in self.results}
                for name in ("connect", *[n for n, _ in steps]):
                    if name not in done and (name == "connect" or self.s.wants(name)):
                        self.results.append(CheckResult(name, SKIPPED, f"not run: {self.aborted}"))
            with suppress(Exception):
                await asyncio.shield(self.link.close())
            if self.observer is not None and self.connection is not None:
                await asyncio.sleep(0)  # let queued log lines land
                self.results.append(self.check_belt_log(settle_until))
        return self.results
