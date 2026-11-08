"""Golden vectors from docs/protocol.md. The iOS app and firmware test the same bytes."""

import pytest

from haptic_hub.protocol import (
    BeltCommand,
    BeltFirmwareMode,
    BeltInfo,
    BeltMode,
    BeltStatus,
    FramePacket,
    ProtocolError,
    decode_belt_command,
    decode_belt_info,
    decode_belt_status,
    decode_frame_packet,
    encode_belt_command,
    encode_belt_status,
    encode_frame_packet,
)

FRAME_GOLDEN = bytes.fromhex("48 42 01 00 01 00 00 00 00 68 e5 cf 8b 01 00 00 f8 01 80 03 ff d8 ff d9")
BELT_GOLDEN = bytes.fromhex("01 01 07 fa 00 05 00 40 80 c0 ff")
STATUS_GOLDEN = bytes.fromhex("01 02 07 28 00 40 e2 01 00 4d 3c 2b 1a ff 05 00 40 80 c0 ff")
INFO_GOLDEN = bytes.fromhex("01 05 33 32 36 38 61 36 33 32")


def test_frame_packet_golden_vector():
    packet = FramePacket(seq=1, capture_ms=1_700_000_000_000, width=504, height=896, jpeg=b"\xff\xd8\xff\xd9")
    assert encode_frame_packet(packet) == FRAME_GOLDEN
    assert decode_frame_packet(FRAME_GOLDEN) == packet


@pytest.mark.parametrize(
    "data",
    [b"", FRAME_GOLDEN[:20], b"XX" + FRAME_GOLDEN[2:], FRAME_GOLDEN[:2] + b"\x09" + FRAME_GOLDEN[3:]],
)
def test_frame_packet_rejects_malformed(data):
    with pytest.raises(ProtocolError):
        decode_frame_packet(data)


def test_belt_command_golden_vector():
    cmd = BeltCommand(BeltMode.LEVELS, (0, 64, 128, 192, 255), ttl_ms=250)
    assert encode_belt_command(cmd, seq=7) == BELT_GOLDEN
    assert decode_belt_command(BELT_GOLDEN) == (cmd, 7)


def test_belt_command_validates():
    with pytest.raises(ValueError):
        BeltCommand(BeltMode.LEVELS, (256,))
    with pytest.raises(ValueError):
        BeltCommand(BeltMode.LEVELS, ())
    with pytest.raises(ValueError):
        BeltCommand(BeltMode.LEVELS, (0,) * 9)


def test_sim_belt_render():
    from haptic_hub.belt.sim import render

    assert render(BeltCommand(BeltMode.LEVELS, (0, 128, 255))) == "LEVELS [ ▄█]"
    assert render(BeltCommand.fault(3)) == "FAULT  [!!!]"


def test_belt_status_golden_vector():
    st = BeltStatus(BeltFirmwareMode.LEVELS, seq=7, command_age_ms=40, uptime_ms=123456, build_id=0x1A2B3C4D,
                    max_duty=255, levels=(0, 64, 128, 192, 255))  # fmt: skip
    assert encode_belt_status(st) == STATUS_GOLDEN
    assert decode_belt_status(STATUS_GOLDEN) == st
    assert len(STATUS_GOLDEN) == 20  # one notification at the default ATT MTU
    assert st.build_hex == "1a2b3c4d"


@pytest.mark.parametrize(
    "data",
    [b"", STATUS_GOLDEN[:14], STATUS_GOLDEN[:-1], b"\x02" + STATUS_GOLDEN[1:], STATUS_GOLDEN[:1] + b"\x07" + STATUS_GOLDEN[2:]],
    ids=["empty", "short-header", "length-mismatch", "bad-version", "unknown-mode"],
)
def test_belt_status_rejects_malformed(data):
    with pytest.raises(ProtocolError):
        decode_belt_status(data)


def test_belt_info_with_and_without_build():
    assert decode_belt_info(INFO_GOLDEN) == BeltInfo(1, 5, "3268a632")
    assert decode_belt_info(b"\x01\x05") == BeltInfo(1, 5, None)  # firmware before 2026-11-08
    assert decode_belt_info(b"\x01\x05" + b"3268a632+").build == "3268a632+"
    with pytest.raises(ProtocolError):
        decode_belt_info(b"\x01")
