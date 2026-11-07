"""Optional debug window (--show): camera frame, sector lines, detections and
what each motor is doing. Must be called from the main thread (macOS)."""

from __future__ import annotations

import cv2
import numpy as np

from haptic_hub.perception.geometry import sector_edges
from haptic_hub.protocol import BeltCommand, BeltMode
from haptic_hub.types import Frame, PerceptionResult

WINDOW = "haptic hub"


class DebugView:
    def __init__(self, width: int = 480) -> None:
        self.width = width

    def show(self, frame: Frame, result: PerceptionResult, cmd: BeltCommand, reason: str | None) -> bool:
        """Draws one frame. Returns False if the user pressed q or Esc."""
        img = frame.image
        scale = self.width / img.shape[1]
        img = cv2.resize(img, (self.width, round(img.shape[0] * scale)))
        h, w = img.shape[:2]
        n = len(result.sectors)

        infer_w, _ = result.debug.get("infer_size", (w, h))
        box_scale = w / infer_w
        for box in result.debug.get("boxes", []):
            p1 = (int(box.x1 * box_scale), int(box.y1 * box_scale))
            p2 = (int(box.x2 * box_scale), int(box.y2 * box_scale))
            cv2.rectangle(img, p1, p2, (0, 200, 255), 2)
            cv2.putText(img, f"{box.label} #{box.track_id}", (p1[0], p1[1] - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 200, 255), 1)

        for x0, _ in sector_edges(w, n)[1:]:
            cv2.line(img, (x0, 0), (x0, h), (255, 255, 255), 1)

        bar_h = 70
        panel = np.zeros((bar_h, w, 3), np.uint8)
        for i, ((x0, x1), sector) in enumerate(zip(sector_edges(w, n), result.sectors, strict=True)):
            level = cmd.levels[i] / 255
            color = (0, 0, 255) if cmd.mode == BeltMode.LEVELS else (120, 120, 120)
            cv2.rectangle(panel, (x0 + 3, int(bar_h * (1 - level))), (x1 - 3, bar_h), color, -1)
            text = f"{sector.nearest_m:.1f}m" if sector.nearest_m is not None else "-"
            if sector.ttc_s is not None:
                text += f" {sector.ttc_s:.1f}s"
            cv2.putText(panel, text, (x0 + 4, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)

        banner = f"{cmd.mode.name}  {result.inference_ms:.0f} ms  [{frame.source}]"
        if reason:
            banner = f"FAULT: {reason}"
        cv2.putText(img, banner, (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0) if not reason else (0, 0, 255), 2)

        cv2.imshow(WINDOW, np.vstack([img, panel]))
        return cv2.waitKey(1) & 0xFF not in (ord("q"), 27)

    def close(self) -> None:
        cv2.destroyWindow(WINDOW)
