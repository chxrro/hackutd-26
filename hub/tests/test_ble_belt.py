"""BleBelt's connection handling against a fake bleak client. No Bluetooth needed."""

import asyncio

import pytest
from bleak.exc import BleakError

import haptic_hub.belt.ble as ble
from haptic_hub.protocol import BeltCommand, BeltMode, decode_belt_command


class FakeClient:
    """Stands in for bleak.BleakClient. `info` is what the info characteristic
    returns, or an exception to raise when it's read."""

    info: bytes | Exception = b"\x01\x05"
    made: list["FakeClient"] = []

    def __init__(self, target, disconnected_callback=None):
        self.address = str(target)
        self.is_connected = False
        self.disconnects = 0
        self.writes: list[bytes] = []
        self._on_disconnect = disconnected_callback
        FakeClient.made.append(self)

    async def connect(self):
        self.is_connected = True

    async def read_gatt_char(self, uuid):
        if isinstance(self.info, Exception):
            raise self.info
        return self.info

    async def write_gatt_char(self, uuid, data, response=False):
        self.writes.append(bytes(data))

    async def disconnect(self):
        self.disconnects += 1
        self.is_connected = False

    def drop_link(self):  # the belt walks out of range
        self.is_connected = False
        self._on_disconnect(self)


@pytest.fixture
def fake_bleak(monkeypatch):
    FakeClient.info, FakeClient.made = b"\x01\x05", []
    monkeypatch.setattr(ble, "BleakClient", FakeClient)
    monkeypatch.setattr(ble.BleBelt, "RECONNECT_DELAY_S", 0.02)
    monkeypatch.setattr(ble.BleBelt, "MAX_RECONNECT_DELAY_S", 0.02)
    return FakeClient


async def _run_for(belt, seconds):
    task = asyncio.create_task(belt.run())
    await asyncio.sleep(seconds)
    alive = not task.done()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return alive


@pytest.mark.parametrize(
    "info",
    [BleakError("characteristic not found"), b"\x01", OSError("link dropped"), ValueError("anything else")],
    ids=["bleak-error", "short-info", "os-error", "unexpected-error"],
)
def test_failed_setup_drops_the_connection_and_keeps_retrying(fake_bleak, info):
    fake_bleak.info = info
    belt = ble.BleBelt(5, address="AA:BB")
    alive = asyncio.run(_run_for(belt, 0.2))
    assert alive, "run() must not die: nothing else would reconnect the belt"
    assert len(fake_bleak.made) >= 2, "should have retried"
    # Every attempt was disconnected again: an orphaned connection keeps the
    # belt from advertising, so the hub could never find it.
    assert all(not c.is_connected and c.disconnects == 1 for c in fake_bleak.made)
    assert belt.state in ("disconnected", "connecting")


def test_connects_sends_and_reconnects_after_link_loss(fake_bleak):
    belt = ble.BleBelt(5, address="AA:BB")

    async def scenario():
        task = asyncio.create_task(belt.run())
        await asyncio.sleep(0.05)
        assert belt.state == "connected"
        await belt.send(BeltCommand.fault(5))
        first = fake_bleak.made[0]
        cmd, _ = decode_belt_command(first.writes[-1])
        assert cmd.mode == BeltMode.FAULT

        first.drop_link()
        await asyncio.sleep(0.01)
        await belt.send(BeltCommand.off(5))  # while down: dropped quietly, no raise
        await asyncio.sleep(0.1)
        assert belt.state == "connected" and len(fake_bleak.made) == 2
        await belt.send(BeltCommand.off(5))
        assert fake_bleak.made[1].writes
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
