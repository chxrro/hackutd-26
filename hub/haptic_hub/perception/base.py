"""Perception interface: one frame in, one risk per belt sector out."""

from __future__ import annotations

from typing import Protocol

from haptic_hub.types import Frame, PerceptionResult


class Perception(Protocol):
    name: str

    def process(self, frame: Frame) -> PerceptionResult:
        """Blocking; the hub calls it from a worker thread. Raise on failure:
        the hub turns exceptions into a fault rather than silence."""


def resolve_device(device: str) -> str:
    """'auto' -> best available torch device."""
    if device != "auto":
        return device
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"
