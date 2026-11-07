"""Belt interface: something that accepts a BeltCommand."""

from __future__ import annotations

from typing import Protocol

from haptic_hub.protocol import BeltCommand


class Belt(Protocol):
    @property
    def state(self) -> str:
        """'connected' | 'connecting' | 'disconnected' | 'simulated'."""

    async def run(self) -> None:
        """Maintain the connection (reconnecting as needed) until cancelled."""

    async def send(self, cmd: BeltCommand) -> None:
        """Best effort; must not raise if the belt is disconnected."""

    async def close(self) -> None:
        """Send a final 'off' if possible and disconnect."""
