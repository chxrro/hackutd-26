"""The real belt: ESP32 over Bluetooth LE (bleak)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from typing import Any

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.exc import BleakError

from haptic_hub.belt.confirm import CommandWriter, WriteHealth, WriteLinkBroken, ttl_window_s
from haptic_hub.protocol import (
    BELT_COMMAND_UUID,
    BELT_DEVICE_NAME,
    BELT_INFO_UUID,
    BELT_SERVICE_UUID,
    BeltCommand,
    encode_belt_command,
)

log = logging.getLogger(__name__)


def is_belt(device: BLEDevice, adv: AdvertisementData) -> bool:
    uuids = {u.lower() for u in adv.service_uuids}
    return BELT_SERVICE_UUID in uuids or (adv.local_name or device.name) == BELT_DEVICE_NAME


async def find_belt(timeout: float = 10.0) -> BLEDevice | None:
    return await BleakScanner.find_device_by_filter(is_belt, timeout=timeout)


def _supports_write_with_response(client: Any) -> bool | None:
    """From the discovered GATT table; None if it can't be told (assume yes)."""
    try:
        char = client.services.get_characteristic(BELT_COMMAND_UUID)
    except Exception:  # noqa: BLE001 - no services collection (or a fake client): don't guess no
        return None
    if char is None:
        return None
    return "write" in {p.lower() for p in char.properties}


class BleBelt:
    RECONNECT_DELAY_S = 1.0  # first retry; doubles up to MAX_RECONNECT_DELAY_S
    MAX_RECONNECT_DELAY_S = 10.0
    DISCONNECT_TIMEOUT_S = 5.0
    # One write-with-response normally takes a couple of BLE connection intervals
    # (tens of ms). An unanswered write means this connection is no good: drop it
    # and reconnect. The status goes unhealthy much sooner, after one TTL.
    WRITE_TIMEOUT_S = 1.0
    MAX_WRITE_FAILURES = 15  # errors in a row (~1 s at 15 Hz) before reconnecting
    RECOVER_AFTER_WRITES = 3  # acknowledged in a row before "not acknowledging" clears

    def __init__(
        self,
        expected_motors: int,
        address: str | None = None,
        client_factory: Callable[..., Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """client_factory replaces bleak.BleakClient (the simulator's fake radio, tests)."""
        self.expected_motors = expected_motors
        self.address = address
        self._client_factory = client_factory
        self._clock = clock
        self._client: Any = None
        self._state = "disconnected"
        self._disconnected = asyncio.Event()
        self.health = WriteHealth(self.RECOVER_AFTER_WRITES)
        self._writer: CommandWriter | None = None
        self._window_s = ttl_window_s(BeltCommand.off(expected_motors))
        self.confirming = True  # False when the firmware only takes write-without-response
        self.reconnects = 0  # connections dropped by the hub because writes weren't acknowledged

    @property
    def state(self) -> str:
        return self._state

    @property
    def fault(self) -> str | None:
        """Why the belt can't be confirmed to be following the hub while connected
        ("belt not acknowledging"), else None. Link state is reported by `state`."""
        if self._state != "connected" or self._writer is None or not self.confirming:
            return None
        return self.health.check(self._clock(), self._window_s)

    async def run(self) -> None:
        backoff = self.RECONNECT_DELAY_S
        while True:
            client = None
            try:
                client = await self._connect_once()
                backoff = self.RECONNECT_DELAY_S
                log.warning("%s; reconnecting", await self._serve(client))
            except Exception as exc:  # noqa: BLE001 - if this task dies, the belt never hears from the hub again
                log.warning("belt connection failed: %s", str(exc) or type(exc).__name__)
            # Not on cancellation: close() still sends the final "off" on this client.
            if client is not None and client.is_connected:
                # We gave up on a connection the radio still thinks is up (writes not
                # acknowledged). The belt stops advertising while connected, so a
                # connection left open would keep the hub from finding it again.
                await self._disconnect_quietly(client)
            self._writer = None
            self._client = None
            self._state = "disconnected"
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self.MAX_RECONNECT_DELAY_S)

    async def _serve(self, client: Any) -> str:
        """Runs the writer until the link drops or stops acknowledging; returns why."""
        writer = CommandWriter(
            lambda payload: client.write_gatt_char(BELT_COMMAND_UUID, payload, response=self.confirming),
            self.health, self.WRITE_TIMEOUT_S, self.MAX_WRITE_FAILURES, self._clock,
        )  # fmt: skip
        self.health.reset(self._clock())
        self._writer = writer
        write_task = asyncio.create_task(writer.run(), name="belt-writer")
        link_task = asyncio.create_task(self._disconnected.wait(), name="belt-link")
        try:
            await asyncio.wait({write_task, link_task}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (write_task, link_task):
                task.cancel()
            for task in (write_task, link_task):
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        if link_task.done() and not link_task.cancelled():
            return "belt disconnected"
        exc = write_task.exception() if write_task.done() and not write_task.cancelled() else None
        if isinstance(exc, WriteLinkBroken):
            self.reconnects += 1
            return f"belt not acknowledging ({exc})"
        if exc is not None:
            raise exc
        return "belt writer stopped"

    async def _connect_once(self) -> Any:
        self._state = "connecting"
        self._disconnected.clear()
        target: BLEDevice | str | None = self.address
        if target is None:
            log.info("scanning for %s ...", BELT_DEVICE_NAME)
            target = await find_belt()
            if target is None:
                raise TimeoutError(f"no {BELT_DEVICE_NAME} found")
        factory = self._client_factory or BleakClient
        client = factory(target, disconnected_callback=lambda _: self._disconnected.set())
        try:
            await client.connect()
            info = await client.read_gatt_char(BELT_INFO_UUID)
            if len(info) < 2:
                raise BleakError(f"belt info is {len(info)} bytes, expected [protocol_version, motor_count]")
        except BaseException:
            # The belt stops advertising while it's connected, so a client left open
            # here would keep the hub from ever finding it again.
            await self._disconnect_quietly(client)
            raise
        version, motors = info[:2]
        if motors != self.expected_motors:
            log.warning("belt reports %d motors but the hub expects %d", motors, self.expected_motors)
        self.confirming = _supports_write_with_response(client) is not False
        if not self.confirming:
            log.warning("belt command characteristic has no write-with-response: lost writes can't be detected")
        log.info("belt connected (%s, protocol v%d, %d motors)", client.address, version, motors)
        self._client = client
        self._state = "connected"
        return client

    async def _disconnect_quietly(self, client: Any) -> None:
        try:
            await asyncio.wait_for(client.disconnect(), self.DISCONNECT_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - best effort; the caller is already handling a failure
            log.warning("couldn't drop half-open belt connection: %s", str(exc) or type(exc).__name__)

    async def send(self, cmd: BeltCommand) -> None:
        """Queues the command for the writer and returns at once (never blocks the
        hub's loop). Dropped quietly while the belt isn't connected."""
        writer = self._writer
        if writer is None or self._state != "connected":
            return
        self._window_s = ttl_window_s(cmd)
        writer.post(cmd)

    async def close(self) -> None:
        client = self._client
        if client is not None and client.is_connected:
            try:
                seq = (self._writer.seq + 1) & 0xFF if self._writer is not None else 0
                payload = encode_belt_command(BeltCommand.off(self.expected_motors, ttl_ms=50), seq)
                await asyncio.wait_for(client.write_gatt_char(BELT_COMMAND_UUID, payload, response=False), 1.0)
            except Exception as exc:  # noqa: BLE001 - best effort; the belt's TTL stops it anyway
                log.warning("final belt 'off' failed: %s", str(exc) or type(exc).__name__)
            finally:
                await self._disconnect_quietly(client)
        self._state = "disconnected"
