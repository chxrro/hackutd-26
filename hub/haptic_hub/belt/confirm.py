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

Firmware from 2026-11-08 on also has a status characteristic (notify): its mode,
and the seq of the last command it applied. StatusHealth checks those against
what the hub wrote. A matching status shows the main loop applied a recent
command, which is stronger than a write acknowledgement (still not proof that
the motors moved).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from haptic_hub.protocol import BeltCommand, BeltFirmwareMode, BeltMode, BeltStatus, encode_belt_command

log = logging.getLogger(__name__)

NOT_ACKNOWLEDGING_REASON = "belt not acknowledging"
# From the belt's status characteristic (docs/protocol.md section 3, "Status").
TTL_EXPIRED_REASON = "belt TTL expired"
FAULT_PATTERN_REASON = "belt playing fault pattern"
NOT_APPLYING_REASON = "belt not applying commands"
STATUS_STALE_REASON = "belt status stale"
BELT_STATUS_REASONS = (TTL_EXPIRED_REASON, FAULT_PATTERN_REASON, NOT_APPLYING_REASON, STATUS_STALE_REASON)

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
        on_write: Callable[[int, BeltCommand, float], None] | None = None,
    ) -> None:
        """on_write(seq, cmd, now) is called just before each write (StatusHealth.wrote)."""
        self._write = write
        self._on_write = on_write
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
            if self._on_write is not None:
                self._on_write(self.seq, cmd, self._clock())
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


_MODE_FOR = {BeltFirmwareMode.OFF: BeltMode.OFF, BeltFirmwareMode.LEVELS: BeltMode.LEVELS, BeltFirmwareMode.FAULT: BeltMode.FAULT}
_NOT_FOLLOWING = (BeltFirmwareMode.EXPIRED, BeltFirmwareMode.DISCONNECTED, BeltFirmwareMode.DORMANT)


class StatusHealth:
    """Checks the belt's own status reports against the commands the hub wrote,
    for one connection.

    A status *matches* when its applied seq is a command written on this
    connection less than one TTL (+ MATCH_SLACK_S) before the status arrived, the
    firmware says it applied it within the TTL, and its mode is that command's
    mode. Anything else is a reason (see BELT_STATUS_REASONS). Reasons count only
    once armed: after the first matching status, or GRACE_S after the first write
    (the status read on connect predates the hub's commands). Once set, a reason
    clears after `recover_after` matching statuses in a row.
    """

    STALE_S = 1.0  # twice the firmware's promised 500 ms notify interval
    GRACE_S = 1.0
    MATCH_SLACK_S = 0.25  # BLE write + notification latency on top of the TTL

    def __init__(self, recover_after: int = 2) -> None:
        if recover_after < 1:
            raise ValueError("recover_after must be >= 1")
        self.recover_after = recover_after
        self.reset()

    def reset(self) -> None:
        self._sent: dict[int, tuple[float, BeltMode]] = {}
        self.first_write_at: float | None = None
        self.last: BeltStatus | None = None
        self.last_at: float | None = None
        self.verdict: str | None = None  # the latest status on its own (None = matching)
        self.ever_matched = False
        self.good_streak = 0
        self.faulted: str | None = None
        self.statuses = 0
        self.mismatches = 0

    def wrote(self, seq: int, cmd: BeltCommand, now: float) -> None:
        self._sent[seq & 0xFF] = (now, cmd.mode)
        if self.first_write_at is None:
            self.first_write_at = now

    def judge(self, st: BeltStatus, now: float, window_s: float) -> str | None:
        """Why this status doesn't confirm the hub's commands, or None if it matches."""
        if st.mode in _NOT_FOLLOWING:
            return TTL_EXPIRED_REASON
        if st.mode == BeltFirmwareMode.IDLE:
            return NOT_APPLYING_REASON
        sent = self._sent.get(st.seq)
        if sent is None or now - sent[0] > window_s + self.MATCH_SLACK_S or st.command_age_ms > window_s * 1000:
            return NOT_APPLYING_REASON
        if st.mode == BeltFirmwareMode.FAULT and sent[1] != BeltMode.FAULT:
            return FAULT_PATTERN_REASON
        if _MODE_FOR.get(st.mode) != sent[1]:
            return NOT_APPLYING_REASON
        return None

    def on_status(self, st: BeltStatus, now: float, window_s: float) -> None:
        self.statuses += 1
        self.verdict = self.judge(st, now, window_s)
        self.last, self.last_at = st, now
        if self.verdict is None:
            self.ever_matched = True
            self.good_streak += 1
        else:
            self.good_streak = 0
            self.mismatches += 1

    def armed(self, now: float) -> bool:
        if self.ever_matched:
            return True
        return self.first_write_at is not None and now - self.first_write_at > self.GRACE_S

    def check(self, now: float) -> str | None:
        """The current status reason, or None (also before the checks are armed)."""
        if not self.armed(now):
            return None
        if self.last_at is None or now - self.last_at > self.STALE_S:
            reason: str | None = STATUS_STALE_REASON
            self.good_streak = 0
        else:
            reason = self.verdict
        if reason is not None:
            if reason != self.faulted:
                log.warning("%s (last status: %s)", reason, self.last)
            self.faulted = reason
        elif self.faulted is not None and self.good_streak >= self.recover_after:
            log.info("belt status confirms commands again")
            self.faulted = None
        return self.faulted

    def confirming(self, now: float) -> bool:
        """A fresh, matching status: the firmware applied one of the hub's recent commands."""
        return (
            self.faulted is None and self.verdict is None and self.ever_matched
            and self.last_at is not None and now - self.last_at <= self.STALE_S
        )  # fmt: skip
