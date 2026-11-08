"""Golden vectors from docs/protocol.md. The iOS app and firmware test the same bytes."""

import pytest

from haptic_hub.protocol import (
    BeltCommand,
    BeltMode,
    FramePacket,
    ProtocolError,
    decode_belt_command,
    decode_frame_packet,
    encode_belt_command,
    encode_frame_packet,
)

FRAME_GOLDEN = bytes.fromhex("48 42 01 00 01 00 00 00 00 68 e5 cf 8b 01 00 00 f8 01 80 03 ff d8 ff d9")
BELT_GOLDEN = bytes.fromhex("01 01 07 fa 00 05 00 40 80 c0 ff")


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
