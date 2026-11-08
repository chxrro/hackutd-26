"""Real perception: fuses a depth map (static obstacles) with tracked objects
(things coming toward you) into one risk per belt sector."""

from __future__ import annotations

import time

import cv2

from haptic_hub.config import PerceptionConfig
from haptic_hub.perception.geometry import box_sectors, depth_sector_nearest, linear_risk
from haptic_hub.perception.models import DepthEstimator, ObjectTracker
from haptic_hub.perception.ttc import TtcEstimator
from haptic_hub.types import Frame, PerceptionResult, SectorRisk


class VisionPerception:
    name = "vision"

    def __init__(
        self,
        cfg: PerceptionConfig,
        depth: DepthEstimator | None,
        tracker: ObjectTracker | None,
    ) -> None:
        if depth is None and tracker is None:
            raise ValueError("need at least a depth model or a detector")
        self.cfg = cfg
        self._depth = depth
        self._tracker = tracker
        self._ttc = TtcEstimator(robust=cfg.robust_ttc)

    def process(self, frame: Frame) -> PerceptionResult:
        started = time.perf_counter()
        cfg, n = self.cfg, self.cfg.sectors

        h0, w0 = frame.image.shape[:2]
        scale = cfg.infer_width / w0
        bgr = cv2.resize(frame.image, (cfg.infer_width, max(1, round(h0 * scale))), interpolation=cv2.INTER_AREA)
        h, w = bgr.shape[:2]

        risk = [0.0] * n
        nearest: list[float | None] = [None] * n
        ttc: list[float | None] = [None] * n
        label: list[str | None] = [None] * n
        evidence = ["unknown"] * n
        motion = ["unknown"] * n
        track_ids: list[int | None] = [None] * n
        debug: dict = {"infer_size": (w, h)}

        if self._depth is not None:
            depth = self._depth.estimate(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
            debug["depth"] = depth
            for i, d in enumerate(depth_sector_nearest(depth, n, cfg.band_top, cfg.band_bottom, cfg.depth_percentile)):
                nearest[i] = d
                risk[i] = linear_risk(d, cfg.near_m, cfg.far_m)
                if risk[i] > 0:
                    evidence[i] = "depth"

        if self._tracker is not None:
            boxes = self._tracker.track(bgr)
            debug["boxes"] = boxes
            for box in boxes:
                if box.height / h < cfg.min_box_height:
                    continue
                box_ttc = None
                if box.track_id is not None:
                    clipped = box.x1 <= 1 or box.y1 <= 1 or box.x2 >= w - 1 or box.y2 >= h - 1
                    box_ttc = self._ttc.update(box.track_id, frame.received_at, box.height, clipped=clipped)
                box_risk = linear_risk(box_ttc, cfg.ttc_near_s, cfg.ttc_far_s)
                for s in box_sectors(box.x1, box.x2, w, n):
                    if box_risk > risk[s]:
                        risk[s] = box_risk
                        label[s] = box.label
                        evidence[s] = "box_ttc"
                        motion[s] = "approaching"
                        track_ids[s] = box.track_id
                    if box_ttc is not None and (ttc[s] is None or box_ttc < ttc[s]):
                        ttc[s] = box_ttc
            self._ttc.prune(frame.received_at)

        sectors = tuple(SectorRisk(risk[i], nearest[i], ttc[i], label[i], evidence[i], motion[i], track_ids[i]) for i in range(n))
        return PerceptionResult(
            frame_seq=frame.seq,
            frame_received_at=frame.received_at,
            sectors=sectors,
            inference_ms=(time.perf_counter() - started) * 1000,
            debug=debug,
        )
