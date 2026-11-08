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
    # Which detector feeds the tracker: "yolo" = yolo_model (80 COCO classes),
    # "openvocab" = YOLOE prompted with openvocab_prompts (perception/openvocab.py).
    detector: str = "yolo"
    openvocab_model: str = "yoloe-26s-seg.pt"  # yoloe-26n-seg.pt: ~4 ms/frame faster alone, found less (README)
    # Text prompts. A box's label is the prompt it matched; "person" is always added.
    openvocab_prompts: tuple[str, ...] = (
        "person", "car", "bicycle", "pole", "utility pole", "lamp post", "bollard", "post",
        "door", "glass door", "stairs", "curb", "raised curb", "trash can", "sign",
        "low wall", "bench", "chair", "table", "fire hydrant", "tree trunk",
    )  # fmt: skip
    openvocab_conf: float = 0.25


@dataclass
class ClassifierConfig:
    """Person vs. object labels for the tracked boxes (see perception/classify.py).

    "local" reuses the detector's COCO class. "nim" asks a Nemotron vision model
    behind any OpenAI-compatible endpoint (vLLM/NIM on Brev, or build.nvidia.com),
    in the background and at a capped rate; YOLO stays the fast path.
    """

    backend: str = "local"  # "local" | "nim"
    # OpenAI-compatible base URL, e.g. http://localhost:8000/v1 for a forwarded Brev port.
    # "" -> env NIM_BASE_URL, else NVIDIA's hosted API.
    base_url: str = ""
    model: str = ""  # "" -> env NIM_MODEL, else the hosted default / the server's first listed model
    max_hz: float = 2.0  # cloud requests per second at most (one in flight at a time)
    timeout_s: float = 2.5  # per HTTP request
    stale_s: float = 3.0  # boxes waiting this long without a cloud reply -> fault
    image_width: int = 512  # frame is downscaled to this width before upload
    jpeg_quality: int = 70
    max_boxes: int = 8  # largest boxes sent per request; the rest keep local labels
    label_ttl_s: float = 10.0  # forget a track's cloud label after this long unseen


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
    result_max_age_s: float = 1.0  # result's frame received longer ago than this -> fault


@dataclass
class HubConfig:
    perception: PerceptionConfig = field(default_factory=PerceptionConfig)
    classifier: ClassifierConfig = field(default_factory=ClassifierConfig)
    haptics: HapticsConfig = field(default_factory=HapticsConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    ws_host: str = "0.0.0.0"
    ws_port: int = 8765
    status_hz: float = 5.0
