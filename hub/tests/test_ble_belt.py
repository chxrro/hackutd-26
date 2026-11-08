"""BleBelt's connection handling against a fake bleak client. No Bluetooth needed.
Timings here are asyncio timings on this machine, not BLE radio measurements."""

import asyncio
import time

import pytest
from bleak.exc import BleakError

import haptic_hub.belt.ble as ble
from haptic_hub.protocol import BeltCommand, BeltMode, decode_belt_command


class FakeClient:
    """Stands in for bleak.BleakClient. `info` is what the info characteristic
    returns, or an exception to raise when it's read."""

    info: bytes | Exception = b"\x01\x05"
    made: list["FakeClient"] = []
    # How writes behave: answered after ack_delay_s; "hang" never answers (a lost
    # write-with-response); an Exception is raised.
    write_mode: str | Exception = "ok"
    ack_delay_s = 0.0

    def __init__(self, target, disconnected_callback=None):
        self.address = str(target)
        self.is_connected = False
        self.disconnects = 0
        self.writes: list[bytes] = []
        self.responses: list[bool] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self._on_disconnect = disconnected_callback
        FakeClient.made.append(self)

    async def connect(self):
        self.is_connected = True

    async def read_gatt_char(self, uuid):
        if isinstance(self.info, Exception):
            raise self.info
        return self.info

    async def write_gatt_char(self, uuid, data, response=False):
        self.responses.append(response)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if isinstance(FakeClient.write_mode, Exception):
                raise FakeClient.write_mode
            if response and FakeClient.write_mode == "hang":
                await asyncio.Event().wait()
            if response and FakeClient.ack_delay_s:
                await asyncio.sleep(FakeClient.ack_delay_s)
            self.writes.append(bytes(data))  # acknowledged (or sent without response)
        finally:
            self.in_flight -= 1

    async def disconnect(self):
        self.disconnects += 1
        self.is_connected = False

    def drop_link(self):  # the belt walks out of range
        self.is_connected = False
        self._on_disconnect(self)


@pytest.fixture
def fake_bleak(monkeypatch):
    FakeClient.info, FakeClient.made = b"\x01\x05", []
    FakeClient.write_mode, FakeClient.ack_delay_s = "ok", 0.0
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
        await asyncio.sleep(0.01)  # send() only queues; the writer task writes
        first = fake_bleak.made[0]
        cmd, _ = decode_belt_command(first.writes[-1])
        assert cmd.mode == BeltMode.FAULT

        first.drop_link()
        await asyncio.sleep(0.01)
        await belt.send(BeltCommand.off(5))  # while down: dropped quietly, no raise
        await asyncio.sleep(0.1)
        assert belt.state == "connected" and len(fake_bleak.made) == 2
        await belt.send(BeltCommand.off(5))
        await asyncio.sleep(0.01)
        assert fake_bleak.made[1].writes
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())


# -- write acknowledgement (belt not acknowledging) ----------------------------

TICK = 1 / 15


async def _start(belt):
    task = asyncio.create_task(belt.run())
    for _ in range(100):
        if belt.state == "connected":
            break
        await asyncio.sleep(0.005)
    assert belt.state == "connected"
    return task


async def _stop(task):
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def _tick(belt, seconds, cmd=None):
    """Sends at the hub's 15 Hz; returns [(t, belt.fault)] sampled after each send."""
    cmd = cmd or BeltCommand.fault(5)
    out, t0 = [], time.monotonic()
    while time.monotonic() - t0 < seconds:
        await belt.send(cmd)
        await asyncio.sleep(TICK)
        out.append((time.monotonic() - t0, belt.fault))
    return out


def test_writes_use_response_one_at_a_time_and_stay_healthy(fake_bleak):
    fake_bleak.ack_delay_s = 0.03  # a couple of connection intervals

    async def scenario():
        belt = ble.BleBelt(5, address="AA:BB")
        task = await _start(belt)
        faults = await _tick(belt, 0.6)
        await _stop(task)
        return belt, faults

    belt, faults = asyncio.run(scenario())
    client = fake_bleak.made[0]
    assert all(client.responses) and client.max_in_flight == 1
    assert all(f is None for _, f in faults)
    assert belt.health.acked >= 6


def test_send_never_blocks_the_hub_loop_even_if_writes_hang(fake_bleak):
    fake_bleak.write_mode = "hang"

    async def scenario():
        belt = ble.BleBelt(5, address="AA:BB")
        task = await _start(belt)
        t0 = time.monotonic()
        for _ in range(10):
            await belt.send(BeltCommand.fault(5))
        took = time.monotonic() - t0
        await _stop(task)
        return took

    assert asyncio.run(scenario()) < 0.01


def test_lost_writes_report_not_acknowledging_within_one_ttl(fake_bleak):
    async def scenario():
        belt = ble.BleBelt(5, address="AA:BB")
        task = await _start(belt)
        healthy = await _tick(belt, 0.3)
        fake_bleak.write_mode = "hang"  # writes vanish; the link stays up
        lost = await _tick(belt, 0.6, BeltCommand.fault(5, ttl_ms=250))
        await _stop(task)
        return healthy, lost

    healthy, lost = asyncio.run(scenario())
    assert all(f is None for _, f in healthy)
    first = next(t for t, f in lost if f is not None)
    assert first <= 0.25 + 2 * TICK, f"reported after {first:.2f} s"
    assert all(f == "belt not acknowledging" for t, f in lost if t > first)  # latched, no flicker


def test_window_follows_the_commands_ttl(fake_bleak):
    async def scenario():
        belt = ble.BleBelt(5, address="AA:BB")
        task = await _start(belt)
        await _tick(belt, 0.2)
        fake_bleak.write_mode = "hang"
        lost = await _tick(belt, 0.8, BeltCommand.fault(5, ttl_ms=600))
        await _stop(task)
        return lost

    lost = asyncio.run(scenario())
    first = next(t for t, f in lost if f is not None)
    assert 0.5 <= first <= 0.6 + 2 * TICK


def test_unanswered_write_drops_the_connection_and_reconnects(fake_bleak, monkeypatch):
    monkeypatch.setattr(ble.BleBelt, "WRITE_TIMEOUT_S", 0.3)

    async def scenario():
        belt = ble.BleBelt(5, address="AA:BB")
        task = await _start(belt)
        fake_bleak.write_mode = "hang"
        await _tick(belt, 0.45)
        fake_bleak.write_mode = "ok"  # new writes are answered (one already hanging still times out)
        after = await _tick(belt, 0.9)
        await _stop(task)
        return belt, after

    belt, after = asyncio.run(scenario())
    first = fake_bleak.made[0]
    assert belt.reconnects >= 1 and first.disconnects == 1 and not first.is_connected  # not left half-open
    assert len(fake_bleak.made) == belt.reconnects + 1 and belt.state == "connected"
    assert after[-1][1] is None and fake_bleak.made[-1].writes


def test_recovers_after_writes_confirm_again_without_flicker(fake_bleak, monkeypatch):
    monkeypatch.setattr(ble.BleBelt, "MAX_WRITE_FAILURES", 1000)  # stay on this connection

    async def scenario():
        belt = ble.BleBelt(5, address="AA:BB")
        task = await _start(belt)
        fake_bleak.write_mode = BleakError("GATT error")  # every write errors out
        bad = await _tick(belt, 0.5)
        fake_bleak.write_mode = "ok"
        good = await _tick(belt, 0.5)
        await _stop(task)
        return belt, bad, good

    belt, bad, good = asyncio.run(scenario())
    assert bad[-1][1] == "belt not acknowledging" and belt.health.failed_writes >= 5
    # still faulted until RECOVER_AFTER_WRITES acks in a row, then healthy for good
    assert good[0][1] == "belt not acknowledging"
    first_ok = next(i for i, (_, f) in enumerate(good) if f is None)
    assert first_ok >= ble.BleBelt.RECOVER_AFTER_WRITES - 1
    assert all(f is None for _, f in good[first_ok:])
    assert len(fake_bleak.made) == 1


def test_repeated_write_errors_reconnect(fake_bleak, monkeypatch):
    monkeypatch.setattr(ble.BleBelt, "MAX_WRITE_FAILURES", 5)

    async def scenario():
        belt = ble.BleBelt(5, address="AA:BB")
        task = await _start(belt)
        fake_bleak.write_mode = OSError("write failed")
        await _tick(belt, 0.6)
        await _stop(task)
        return belt

    belt = asyncio.run(scenario())
    assert belt.reconnects >= 1 and len(fake_bleak.made) >= 2


def test_no_fault_reported_while_link_is_down(fake_bleak):
    """'belt disconnected' / 'belt connecting' come from `state`; `fault` is only
    about a link that is up."""

    async def scenario():
        belt = ble.BleBelt(5, address="AA:BB")
        task = await _start(belt)
        fake_bleak.made[0].drop_link()
        await asyncio.sleep(0.005)
        state, fault = belt.state, belt.fault
        await _stop(task)
        return state, fault

    state, fault = asyncio.run(scenario())
    assert state in ("disconnected", "connecting") and fault is None


def test_firmware_without_write_with_response_falls_back(fake_bleak):
    class Char:
        properties = ["write-without-response"]

    class Services:
        def get_characteristic(self, uuid):
            return Char()

    class OldFirmwareClient(fake_bleak):
        services = Services()

    async def scenario():
        belt = ble.BleBelt(5, address="AA:BB", client_factory=OldFirmwareClient)
        task = await _start(belt)
        faults = await _tick(belt, 0.4)
        await _stop(task)
        return belt, faults

    belt, faults = asyncio.run(scenario())
    assert belt.confirming is False and not any(fake_bleak.made[0].responses)
    assert all(f is None for _, f in faults)


def test_close_sends_off_after_run_is_cancelled(fake_bleak):
    async def scenario():
        belt = ble.BleBelt(5, address="AA:BB")
        task = await _start(belt)
        await _tick(belt, 0.1)
        await _stop(task)  # the hub cancels run() first, then calls close()
        await belt.close()
        return belt

    belt = asyncio.run(scenario())
    client = fake_bleak.made[0]
    cmd, _ = decode_belt_command(client.writes[-1])
    assert cmd.mode == BeltMode.OFF and client.disconnects == 1 and belt.state == "disconnected"
