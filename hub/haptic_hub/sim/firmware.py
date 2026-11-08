"""The belt firmware's logic (esp32/lib/belt_core) compiled for the host and loaded
with ctypes, plus a Belt adapter the hub can drive instead of BLE.

This exercises the real C++ command decoder and TTL/fault state machine. It does
not simulate the ESP32 radio, PWM, motors, power, or the boot self-test.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import hashlib
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path

from haptic_hub.protocol import BeltCommand

_HERE = Path(__file__).resolve().parent
_SHIM = _HERE / "belt_capi.cpp"
_CORE = _HERE.parents[2] / "esp32" / "lib" / "belt_core" / "src"
_SOURCES = ("belt_protocol.cpp", "haptic_controller.cpp")
_HEADERS = ("belt_protocol.h", "haptic_controller.h")
_BUILD_DIR = _HERE.parents[1] / ".sim-build"

FIRMWARE_LOOP_MS = 5  # esp32/src/main.cpp loop() delay


class FirmwareUnavailable(RuntimeError):
    """No C++ compiler, or the esp32/ sources aren't next to the hub."""


class FirmwareState(IntEnum):
    IDLE = 0
    ACTIVE = 1
    LINK_LOST = 2
    DORMANT = 3


def _compiler() -> str:
    for cxx in (os.environ.get("CXX"), "c++", "clang++", "g++"):
        if cxx and shutil.which(cxx):
            return cxx
    raise FirmwareUnavailable("no C++ compiler found (set CXX)")


def build_library() -> Path:
    """Compiles belt_core + the C shim into a shared library, cached by source hash."""
    files = [_SHIM, *(_CORE / s for s in _SOURCES), *(_CORE / h for h in _HEADERS)]
    missing = [str(f) for f in files if not f.exists()]
    if missing:
        raise FirmwareUnavailable(f"firmware sources not found: {missing}")
    digest = hashlib.sha256(b"".join(f.read_bytes() for f in files)).hexdigest()[:16]
    suffix = ".dylib" if os.uname().sysname == "Darwin" else ".so"
    out = _BUILD_DIR / f"libbelt_core_{digest}{suffix}"
    if out.exists():
        return out
    _BUILD_DIR.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + f".{os.getpid()}.tmp")
    cmd = [
        _compiler(), "-std=c++17", "-O2", "-shared", "-fPIC", "-Wall",
        f"-I{_CORE}", str(_SHIM), *(str(_CORE / s) for s in _SOURCES), "-o", str(tmp),
    ]  # fmt: skip
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise FirmwareUnavailable(f"firmware build failed:\n{proc.stderr}")
    tmp.replace(out)
    return out


_LIB: ctypes.CDLL | None = None


def _lib() -> ctypes.CDLL:
    global _LIB
    if _LIB is None:
        lib = ctypes.CDLL(str(build_library()))
        lib.belt_new.restype = ctypes.c_void_p
        lib.belt_new.argtypes = [ctypes.c_uint8]
        lib.belt_free.argtypes = [ctypes.c_void_p]
        lib.belt_on_payload.restype = ctypes.c_int
        lib.belt_on_payload.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_uint32]
        lib.belt_on_link_lost.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        lib.belt_update.restype = ctypes.c_int
        lib.belt_update.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint8)]
        lib.belt_last_mode.restype = ctypes.c_int
        lib.belt_last_mode.argtypes = [ctypes.c_void_p]
        lib.belt_motor_count.restype = ctypes.c_uint8
        lib.belt_motor_count.argtypes = [ctypes.c_void_p]
        _LIB = lib
    return _LIB


class BeltFirmware:
    """One emulated belt controller. Time is passed in explicitly (ms, wraps at 2^32)."""

    def __init__(self, motors: int) -> None:
        self._lib = _lib()
        self._handle = self._lib.belt_new(motors)
        self.motors = int(self._lib.belt_motor_count(self._handle))
        self._out = (ctypes.c_uint8 * 8)()

    def __del__(self) -> None:
        handle, self._handle = getattr(self, "_handle", None), None
        if handle:
            self._lib.belt_free(handle)

    def on_payload(self, data: bytes, now_ms: int) -> int:
        return int(self._lib.belt_on_payload(self._handle, data, len(data), now_ms & 0xFFFFFFFF))

    def on_link_lost(self, now_ms: int) -> None:
        self._lib.belt_on_link_lost(self._handle, now_ms & 0xFFFFFFFF)

    def update(self, now_ms: int) -> tuple[FirmwareState, tuple[int, ...]]:
        state = self._lib.belt_update(self._handle, now_ms & 0xFFFFFFFF, self._out)
        return FirmwareState(state), tuple(self._out[: self.motors])

    @property
    def last_mode(self) -> int:
        return int(self._lib.belt_last_mode(self._handle))


@dataclass
class BeltSample:
    t: float  # seconds since the belt started
    state: FirmwareState
    levels: tuple[int, ...]
    signalling_fault: bool  # fault pattern active (its motors pulse on and off)


@dataclass
class LinkFaults:
    """Radio problems the sim can inject between hub and belt."""

    drop_writes: bool = False  # writes are lost (never acknowledged) but the link stays up
    disconnected: bool = False  # BLE link dropped (firmware gets onLinkLost); reconnects fail meanwhile


# The fake radio's timings. Assumptions, not measurements: a write-with-response
# is acknowledged a couple of BLE connection intervals later; a reconnect to a
# known address takes a moment. The real hub retries after 1 s (doubling); the
# sim retries sooner so scenarios stay short.
ACK_RTT_S = 0.02
CONNECT_S = 0.05
SIM_RECONNECT_DELAY_S = 0.2
SIM_MAX_RECONNECT_DELAY_S = 0.4


class SimRadioClient:
    """Stands in for bleak.BleakClient between the hub's real BleBelt and the
    emulated firmware: writes are decoded by belt_core, acknowledged after
    ACK_RTT_S, or silently lost (never acknowledged) while drop_writes is on."""

    def __init__(self, belt: EmulatedBelt, target: str, disconnected_callback=None) -> None:  # noqa: ANN001
        self.belt = belt
        self.address = str(target)
        self.is_connected = False
        self._on_disconnect = disconnected_callback

    async def connect(self) -> None:
        await asyncio.sleep(CONNECT_S)
        if self.belt.faults.disconnected:
            raise TimeoutError("sim: belt out of range")
        self.is_connected = True
        self.belt._radio = self

    async def read_gatt_char(self, uuid: str) -> bytes:
        return bytes([1, self.belt.motors])  # [protocol_version, motor_count]

    async def write_gatt_char(self, uuid: str, data: bytes, response: bool = False) -> None:
        belt = self.belt
        belt.writes += 1
        if not self.is_connected:
            raise OSError("sim: not connected")
        if belt.faults.drop_writes:
            belt.lost_writes += 1
            if response:
                await asyncio.Event().wait()  # no Write Response ever comes
            return
        if belt.fw.on_payload(bytes(data), belt._now_ms()) != 0:
            belt.rejected += 1
        if response:
            await asyncio.sleep(ACK_RTT_S)

    async def disconnect(self) -> None:
        if self.is_connected:
            self.is_connected = False
            self.belt.fw.on_link_lost(self.belt._now_ms())  # the firmware's onDisconnect

    def drop_link(self) -> None:
        if self.is_connected:
            self.is_connected = False
            if self._on_disconnect is not None:
                self._on_disconnect(self)


@dataclass
class EmulatedBelt:
    """Implements haptic_hub.belt.base.Belt by running the hub's real BleBelt
    (write-with-response, acknowledgement window, reconnects) over SimRadioClient
    into the real firmware logic. The firmware loop runs every 5 ms and every
    output is recorded for the scenario checks."""

    motors: int
    t0: float = field(default_factory=time.monotonic)
    faults: LinkFaults = field(default_factory=LinkFaults)
    samples: list[BeltSample] = field(default_factory=list)
    writes: int = 0  # radio writes attempted by the hub's BleBelt
    lost_writes: int = 0
    rejected: int = 0

    def __post_init__(self) -> None:
        from haptic_hub.belt.ble import BleBelt

        self.fw = BeltFirmware(self.motors)
        self._radio: SimRadioClient | None = None
        self._was_disconnected = False
        self.ble = BleBelt(self.motors, address="sim-belt", client_factory=lambda target, **kw: SimRadioClient(self, target, **kw))
        self.ble.RECONNECT_DELAY_S = SIM_RECONNECT_DELAY_S
        self.ble.MAX_RECONNECT_DELAY_S = SIM_MAX_RECONNECT_DELAY_S

    def _now_ms(self) -> int:
        return int((time.monotonic() - self.t0) * 1000)

    @property
    def state(self) -> str:
        return self.ble.state

    @property
    def fault(self) -> str | None:
        return self.ble.fault

    @property
    def reconnects(self) -> int:
        return self.ble.reconnects

    async def run(self) -> None:
        link = asyncio.create_task(self.ble.run(), name="sim-ble")
        try:
            while True:
                if link.done():
                    link.result()  # surface a crash in the hub's BLE code
                self.tick()
                await asyncio.sleep(FIRMWARE_LOOP_MS / 1000)
        finally:
            link.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await link

    def tick(self) -> BeltSample:
        now_ms = self._now_ms()
        if self.faults.disconnected and not self._was_disconnected:
            self.fw.on_link_lost(now_ms)
            if self._radio is not None:
                self._radio.drop_link()
        self._was_disconnected = self.faults.disconnected
        state, levels = self.fw.update(now_ms)
        fault = state == FirmwareState.LINK_LOST or (state == FirmwareState.ACTIVE and self.fw.last_mode == 2)
        sample = BeltSample(now_ms / 1000, state, levels, fault)
        self.samples.append(sample)
        return sample

    @property
    def latest(self) -> BeltSample | None:
        return self.samples[-1] if self.samples else None

    async def send(self, cmd: BeltCommand) -> None:
        await self.ble.send(cmd)

    async def close(self) -> None:
        await self.ble.close()
