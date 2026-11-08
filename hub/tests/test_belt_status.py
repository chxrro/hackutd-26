"""The belt status characteristic on the hub side (docs/protocol.md section 3,
"Status"): StatusHealth's verdicts on synthetic statuses, and BleBelt subscribing
to a fake bleak client. No Bluetooth; timings are asyncio timings on this
machine, not BLE radio measurements."""

import asyncio
import time

import pytest

import haptic_hub.belt.ble as ble
from haptic_hub.belt.confirm import (
    FAULT_PATTERN_REASON,
    NOT_APPLYING_REASON,
    STATUS_STALE_REASON,
    TTL_EXPIRED_REASON,
    StatusHealth,
)
from haptic_hub.protocol import (
    BELT_STATUS_UUID,
    BeltCommand,
    BeltFirmwareMode,
    BeltMode,
    BeltStatus,
    decode_belt_command,
    encode_belt_status,
)
from test_ble_belt import TICK, FakeClient, _start, _stop, fake_bleak  # noqa: F401 - fixture

W = 0.25  # command TTL window, s
LEVELS = BeltCommand(BeltMode.LEVELS, (0, 0, 150, 0, 0))
FW_MODE = {BeltMode.OFF: BeltFirmwareMode.OFF, BeltMode.LEVELS: BeltFirmwareMode.LEVELS, BeltMode.FAULT: BeltFirmwareMode.FAULT}


def status(mode=BeltFirmwareMode.LEVELS, seq=1, age=20, levels=(0, 0, 150, 0, 0)):
    return BeltStatus(mode, seq, age, 1000, 0x1A2B3C4D, 255, levels)


# ------------------------------------------------------------------ StatusHealth


def test_matching_status_confirms():
    h = StatusHealth()
    h.wrote(1, LEVELS, 10.0)
    h.on_status(status(seq=1), 10.05, W)
    assert h.check(10.05) is None and h.confirming(10.05)


def test_status_before_the_first_write_is_ignored_until_the_grace_ends():
    """The status read on connect describes the previous connection ("disconnected")."""
    h = StatusHealth()
    h.on_status(status(BeltFirmwareMode.DISCONNECTED, seq=40), 10.0, W)
    h.wrote(1, LEVELS, 10.01)
    assert h.check(10.02) is None and not h.confirming(10.02)
    # Never matched: armed GRACE_S after the first write, and the old status is stale by then.
    assert h.check(10.01 + h.GRACE_S + 0.01) == STATUS_STALE_REASON


@pytest.mark.parametrize(
    "st, reason",
    [
        (status(BeltFirmwareMode.EXPIRED), TTL_EXPIRED_REASON),
        (status(BeltFirmwareMode.DISCONNECTED), TTL_EXPIRED_REASON),
        (status(BeltFirmwareMode.DORMANT, age=0xFFFF), TTL_EXPIRED_REASON),
        (status(BeltFirmwareMode.IDLE, seq=0, age=0xFFFF), NOT_APPLYING_REASON),
        (status(BeltFirmwareMode.FAULT), FAULT_PATTERN_REASON),  # the hub sent levels
        (status(seq=99), NOT_APPLYING_REASON),  # a seq the hub never wrote
        (status(age=400), NOT_APPLYING_REASON),  # applied longer ago than the TTL
        (status(BeltFirmwareMode.OFF), NOT_APPLYING_REASON),  # mode doesn't match the command
    ],
    ids=["expired", "disconnected", "dormant", "idle", "fault-not-sent", "unknown-seq", "old-command", "mode-mismatch"],
)
def test_bad_status_after_a_match_is_a_reason(st, reason):
    h = StatusHealth()
    h.wrote(1, LEVELS, 10.0)
    h.on_status(status(seq=1), 10.02, W)
    assert h.check(10.02) is None
    h.on_status(st, 10.1, W)
    assert h.check(10.1) == reason and not h.confirming(10.1)


def test_applied_seq_too_old_by_the_hub_clock():
    """The firmware says it applied seq 1 recently, but the hub wrote seq 1 long
    ago and has written newer commands since: they were acknowledged, not applied."""
    h = StatusHealth()
    h.wrote(1, LEVELS, 10.0)
    h.on_status(status(seq=1), 10.02, W)
    h.wrote(2, LEVELS, 10.07)
    h.on_status(status(seq=1, age=20), 10.0 + W + h.MATCH_SLACK_S + 0.01, W)
    assert h.check(10.6) == NOT_APPLYING_REASON


def test_fault_reported_while_hub_sent_fault_is_fine():
    h = StatusHealth()
    h.wrote(1, BeltCommand.fault(5), 10.0)
    h.on_status(status(BeltFirmwareMode.FAULT, seq=1, levels=(180,) * 5), 10.02, W)
    assert h.check(10.02) is None and h.confirming(10.02)


def test_stale_after_one_second_without_status():
    h = StatusHealth()
    h.wrote(1, LEVELS, 10.0)
    h.on_status(status(seq=1), 10.02, W)
    assert h.check(10.02 + h.STALE_S) is None
    assert h.check(10.02 + h.STALE_S + 0.01) == STATUS_STALE_REASON


def test_reason_clears_after_two_matching_statuses():
    h = StatusHealth(recover_after=2)
    h.wrote(1, LEVELS, 10.0)
    h.on_status(status(seq=1), 10.02, W)
    h.on_status(status(BeltFirmwareMode.EXPIRED, seq=1), 10.3, W)
    assert h.check(10.3) == TTL_EXPIRED_REASON
    h.wrote(2, LEVELS, 10.4)
    h.on_status(status(seq=2), 10.42, W)
    assert h.check(10.42) == TTL_EXPIRED_REASON  # one good status isn't enough
    h.wrote(3, LEVELS, 10.47)
    h.on_status(status(seq=3), 10.5, W)
    assert h.check(10.5) is None


# ------------------------------------------------------------------ BleBelt + fake bleak


class _Char:
    def __init__(self, *props):
        self.properties = list(props)


class _Services:
    def get_characteristic(self, uuid):
        if uuid == BELT_STATUS_UUID:
            return _Char("read", "notify")
        return _Char("write", "write-without-response")


class StatusClient(FakeClient):
    """New firmware: answers the info read with a build id and notifies a status
    after every applied write. `firmware` = "echo" (follows the hub), "expired"
    (acknowledges writes, applies none, reports its TTL lapsed), "silent"
    (stops notifying), "levels" (always claims a levels command)."""

    info = b"\x01\x05" + b"3268a632"
    firmware = "echo"
    notify_fails = False
    services = _Services()

    def __init__(self, target, disconnected_callback=None):
        super().__init__(target, disconnected_callback)
        self.callback = None
        self.subscribed = []

    async def start_notify(self, uuid, callback):
        if StatusClient.notify_fails:
            raise OSError("CCCD write failed")
        self.subscribed.append(uuid)
        self.callback = callback

    async def read_gatt_char(self, uuid):
        if uuid == BELT_STATUS_UUID:
            return encode_belt_status(status(BeltFirmwareMode.DISCONNECTED, seq=200))
        return await super().read_gatt_char(uuid)

    async def write_gatt_char(self, uuid, data, response=False):
        await super().write_gatt_char(uuid, data, response)
        cmd, seq = decode_belt_command(bytes(data))
        mode = StatusClient.firmware
        if self.callback is None or mode == "silent":
            return
        if mode == "expired":
            st = status(BeltFirmwareMode.EXPIRED, seq=200, age=600)
        elif mode == "levels":
            st = status(BeltFirmwareMode.LEVELS, seq=seq, levels=cmd.levels)
        else:
            st = status(FW_MODE[cmd.mode], seq=seq, levels=cmd.levels)
        self.callback(None, bytearray(encode_belt_status(st)))


@pytest.fixture
def status_client(fake_bleak):  # noqa: F811
    StatusClient.firmware, StatusClient.notify_fails = "echo", False
    return StatusClient


async def _drive(belt, seconds, cmd=LEVELS):
    out, t0 = [], time.monotonic()
    while time.monotonic() - t0 < seconds:
        await belt.send(cmd)
        await asyncio.sleep(TICK)
        out.append((time.monotonic() - t0, belt.fault, belt.confirmed))
    return out


def test_subscribes_and_confirms_by_status(status_client):
    async def scenario():
        belt = ble.BleBelt(5, address="AA:BB", client_factory=status_client)
        task = await _start(belt)
        samples = await _drive(belt, 0.4)
        fw = belt.firmware
        await _stop(task)
        return belt, samples, fw

    belt, samples, fw = asyncio.run(scenario())
    assert belt.status_supported and belt.info.build == "3268a632"
    assert all(f is None for _, f, _ in samples)
    assert samples[-1][2] == "status"
    assert fw["build"] == "3268a632" and fw["mode"] == "levels" and fw["levels"] == [0, 0, 150, 0, 0]
    assert fw["max_duty"] == 255 and fw["status_age_ms"] < 200


@pytest.mark.parametrize(
    "firmware, cmd, reason",
    [
        ("expired", LEVELS, TTL_EXPIRED_REASON),
        ("silent", LEVELS, STATUS_STALE_REASON),
        ("levels", BeltCommand.off(5), NOT_APPLYING_REASON),
    ],
)
def test_status_problem_turns_unhealthy_although_writes_are_acknowledged(status_client, firmware, cmd, reason):
    async def scenario():
        belt = ble.BleBelt(5, address="AA:BB", client_factory=status_client)
        task = await _start(belt)
        await _drive(belt, 0.2)  # healthy, matched
        status_client.firmware = firmware
        samples = await _drive(belt, 1.4, cmd)
        await _stop(task)
        return belt, samples

    belt, samples = asyncio.run(scenario())
    assert belt.health.acked > 0 and belt.health.faulted is False  # the write path looked fine
    reasons = {f for _, f, _ in samples if f is not None}
    assert reasons == {reason}
    first = min(t for t, f, _ in samples if f is not None)
    bound = 0.15 if firmware != "silent" else StatusHealth.STALE_S + 0.15
    assert first <= bound, f"{reason} after {first:.2f} s"
    assert samples[-1][2] is None  # not confirmed while faulted


def test_old_firmware_without_status_relies_on_acks(fake_bleak):  # noqa: F811
    async def scenario():
        belt = ble.BleBelt(5, address="AA:BB")
        task = await _start(belt)
        samples = await _drive(belt, 1.3)
        fw = belt.firmware
        await _stop(task)
        return belt, samples, fw

    belt, samples, fw = asyncio.run(scenario())
    assert not belt.status_supported and belt.info.build is None and fw is None
    assert all(f is None for _, f, _ in samples) and samples[-1][2] == "ack"


def test_subscription_failure_falls_back_instead_of_refusing_the_belt(status_client):
    status_client.notify_fails = True

    async def scenario():
        belt = ble.BleBelt(5, address="AA:BB", client_factory=status_client)
        task = await _start(belt)
        samples = await _drive(belt, 1.3)
        await _stop(task)
        return belt, samples

    belt, samples = asyncio.run(scenario())
    assert not belt.status_supported and len(status_client.made) == 1
    assert all(f is None for _, f, _ in samples) and samples[-1][2] == "ack"


def test_write_ack_reason_takes_precedence(status_client):
    async def scenario():
        belt = ble.BleBelt(5, address="AA:BB", client_factory=status_client)
        task = await _start(belt)
        await _drive(belt, 0.2)
        FakeClient.write_mode = "hang"  # writes stop being acknowledged ...
        client = status_client.made[0]
        expired = encode_belt_status(status(BeltFirmwareMode.EXPIRED, seq=200, age=600))
        samples = []
        for _ in range(9):  # ... and the belt reports its TTL lapsed
            client.callback(None, bytearray(expired))
            samples += await _drive(belt, TICK)
        await _stop(task)
        return belt, samples

    belt, samples = asyncio.run(scenario())
    assert belt.status_health.check(time.monotonic()) == TTL_EXPIRED_REASON  # both are true ...
    assert samples[-1][1] == "belt not acknowledging"  # ... the write-ack reason is reported


def test_hub_status_json_carries_belt_firmware_and_confirmation():
    from haptic_hub.app import Hub
    from haptic_hub.belt.sim import SimBelt
    from haptic_hub.config import HubConfig
    from haptic_hub.perception.sweep import SweepPerception
    from test_end_to_end import FakeSource

    class ReportingBelt(SimBelt):
        state = "connected"
        confirmed = "status"
        firmware = {"build": "3268a632", "mode": "fault", "seq": 3, "status_age_ms": 12,
                    "uptime_ms": 5000, "max_duty": 190, "levels": [180] * 5}  # fmt: skip

    hub = Hub(HubConfig(), FakeSource(seconds=1), SweepPerception(5), ReportingBelt(5))
    st = hub.status()
    assert st["belt_confirmed"] == "status" and st["belt_firmware"]["build"] == "3268a632"
    plain = Hub(HubConfig(), FakeSource(seconds=1), SweepPerception(5), SimBelt(5)).status()
    assert plain["belt_confirmed"] is None and plain["belt_firmware"] is None  # belts without a report
