"""Tunable settings, grouped by stage. Defaults are starting points, not validated values."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FlowConfig:
    """Optical-flow motion for tracked boxes (perception/flow.py, --motion flow).

    Starting points from synthetic tests and Nathan's 1 fps webcam experiment,
    not validated values. Re-tune on replayed hub clips.
    """

    # Reference frame: the stored frame closest to target_dt_s old, within [min, max].
    # Short enough that a walking-pace crossing moves < ~25 px at 384 px width
    # (LK fails beyond that on synthetic tests), long enough to measure expansion.
    target_dt_s: float = 0.15
    min_dt_s: float = 0.08
    max_dt_s: float = 0.4
    # Features and Lucas-Kanade
    max_corners: int = 120  # per region (background, each box)
    corner_quality: float = 0.005
    lk_window: int = 21
    lk_levels: int = 3
    max_fb_error_px: float = 1.0  # forward-backward check
    # Grow each box by this fraction. 0 for YOLO's tight boxes: on synthetic tests a
    # 0.1 margin let background points bias the scale low and fail the inlier check.
    box_margin: float = 0.0
    # Similarity fit (RANSAC)
    ransac_px: float = 1.5
    min_inliers: int = 8
    min_background_inliers: int = 12
    min_inlier_fraction: float = 0.7
    min_spread_px: float = 6.0
    min_scale: float = 0.5
    max_scale: float = 2.0
    # Smoothing: median over this window, needing at least min_samples measurements
    smooth_window_s: float = 0.6
    min_samples: int = 2
    # Crossing: relative to the background, projected ahead lookahead_s
    min_cross_speed: float = 0.1  # image widths per second
    lookahead_s: float = 1.0
    crossing_min_box_height: float = 0.25  # fraction of frame height; a proxy for "near"
    corridor: tuple[float, float] = (0.35, 0.65)  # image band treated as the wearer's heading
    crossing_risk: float = 0.5  # risk for a near box whose path crosses the corridor


@dataclass
class PerceptionConfig:
    sectors: int = 5  # must equal the belt's motor count
    device: str = "auto"  # "auto" | "mps" | "cuda" | "cpu"
    # Depth Anything V2 metric model (outputs meters). Use the Outdoor variant outside.
    depth_model: str = "depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf"
    yolo_model: str = "yolo11n.pt"
    motion: str = "off"  # "off" | "flow": optical-flow approach/crossing on tracked boxes
    flow: FlowConfig = field(default_factory=FlowConfig)
    infer_width: int = 384  # frames are resized to this width before inference
    # Which depth pixels can be an obstacle:
    #   "floor" (default): back-project the depth map with the camera intrinsics, fit the
    #       floor plane (RANSAC), and keep points between obstacle_min_height_m above the
    #       floor and obstacle_max_above_eye_m above the camera. Rejects floor and ceiling
    #       whatever the head pitch, and sees low obstacles. Needs hfov_deg.
    #   "band" (previous behavior): fixed image rows band_top..band_bottom; sees the
    #       ceiling, or the floor when the head tilts down, and misses low obstacles.
    depth_geometry: str = "floor"
    # Horizontal camera field of view. ASSUMED (~100 deg diagonal for the 9:16 portrait
    # stream), not measured on the glasses. Measure it and set it here.
    hfov_deg: float = 60.0
    eye_height_m: float = 1.6  # camera height used when no floor can be fitted (assumed)
    obstacle_min_height_m: float = 0.15  # lower is floor (depth noise, curbs below this are missed)
    obstacle_max_above_eye_m: float = 0.3  # higher can't be walked into (ceiling, overhead signs)
    obstacle_percentile: float = 2.5  # % of a sector's pixels an obstacle must cover (floor mode)
    # Floor fit: plausible floors only, so a wall or ceiling is never taken for the floor.
    floor_max_tilt_deg: float = 50.0  # normal vs. a level camera's up (head pitch/roll limit)
    floor_height_range: tuple[float, float] = (0.9, 2.1)  # plausible camera heights above it, m
    floor_tol_m: float = 0.05  # inlier distance from the plane: tol_m + tol_rel * range
    floor_tol_rel: float = 0.03
    floor_min_inlier_frac: float = 0.08  # of the sampled pixels
    floor_hold_s: float = 0.5  # reuse the last fit this long when none is found, then the prior
    # Trust check: if more of the sampled depth points than this lie below the floor, the depth
    # map doesn't fit one plane with these intrinsics; that frame falls back to band mode.
    floor_max_below_frac: float = 0.05
    floor_stride: int = 4  # sample every n-th pixel for the fit (2 for obstacles)
    # Band mode: horizontal band of the image used for depth risk (fractions of height).
    band_top: float = 0.20
    band_bottom: float = 0.75
    depth_percentile: float = 5.0  # band mode: "nearest" = this percentile of the sector's depth
    near_m: float = 0.6  # at or below this distance: full risk
    far_m: float = 2.5  # beyond this distance: no risk
    ttc_near_s: float = 1.0  # time to collision at or below this: full risk
    ttc_far_s: float = 3.0  # beyond this: no risk
    min_box_height: float = 0.08  # ignore detections smaller than this fraction of frame height
    detector_conf: float = 0.35  # boxes at or above this drive the belt (TTC risk)
    # Boxes scoring from uncertain_conf up to the confident threshold don't drive the belt,
    # but are reported: SectorRisk.uncertain and status sectors[i].uncertain. Never silence.
    uncertain_conf: float = 0.15
    # Box-growth TTC (perception/ttc.py): fit 1/height over this window (no lag for a
    # constant-speed approach), need this many samples spanning min_span_s, and use the
    # closing rate minus ttc_sigma standard errors (noisy fits read as farther away).
    # The window trades jitter rejection (slope error ~ window^-1.5) against how long an
    # alert lingers after an approach stops (cut by a change-point check in ttc.py).
    ttc_window_s: float = 1.0
    ttc_min_samples: int = 5
    ttc_min_span_s: float = 0.25
    ttc_sigma: float = 1.0
    ttc_noise_px: float = 1.0  # assumed minimum box-height noise (pixels at infer_width)
    ttc_change_sigma: float = 1.5  # confidence needed to cap the rate at the recent half-window's
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

    @property
    def confident_conf(self) -> float:
        """Score at which the selected detector's boxes drive the belt."""
        return self.openvocab_conf if self.detector == "openvocab" else self.detector_conf

    @property
    def tracker_conf(self) -> float:
        """Score the detector is run with: low enough to also return uncertain boxes."""
        return min(self.confident_conf, self.uncertain_conf)


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
    # While alerts are paused: one low pulse on the outer motors this often, so the
    # paused state is never silent for long (cues.py). 0 = off, else >= 2 s.
    pause_reminder_s: float = 15.0


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
