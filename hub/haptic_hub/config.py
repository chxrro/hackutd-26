"""Tunable settings, grouped by stage. Defaults are starting points, not validated values."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PerceptionConfig:
    sectors: int = 5  # must equal the belt's motor count
    device: str = "auto"  # "auto" | "mps" | "cuda" | "cpu"
    # Depth Anything V2 metric model (outputs meters). Use the Outdoor variant outside.
    depth_model: str = "depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf"
    yolo_model: str = "yolo11n.pt"
    infer_width: int = 384  # frames are resized to this width before inference
    # Horizontal band of the image used for depth risk (fractions of height).
    # The bottom of the frame is mostly floor, which always looks close.
    band_top: float = 0.20
    band_bottom: float = 0.75
    depth_percentile: float = 5.0  # "nearest" = this percentile of the sector's depth
    near_m: float = 0.6  # at or below this distance: full risk
    far_m: float = 2.5  # beyond this distance: no risk
    ttc_near_s: float = 1.0  # time to collision at or below this: full risk
    ttc_far_s: float = 3.0  # beyond this: no risk
    min_box_height: float = 0.08  # ignore detections smaller than this fraction of frame height
    detector_conf: float = 0.35


@dataclass
class HapticsConfig:
    send_hz: float = 15.0  # belt heartbeat rate; must be well above 1000/ttl_ms
    ttl_ms: int = 250  # belt stops/faults if it hears nothing for this long
    release: float = 0.6  # 0..1, how slowly alerts fade (rising risk applies instantly)
    deadband: float = 0.12  # risks below this are not felt
    min_level: int = 90  # weakest PWM that reliably spins a coin motor
    max_level: int = 255


@dataclass
class SafetyConfig:
    frame_stale_s: float = 1.0  # no new camera frame for this long -> fault
    result_stale_s: float = 1.5  # no perception result for this long -> fault


@dataclass
class HubConfig:
    perception: PerceptionConfig = field(default_factory=PerceptionConfig)
    haptics: HapticsConfig = field(default_factory=HapticsConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    ws_host: str = "0.0.0.0"
    ws_port: int = 8765
    status_hz: float = 5.0
