"""Knowing whether the belt is actually getting our commands.

A BLE write without response can vanish without any error on the hub. The belt
firmware notices (its TTL runs out and it plays the fault pattern), but the hub
would keep telling the phone it's healthy. So the hub writes *with* response:
the belt's BLE stack acknowledges every command (ATT Write Response). The
firmware has always accepted both write types (docs/protocol.md section 3), so
this needs no firmware change.

Two pieces, kept free of bleak so the simulator and tests drive them directly:

  WriteHealth    bookkeeping: when did the belt last acknowledge a command, and
                 is that longer ago than the command's TTL?
  CommandWriter  sends the newest command, one write in flight at a time. The
                 hub's 15 Hz loop only posts commands, so a slow or stuck write
                 never stalls it.

What an acknowledgement proves: the belt's BLE stack received the bytes. It does
not prove the firmware's main loop applied them (its event queue drops events
when full), or that the motors moved.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from haptic_hub.protocol import BeltCommand, encode_belt_command

log = logging.getLogger(__name__)

NOT_ACKNOWLEDGING_REASON = "belt not acknowledging"

# Firmware clamps ttl_ms to this range (esp32/lib/belt_core); the window follows suit.
FIRMWARE_TTL_MIN_S, FIRMWARE_TTL_MAX_S = 0.05, 1.0


def ttl_window_s(cmd: BeltCommand) -> float:
    """How long the belt keeps following `cmd` before its own failsafe kicks in."""
    return min(FIRMWARE_TTL_MAX_S, max(FIRMWARE_TTL_MIN_S, cmd.ttl_ms / 1000))


class WriteHealth:
    """Acknowledged-write bookkeeping for one belt connection.

    Unhealthy once no write has been acknowledged for longer than the TTL of the
    commands being sent: past that point the belt has stopped following the hub
    (its firmware plays the fault pattern, or it never got the command at all).
    It stays unhealthy until `recover_after` writes in a row are acknowledged, so
    a link that only gets the odd write through doesn't flicker back to healthy.
    """

    def __init__(self, recover_after: int = 3) -> None:
        if recover_after < 1:
            raise ValueError("recover_after must be >= 1")
        self.recover_after = recover_after
        self.reset(0.0)

    def reset(self, now: float) -> None:
        """A new connection: the window starts now."""
        self.last_ok = now
        self.faulted = False
        self.ok_streak = 0
        self.failures_in_row = 0
        self.acked = 0
        self.failed_writes = 0

    def ok(self, now: float) -> None:
        self.last_ok = now
        self.acked += 1
        self.failures_in_row = 0
        self.ok_streak += 1
        if self.faulted and self.ok_streak >= self.recover_after:
            self.faulted = False
            log.info("belt acknowledging writes again")

    def failed(self) -> None:
        """A write raised an error (not a timeout: that ends the connection)."""
        self.failed_writes += 1
        self.failures_in_row += 1
        self.ok_streak = 0

    def check(self, now: float, window_s: float) -> str | None:
        """The fault reason, or None while the belt is confirming commands in time."""
        if not self.faulted and now - self.last_ok > window_s:
            self.faulted = True
            self.ok_streak = 0
            log.warning("belt hasn't acknowledged a command for %.2f s (TTL %.2f s)", now - self.last_ok, window_s)
        return NOT_ACKNOWLEDGING_REASON if self.faulted else None


class WriteLinkBroken(Exception):
    """The connection's write path is no longer usable; reconnect."""


class CommandWriter:
    """Writes the newest posted command, one at a time, and records the outcome.

    `write(payload)` must return once the belt acknowledged the payload, and raise
    on an error. Commands posted while a write is in flight replace each other:
    the belt only ever needs the latest one.

    `run()` returns only by raising WriteLinkBroken: a write that isn't answered
    within `timeout_s`, or `max_failures` errors in a row. A timed-out write can't
    be safely retried on the same connection (bleak's CoreBluetooth backend keeps
    one pending future per characteristic, so a late answer could be taken for the
    next write's), so the owner drops the connection and reconnects.
    """

    def __init__(
        self,
        write: Callable[[bytes], Awaitable[None]],
        health: WriteHealth,
        timeout_s: float = 1.0,
        max_failures: int = 15,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._write = write
        self.health = health
        self.timeout_s = timeout_s
        self.max_failures = max_failures
        self._clock = clock
        self._pending: BeltCommand | None = None
        self._wake = asyncio.Event()
        self.seq = 0
        self.writes = 0  # writes started
        self.superseded = 0  # posted commands replaced before they were written

    def post(self, cmd: BeltCommand) -> None:
        if self._pending is not None:
            self.superseded += 1
        self._pending = cmd
        self._wake.set()

    async def run(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            cmd, self._pending = self._pending, None
            if cmd is None:
                continue
            self.seq = (self.seq + 1) & 0xFF
            self.writes += 1
            try:
                await asyncio.wait_for(self._write(encode_belt_command(cmd, self.seq)), self.timeout_s)
            except TimeoutError:
                raise WriteLinkBroken(f"write not acknowledged within {self.timeout_s:g} s") from None
            except Exception as exc:  # noqa: BLE001 - this task must keep going; errors are counted
                self.health.failed()
                if self.health.failures_in_row == 1:
                    log.warning("belt write failed: %s", str(exc) or type(exc).__name__)
                if self.health.failures_in_row >= self.max_failures:
                    raise WriteLinkBroken(f"{self.health.failures_in_row} belt writes failed in a row") from exc
            else:
                self.health.ok(self._clock())
