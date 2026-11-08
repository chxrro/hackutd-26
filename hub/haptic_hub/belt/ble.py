"""The real belt: ESP32 over Bluetooth LE (bleak)."""

from __future__ import annotations

import asyncio
import logging

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.exc import BleakError

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


class BleBelt:
    RECONNECT_DELAY_S = 1.0  # first retry; doubles up to MAX_RECONNECT_DELAY_S
    MAX_RECONNECT_DELAY_S = 10.0
    DISCONNECT_TIMEOUT_S = 5.0

    def __init__(self, expected_motors: int, address: str | None = None) -> None:
        self.expected_motors = expected_motors
        self.address = address
        self._client: BleakClient | None = None
        self._state = "disconnected"
        self._seq = 0
        self._disconnected = asyncio.Event()

    @property
    def state(self) -> str:
        return self._state

    async def run(self) -> None:
        backoff = self.RECONNECT_DELAY_S
        while True:
            try:
                await self._connect_once()
                backoff = self.RECONNECT_DELAY_S
                await self._disconnected.wait()
                log.warning("belt disconnected; reconnecting")
            except Exception as exc:  # noqa: BLE001 - if this task dies, the belt never hears from the hub again
                log.warning("belt connection failed: %s", str(exc) or type(exc).__name__)
            self._client = None
            self._state = "disconnected"
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self.MAX_RECONNECT_DELAY_S)

    async def _connect_once(self) -> None:
        self._state = "connecting"
        self._disconnected.clear()
        target: BLEDevice | str | None = self.address
        if target is None:
            log.info("scanning for %s ...", BELT_DEVICE_NAME)
            target = await find_belt()
            if target is None:
                raise TimeoutError(f"no {BELT_DEVICE_NAME} found")
        client = BleakClient(target, disconnected_callback=lambda _: self._disconnected.set())
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
        log.info("belt connected (%s, protocol v%d, %d motors)", client.address, version, motors)
        self._client = client
        self._state = "connected"

    async def _disconnect_quietly(self, client: BleakClient) -> None:
        try:
            await asyncio.wait_for(client.disconnect(), self.DISCONNECT_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - best effort; the caller is already handling a failure
            log.warning("couldn't drop half-open belt connection: %s", str(exc) or type(exc).__name__)

    async def send(self, cmd: BeltCommand) -> None:
        client = self._client
        if client is None or not client.is_connected:
            return
        self._seq = (self._seq + 1) & 0xFF
        try:
            await client.write_gatt_char(BELT_COMMAND_UUID, encode_belt_command(cmd, self._seq), response=False)
        except (BleakError, OSError) as exc:
            log.warning("belt write failed: %s", exc)

    async def close(self) -> None:
        client = self._client
        if client is not None and client.is_connected:
            try:
                await self.send(BeltCommand.off(self.expected_motors, ttl_ms=50))
            finally:
                await client.disconnect()
        self._state = "disconnected"
