"""Where frames come from: the glasses (via the phone) or a dev webcam/video."""

from haptic_hub.sources.base import FrameSource, LatestFrameSlot, StatusSink
from haptic_hub.sources.camera import OpenCVSource
from haptic_hub.sources.glasses import GlassesServer

__all__ = ["FrameSource", "GlassesServer", "LatestFrameSlot", "OpenCVSource", "StatusSink"]
