"""Where haptic commands go: the real BLE belt or a simulator."""

from haptic_hub.belt.base import Belt
from haptic_hub.belt.sim import SimBelt

__all__ = ["Belt", "SimBelt"]
