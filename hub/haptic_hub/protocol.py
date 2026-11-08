"""Wire formats shared with the phone app and the belt firmware.

The contract is docs/protocol.md; the golden vectors there are checked in
tests/test_protocol.py, meta/HapticBeltTests and esp32/test.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum

# ---------------------------------------------------------------- phone -> hub

FRAME_MAGIC = b"HB"
FRAME_VERSION = 1
_FRAME_HEADER = struct.Struct("<2sBBIQHH")
FRAME_HEADER_SIZE = _FRAME_HEADER.size  # 20


class ProtocolError(ValueError):
    """A message didn't match the documented format."""


@dataclass(frozen=True)
class FramePacket:
    seq: int
    capture_ms: int
    width: int
    height: int
    jpeg: bytes


def encode_frame_packet(packet: FramePacket) -> bytes:
    header = _FRAME_HEADER.pack(
        FRAME_MAGIC, FRAME_VERSION, 0, packet.seq, packet.capture_ms, packet.width, packet.height
    )
    return header + packet.jpeg


def decode_frame_packet(data: bytes) -> FramePacket:
    if len(data) <= FRAME_HEADER_SIZE:
        raise ProtocolError(f"frame message too short ({len(data)} bytes)")
    magic, version, _flags, seq, capture_ms, width, height = _FRAME_HEADER.unpack_from(data)
    if magic != FRAME_MAGIC:
        raise ProtocolError(f"bad frame magic {magic!r}")
    if version != FRAME_VERSION:
        raise ProtocolError(f"unsupported frame version {version}")
    return FramePacket(seq, capture_ms, width, height, bytes(data[FRAME_HEADER_SIZE:]))


# ---------------------------------------------------------------- hub -> belt

BELT_SERVICE_UUID = "88368a33-70aa-41e2-b071-4f618af1856a"
BELT_COMMAND_UUID = "71dbf1b6-d7d2-4208-954b-b8a38e54618f"
BELT_INFO_UUID = "986b42ae-86e7-4eb6-a579-cd8360e40c0b"
BELT_DEVICE_NAME = "HapticBelt"

BELT_PROTOCOL_VERSION = 1
BELT_MAX_MOTORS = 8
_BELT_HEADER = struct.Struct("<BBBHB")


class BeltMode(IntEnum):
    OFF = 0
    LEVELS = 1
    FAULT = 2


@dataclass(frozen=True)
class BeltCommand:
    mode: BeltMode
    levels: tuple[int, ...]
    ttl_ms: int = 250

    def __post_init__(self) -> None:
        if not 1 <= len(self.levels) <= BELT_MAX_MOTORS:
            raise ValueError(f"need 1..{BELT_MAX_MOTORS} levels, got {len(self.levels)}")
        if any(not 0 <= lv <= 255 for lv in self.levels):
            raise ValueError(f"levels must be 0..255: {self.levels}")
        if not 0 <= self.ttl_ms <= 0xFFFF:
            raise ValueError(f"ttl_ms out of range: {self.ttl_ms}")

    @classmethod
    def off(cls, motors: int, ttl_ms: int = 250) -> BeltCommand:
        return cls(BeltMode.OFF, (0,) * motors, ttl_ms)

    @classmethod
    def fault(cls, motors: int, ttl_ms: int = 250) -> BeltCommand:
        return cls(BeltMode.FAULT, (0,) * motors, ttl_ms)


def encode_belt_command(cmd: BeltCommand, seq: int) -> bytes:
    header = _BELT_HEADER.pack(BELT_PROTOCOL_VERSION, int(cmd.mode), seq & 0xFF, cmd.ttl_ms, len(cmd.levels))
    return header + bytes(cmd.levels)


def decode_belt_command(data: bytes) -> tuple[BeltCommand, int]:
    """Inverse of encode_belt_command. Returns (command, seq). Used by tests and the simulator."""
    if len(data) < _BELT_HEADER.size:
        raise ProtocolError("belt command too short")
    version, mode, seq, ttl_ms, count = _BELT_HEADER.unpack_from(data)
    if version != BELT_PROTOCOL_VERSION:
        raise ProtocolError(f"unsupported belt version {version}")
    if len(data) != _BELT_HEADER.size + count:
        raise ProtocolError("belt command length mismatch")
    return BeltCommand(BeltMode(mode), tuple(data[_BELT_HEADER.size :]), ttl_ms), seq


# ---------------------------------------------------------------- belt -> hub

BELT_STATUS_UUID = "ba2928d9-3014-49de-8949-bd9933b65d7a"
BELT_STATUS_VERSION = 1
_BELT_STATUS_HEADER = struct.Struct("<BBBHIIBB")  # 15 bytes, then `count` levels
BELT_STATUS_NO_COMMAND_AGE = 0xFFFF
BELT_INFO_MAX_BUILD = 16


class BeltFirmwareMode(IntEnum):
    """What the belt reports it is doing (docs/protocol.md section 3, "Status")."""

    IDLE = 0  # no command since boot: silent
    OFF = 1
    LEVELS = 2
    FAULT = 3  # following a hub fault command
    EXPIRED = 4  # command TTL lapsed: fault pattern
    DISCONNECTED = 5  # BLE dropped while active: fault pattern
    DORMANT = 6  # fault pattern finished: silent


@dataclass(frozen=True)
class BeltStatus:
    mode: BeltFirmwareMode
    seq: int  # seq of the last command the firmware applied
    command_age_ms: int  # since that command was applied; BELT_STATUS_NO_COMMAND_AGE = none / saturated
    uptime_ms: int
    build_id: int  # first 8 hex digits of the firmware commit, 0 = unknown
    max_duty: int
    levels: tuple[int, ...]

    @property
    def build_hex(self) -> str | None:
        return f"{self.build_id:08x}" if self.build_id else None


def encode_belt_status(st: BeltStatus) -> bytes:
    """What the firmware sends. Used by tests and the golden vector."""
    header = _BELT_STATUS_HEADER.pack(
        BELT_STATUS_VERSION, int(st.mode), st.seq & 0xFF, st.command_age_ms, st.uptime_ms & 0xFFFFFFFF,
        st.build_id, st.max_duty, len(st.levels),
    )  # fmt: skip
    return header + bytes(st.levels)


def decode_belt_status(data: bytes) -> BeltStatus:
    if len(data) < _BELT_STATUS_HEADER.size:
        raise ProtocolError(f"belt status too short ({len(data)} bytes)")
    version, mode, seq, age, uptime, build, max_duty, count = _BELT_STATUS_HEADER.unpack_from(data)
    if version != BELT_STATUS_VERSION:
        raise ProtocolError(f"unsupported belt status version {version}")
    if len(data) != _BELT_STATUS_HEADER.size + count:
        raise ProtocolError(f"belt status length mismatch ({len(data)} bytes, {count} levels)")
    try:
        fw_mode = BeltFirmwareMode(mode)
    except ValueError:
        raise ProtocolError(f"unknown belt status mode {mode}") from None
    return BeltStatus(fw_mode, seq, age, uptime, build, max_duty, tuple(data[_BELT_STATUS_HEADER.size :]))


@dataclass(frozen=True)
class BeltInfo:
    version: int
    motors: int
    build: str | None  # firmware build id text; None for firmware older than 2026-11-08


def decode_belt_info(data: bytes) -> BeltInfo:
    """[protocol_version, motor_count, build id ASCII (optional, <= 16 bytes)]."""
    if len(data) < 2:
        raise ProtocolError(f"belt info is {len(data)} bytes, expected [protocol_version, motor_count]")
    text = bytes(data[2 : 2 + BELT_INFO_MAX_BUILD]).decode("ascii", "replace").strip("\x00 ")
    return BeltInfo(data[0], data[1], text or None)
