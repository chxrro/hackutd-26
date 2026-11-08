"""Stand-in belt for development: remembers the last command so the debug window
and status can show it. Nothing physical happens."""

from __future__ import annotations

import asyncio
import logging
import time

from haptic_hub.protocol import BeltCommand, BeltMode, decode_belt_command, encode_belt_command

log = logging.getLogger(__name__)

_BARS = " ▁▂▃▄▅▆▇█"


def render(cmd: BeltCommand) -> str:
    """One-line picture of the belt, left to right, e.g. 'LEVELS [ ▃█▃ ]'."""
    if cmd.mode == BeltMode.FAULT:
        return "FAULT  [" + "!" * len(cmd.levels) + "]"
    return f"{cmd.mode.name:<6} [" + "".join(_BARS[round(lv / 255 * 8)] for lv in cmd.levels) + "]"


class SimBelt:
    state = "simulated"
    fault: str | None = None  # nothing to lose writes on

    def __init__(self, motors: int, log_interval_s: float = 0.5) -> None:
        self.last: BeltCommand = BeltCommand.off(motors)
        self.sent = 0
        self._log_interval_s = log_interval_s
        self._logged: str | None = None
        self._logged_at = 0.0

    async def run(self) -> None:
        await asyncio.Event().wait()  # nothing to maintain

    async def send(self, cmd: BeltCommand) -> None:
        # Round-trip through the wire format so the simulator exercises encoding too.
        self.last, _ = decode_belt_command(encode_belt_command(cmd, self.sent))
        self.sent += 1
        picture, now = render(self.last), time.monotonic()
        if picture != self._logged and now - self._logged_at >= self._log_interval_s:
            log.info("belt %s", picture)
            self._logged, self._logged_at = picture, now

    async def close(self) -> None:
        pass
