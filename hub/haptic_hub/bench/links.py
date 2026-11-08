"""How the bench reaches a belt.

`BleBenchLink` uses the same bleak client, UUIDs, belt filter and info check as
`belt/ble.py`, but without its background reconnect loop: the bench needs to
drop and re-open the link on purpose and time each write.

`--belt sim` uses `FirmwareSimLink` when a C++ compiler is available: the real
esp32/lib/belt_core decoder and TTL/fault state machine, compiled for the host
by `haptic_hub.sim.firmware`, ticked every 5 ms like main.cpp's loop, reporting
the same state-change lines the serial log prints, and the same status reports
the firmware notifies over BLE. Without a compiler it falls
back to `SimBenchLink`: `SimBelt` plus a small Python copy of the TTL rules
(docs/protocol.md "Belt failsafe behavior"). Both exercise the bench, not the
board: a pass against either says nothing about the real belt.

Firmware with the status characteristic (docs/protocol.md section 3, "Status")
gets a `status_log`: an EventLog of its mode changes ("mode" events, e.g.
`expired`, `fault`), which lets the bench time TTL expiry and see a fault command
applied without the serial log. Older firmware (and SimBenchLink) has none.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.exc import BleakError

from haptic_hub.belt.ble import has_status, is_belt
from haptic_hub.belt.sim import SimBelt
from haptic_hub.protocol import (
    BELT_COMMAND_UUID,
    BELT_DEVICE_NAME,
    BELT_INFO_UUID,
    BELT_PROTOCOL_VERSION,
    BELT_STATUS_UUID,
    BeltCommand,
    BeltMode,
    BeltStatus,
    ProtocolError,
    decode_belt_info,
    decode_belt_status,
    encode_belt_command,
)

# Firmware TTL clamp (docs/protocol.md section 3, HapticTiming in belt_core).
FIRMWARE_MIN_TTL_MS = 50
FIRMWARE_MAX_TTL_MS = 1000
# How long the firmware plays the fault pattern after a lost link before going quiet.
FIRMWARE_LINK_LOST_ALERT_S = 30.0


def effective_ttl_ms(ttl_ms: int) -> int:
    """The TTL the firmware actually applies to a command."""
    return min(max(ttl_ms, FIRMWARE_MIN_TTL_MS), FIRMWARE_MAX_TTL_MS)


class BenchLinkError(RuntimeError):
    """The belt couldn't be found, connected, read or written."""


@dataclass
class ConnectInfo:
    address: str
    name: str | None
    rssi: int | None  # dBm, from the advertisement; None if it wasn't seen
    scan_s: float | None  # None when no scan was needed
    connect_s: float  # connect + info read
    info_hex: str
    protocol_version: int
    motor_count: int
    mtu: int | None = None
    build: str | None = None  # firmware build id from the info characteristic (None: older firmware)
    status: bool = False  # the firmware notifies its status (docs/protocol.md "Status")


# ---------------------------------------------------------------- belt events


@dataclass(frozen=True)
class ObservedEvent:
    t: float  # hub time.monotonic() when the event was seen
    kind: str  # state | connected | disconnected | rejected | boot | error | other
    value: str | None
    line: str


@dataclass
class EventLog:
    """Events the belt reported (serial log) or the firmware model produced.
    `wait_for` lets a check wait for one, e.g. the state going to link-lost."""

    source: str
    events: list[ObservedEvent] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._wake = asyncio.Event()

    def add(self, event: ObservedEvent) -> None:
        self.events.append(event)
        wake, self._wake = self._wake, asyncio.Event()
        wake.set()

    def mark(self) -> int:
        return len(self.events)

    def since(self, mark: int) -> list[ObservedEvent]:
        return self.events[mark:]

    async def wait_for(self, kind: str, value: str | None = None, since: int = 0, timeout: float = 1.0) -> ObservedEvent | None:
        deadline = time.monotonic() + timeout
        checked = since
        while True:
            for event in self.events[checked:]:
                if event.kind == kind and (value is None or event.value == value):
                    return event
            checked = len(self.events)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                await asyncio.wait_for(self._wake.wait(), remaining)
            except TimeoutError:
                return None

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


class StatusEvents:
    """Turns belt status reports into "mode" events (one per mode change, plus
    the first report after each connect) in an EventLog."""

    def __init__(self, source: str) -> None:
        self.log = EventLog(source)
        self.last: BeltStatus | None = None
        self.reports = 0
        self.malformed = 0

    def new_connection(self) -> None:
        self.last = None

    def on_payload(self, data: bytes | bytearray) -> None:
        try:
            st = decode_belt_status(bytes(data))
        except ProtocolError:
            self.malformed += 1
            return
        self.reports += 1
        if self.last is None or st.mode != self.last.mode:
            name = st.mode.name.lower()
            self.log.add(ObservedEvent(time.monotonic(), "mode", name, f"[status] mode -> {name} (seq {st.seq}, max duty {st.max_duty})"))
        self.last = st


# ---------------------------------------------------------------- real belt


class BleBenchLink:
    kind = "ble"
    simulated = False
    DISCONNECT_TIMEOUT_S = 5.0

    def __init__(self, motors: int, address: str | None = None) -> None:
        self.motors = motors
        self.address = address
        self.observer: EventLog | None = None  # set by the caller (e.g. --serial)
        self._client: BleakClient | None = None
        self._seq = 0
        self._status = StatusEvents("belt BLE status notifications")
        self.subscribe_error: str | None = None
        self.status_log: EventLog | None = None  # set on connect if the firmware has the status characteristic

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    async def connect(self, timeout: float = 10.0) -> ConnectInfo:
        seen: dict[str, AdvertisementData] = {}

        def match(device: BLEDevice, adv: AdvertisementData) -> bool:
            if self.address is not None:
                ok = device.address.lower() == self.address.lower()
            else:
                ok = is_belt(device, adv)
            if ok:
                seen["adv"] = adv
            return ok

        started = time.monotonic()
        device = await BleakScanner.find_device_by_filter(match, timeout=timeout)
        scan_s = time.monotonic() - started
        if device is None:
            what = self.address or f"a belt advertising as {BELT_DEVICE_NAME}"
            raise BenchLinkError(f"no {what} found in {timeout:.0f} s; is it powered, and not connected to another hub?")
        adv = seen.get("adv")

        client = BleakClient(device, disconnected_callback=lambda _: None)
        started = time.monotonic()
        try:
            await asyncio.wait_for(client.connect(), timeout)
            info = bytes(await client.read_gatt_char(BELT_INFO_UUID))
        except BaseException as exc:
            # The belt stops advertising while connected; never leave it half-open.
            await self._disconnect_quietly(client)
            if isinstance(exc, (BleakError, OSError, TimeoutError)):
                raise BenchLinkError(f"connect/info read failed: {str(exc) or type(exc).__name__}") from exc
            raise
        connect_s = time.monotonic() - started
        if len(info) < 2:
            await self._disconnect_quietly(client)
            raise BenchLinkError(f"belt info is {len(info)} bytes, expected [protocol_version, motor_count]")
        self._client = client
        self.status_log = None
        if has_status(client):
            self._status.new_connection()
            try:
                await client.start_notify(BELT_STATUS_UUID, lambda _sender, data: self._status.on_payload(data))
                self._status.on_payload(await client.read_gatt_char(BELT_STATUS_UUID))
                self.status_log = self._status.log
            except (BleakError, OSError) as exc:
                self.subscribe_error = str(exc) or type(exc).__name__  # noted in the report; the bench goes on without it
        mtu = getattr(client, "mtu_size", None)
        return ConnectInfo(
            address=str(getattr(client, "address", device.address)),
            name=(adv.local_name if adv else None) or getattr(device, "name", None),
            rssi=adv.rssi if adv is not None else None,
            scan_s=scan_s,
            connect_s=connect_s,
            info_hex=info.hex(" "),
            protocol_version=info[0],
            motor_count=info[1],
            mtu=mtu if isinstance(mtu, int) else None,
            build=decode_belt_info(info).build,
            status=self.status_log is not None,
        )

    async def write(self, cmd: BeltCommand, response: bool = False) -> None:
        client = self._client
        if client is None or not client.is_connected:
            raise BenchLinkError("belt not connected")
        self._seq = (self._seq + 1) & 0xFF
        try:
            await client.write_gatt_char(BELT_COMMAND_UUID, encode_belt_command(cmd, self._seq), response=response)
        except (BleakError, OSError) as exc:
            raise BenchLinkError(f"write failed: {exc}") from exc

    async def disconnect(self) -> None:
        """Drop the link without sending anything first (what a crash or range loss looks like)."""
        client, self._client = self._client, None
        if client is not None:
            await self._disconnect_quietly(client)

    async def close(self) -> None:
        if self.is_connected:
            try:
                await self.write(BeltCommand.off(self.motors, ttl_ms=FIRMWARE_MIN_TTL_MS))
            except BenchLinkError:
                pass
        await self.disconnect()

    async def _disconnect_quietly(self, client: BleakClient) -> None:
        try:
            await asyncio.wait_for(client.disconnect(), self.DISCONNECT_TIMEOUT_S)
        except Exception:  # noqa: BLE001 - best effort, the caller is already finishing or failing
            pass


# ---------------------------------------------------------------- simulator


class SimFirmware:
    """Firmware state machine timing, reported like the serial log would:
    idle -> active on a command; active -> link-lost when the (clamped) TTL lapses
    or the link drops; link-lost -> dormant after 30 s."""

    def __init__(self, log: EventLog, link_lost_alert_s: float = FIRMWARE_LINK_LOST_ALERT_S) -> None:
        self.log = log
        self.state = "idle"
        self.link_lost_alert_s = link_lost_alert_s
        self.last: BeltCommand | None = None
        self._ttl_timer: asyncio.TimerHandle | None = None
        self._dormant_timer: asyncio.TimerHandle | None = None

    def _emit(self, kind: str, value: str | None, line: str) -> None:
        self.log.add(ObservedEvent(time.monotonic(), kind, value, line))

    def _set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self._emit("state", state, f"[belt] state -> {state}")

    def on_connect(self) -> None:
        self._emit("connected", None, "[ble] hub connected")

    def on_command(self, cmd: BeltCommand) -> None:
        self.last = cmd
        self._cancel_timers()
        self._set_state("active")
        loop = asyncio.get_running_loop()
        self._ttl_timer = loop.call_later(effective_ttl_ms(cmd.ttl_ms) / 1000, self._link_lost)

    def on_disconnect(self) -> None:
        self._emit("disconnected", None, "[ble] hub disconnected")
        if self.state == "active":
            self._link_lost()

    def _link_lost(self) -> None:
        self._cancel_timers()
        self._set_state("link-lost")
        loop = asyncio.get_running_loop()
        self._dormant_timer = loop.call_later(self.link_lost_alert_s, self._set_state, "dormant")

    def _cancel_timers(self) -> None:
        for timer in (self._ttl_timer, self._dormant_timer):
            if timer is not None:
                timer.cancel()
        self._ttl_timer = self._dormant_timer = None

    def output(self) -> str:
        """What the wearer would feel right now, in words."""
        if self.state in ("idle", "dormant"):
            return "silent"
        if self.state == "link-lost" or (self.last is not None and self.last.mode == BeltMode.FAULT):
            return "fault pattern"
        if self.last is None or self.last.mode == BeltMode.OFF:
            return "silent"
        return "levels " + ",".join(str(lv) for lv in self.last.levels)

    def shutdown(self) -> None:
        self._cancel_timers()


class SimBenchLink:
    kind = "sim"
    simulated = True

    def __init__(self, motors: int, reported_motors: int | None = None, link_lost_alert_s: float = FIRMWARE_LINK_LOST_ALERT_S) -> None:
        self.motors = motors
        self.reported_motors = motors if reported_motors is None else reported_motors
        self.belt = SimBelt(motors, log_interval_s=float("inf"))  # the bench prints its own picture
        self.observer = EventLog("simulated firmware model (belt_core timing rules; not the real board)")
        self.firmware = SimFirmware(self.observer, link_lost_alert_s)
        self._connected = False
        self.writes = 0

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self, timeout: float = 10.0) -> ConnectInfo:
        started = time.monotonic()
        self._connected = True
        self.firmware.on_connect()
        info = bytes([BELT_PROTOCOL_VERSION, self.reported_motors])
        return ConnectInfo(
            address="sim",
            name=f"{BELT_DEVICE_NAME} (simulated)",
            rssi=None,
            scan_s=None,
            connect_s=time.monotonic() - started,
            info_hex=info.hex(" "),
            protocol_version=info[0],
            motor_count=info[1],
        )

    async def write(self, cmd: BeltCommand, response: bool = False) -> None:
        if not self._connected:
            raise BenchLinkError("belt not connected")
        await self.belt.send(cmd)  # encode -> decode round trip
        self.writes += 1
        self.firmware.on_command(self.belt.last)

    async def disconnect(self) -> None:
        if self._connected:
            self._connected = False
            self.firmware.on_disconnect()

    async def close(self) -> None:
        if self._connected:
            await self.write(BeltCommand.off(self.motors, ttl_ms=FIRMWARE_MIN_TTL_MS))
        await self.disconnect()
        self.firmware.shutdown()


# ---------------------------------------------------------------- simulator, real belt_core

_STATE_NAMES = {0: "idle", 1: "active", 2: "link-lost", 3: "dormant"}  # haptic_controller.cpp toString()
FIRMWARE_LOOP_S = 0.005  # esp32/src/main.cpp loop() delay


class FirmwareSimLink:
    """The bench's sim belt backed by the real belt_core logic. Raises
    haptic_hub.sim.firmware.FirmwareUnavailable if it can't be built."""

    kind = "sim"
    simulated = True

    def __init__(self, motors: int, reported_motors: int | None = None) -> None:
        from haptic_hub.sim.firmware import BeltFirmware

        self.fw = BeltFirmware(motors)
        self.motors = motors
        self.reported_motors = self.fw.motors if reported_motors is None else reported_motors
        self.observer = EventLog("esp32/lib/belt_core compiled for the host (real TTL/fault logic; no radio, motors or serial)")
        self._status = StatusEvents("belt_core status reports (host build; no radio)")
        self.status_log: EventLog | None = self._status.log
        self.writes = 0
        self.rejected = 0
        self.last: BeltCommand | None = None
        self._t0 = time.monotonic()
        self._state = "idle"
        self._connected = False
        self._seq = 0
        self._loop_task: asyncio.Task | None = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _now_ms(self) -> int:
        return int((time.monotonic() - self._t0) * 1000)

    def _emit(self, kind: str, value: str | None, line: str) -> None:
        self.observer.add(ObservedEvent(time.monotonic(), kind, value, line))

    def tick(self) -> None:
        now_ms = self._now_ms()
        state, _levels = self.fw.update(now_ms)
        name = _STATE_NAMES.get(int(state), str(int(state)))
        if name != self._state:
            self._state = name
            self._emit("state", name, f"[belt] state -> {name}")
        if self.fw.status_due(now_ms) and self._connected:  # what main.cpp notifies
            self._status.on_payload(self.fw.status(now_ms))

    async def _run(self) -> None:
        while True:
            self.tick()
            await asyncio.sleep(FIRMWARE_LOOP_S)

    async def connect(self, timeout: float = 10.0) -> ConnectInfo:
        started = time.monotonic()
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.create_task(self._run())
        self._connected = True
        self._emit("connected", None, "[ble] hub connected")
        self._status.new_connection()
        self._status.on_payload(self.fw.status(self._now_ms()))  # the status read on connect
        info = bytes([BELT_PROTOCOL_VERSION, self.reported_motors]) + b"sim"
        return ConnectInfo(
            address="sim",
            name=f"{BELT_DEVICE_NAME} (simulated, belt_core)",
            rssi=None,
            scan_s=None,
            connect_s=time.monotonic() - started,
            info_hex=info.hex(" "),
            protocol_version=info[0],
            motor_count=info[1],
            build="sim",
            status=True,
        )

    async def write(self, cmd: BeltCommand, response: bool = False) -> None:
        if not self._connected:
            raise BenchLinkError("belt not connected")
        self._seq = (self._seq + 1) & 0xFF
        payload = encode_belt_command(cmd, self._seq)
        err = self.fw.on_payload(payload, self._now_ms())
        self.writes += 1
        self.last = cmd
        if err != 0:
            self.rejected += 1
            self._emit("rejected", f"error {err}, {len(payload)} bytes", f"[ble] rejected command (error {err}, {len(payload)} bytes)")
        await asyncio.sleep(0)

    async def disconnect(self) -> None:
        if self._connected:
            self._connected = False
            self._emit("disconnected", None, "[ble] hub disconnected")
            self.fw.on_link_lost(self._now_ms())

    async def close(self) -> None:
        if self._connected:
            await self.write(BeltCommand.off(self.motors, ttl_ms=FIRMWARE_MIN_TTL_MS))
        await self.disconnect()
        self.tick()
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None


def make_sim_link(motors: int) -> tuple[FirmwareSimLink | SimBenchLink, str | None]:
    """The best available sim belt, and a warning if it had to fall back."""
    from haptic_hub.sim.firmware import FirmwareUnavailable

    try:
        return FirmwareSimLink(motors), None
    except FirmwareUnavailable as exc:
        first = str(exc).splitlines()[0]
        return SimBenchLink(motors), f"belt_core emulation unavailable ({first}); using the Python TTL model instead"
