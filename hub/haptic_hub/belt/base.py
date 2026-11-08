"""Belt interface: something that accepts a BeltCommand."""

from __future__ import annotations

from typing import Protocol

from haptic_hub.protocol import BeltCommand


class Belt(Protocol):
    @property
    def state(self) -> str:
        """'connected' | 'connecting' | 'disconnected' | 'simulated'."""

    @property
    def fault(self) -> str | None:
        """Why the belt can't be confirmed to be following the hub although the
        link is up (e.g. "belt not acknowledging"), else None. Optional: the hub
        treats a belt without this attribute as never reporting one."""

    async def run(self) -> None:
        """Maintain the connection (reconnecting as needed) until cancelled."""

    async def send(self, cmd: BeltCommand) -> None:
        """Best effort; must not raise if the belt is disconnected, and must not
        block the hub's loop for long (BleBelt only queues the command)."""

    async def close(self) -> None:
        """Send a final 'off' if possible and disconnect."""
