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
        cv2.imshow(WINDOW, render(frame, result, cmd, reason, self.width))
        return cv2.waitKey(1) & 0xFF not in (ord("q"), 27)

    def close(self) -> None:
        cv2.destroyWindow(WINDOW)


def render(
    frame: Frame,
    result: PerceptionResult,
    cmd: BeltCommand,
    reason: str | None,
    width: int = 480,
    panel: bool = True,
    banner: bool = True,
) -> np.ndarray:
    """The --show picture as a BGR image: frame scaled to `width`, sector lines, labelled
    boxes, and (optionally) the motor panel below and the mode/fault banner on top.
    Pure: no window, safe to call from any thread."""
    img = frame.image
    scale = width / img.shape[1]
    img = cv2.resize(img, (width, round(img.shape[0] * scale)))
    h, w = img.shape[:2]
    n = len(result.sectors)

    infer_w, _ = result.debug.get("infer_size", (w, h))
    box_scale = w / infer_w
    for box, text, color in _box_labels(result.debug):
        p1 = (int(box.x1 * box_scale), int(box.y1 * box_scale))
        p2 = (int(box.x2 * box_scale), int(box.y2 * box_scale))
        cv2.rectangle(img, p1, p2, color, 2)
        cv2.putText(img, text, (p1[0], p1[1] - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
    if banner and (classifier := _classifier_line(result.debug)) is not None:
        cv2.putText(img, classifier, (6, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)

    for x0, _ in sector_edges(w, n)[1:]:
        cv2.line(img, (x0, 0), (x0, h), (255, 255, 255), 1)

    if banner:
        text = f"{cmd.mode.name}  {result.inference_ms:.0f} ms  [{frame.source}]"
        if reason:
            text = f"FAULT: {reason}"
        cv2.putText(img, text, (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0) if not reason else (0, 0, 255), 2)

    if not panel:
        return img
    bar_h = 70
    strip = np.zeros((bar_h, w, 3), np.uint8)
    for i, ((x0, x1), sector) in enumerate(zip(sector_edges(w, n), result.sectors, strict=True)):
        level = cmd.levels[i] / 255
        color = (0, 0, 255) if cmd.mode == BeltMode.LEVELS else (120, 120, 120)
        cv2.rectangle(strip, (x0 + 3, int(bar_h * (1 - level))), (x1 - 3, bar_h), color, -1)
        label = f"{sector.nearest_m:.1f}m" if sector.nearest_m is not None else "-"
        if sector.ttc_s is not None:
            label += f" {sector.ttc_s:.1f}s"
        cv2.putText(strip, label, (x0 + 4, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
    return np.vstack([img, strip])


PERSON_COLOR = (255, 0, 255)  # magenta
OBJECT_COLOR = (0, 200, 255)  # orange


def _box_labels(debug: dict):
    """(box, text, BGR color) per detection. With a classifier (perception/classify.py)
    each box says PERSON or object, and where the label came from."""
    classified = debug.get("classified")
    if classified is None:
        for box in debug.get("boxes", []):
            yield box, f"{box.label} #{box.track_id}", OBJECT_COLOR
        return
    for c in classified:
        box = c.box
        name = "PERSON" if c.kind == "person" else f"object:{box.label}"
        yield box, f"{name} #{box.track_id} ({c.origin})", PERSON_COLOR if c.kind == "person" else OBJECT_COLOR


def _classifier_line(debug: dict) -> str | None:
    status = debug.get("classifier")
    if not status:
        return None
    if status.get("backend") != "nim":
        return "classifier: local (detector class)"
    parts = ["classifier: nim"]
    if status.get("rtt_ms") is not None:
        parts.append(f"rtt {status['rtt_ms']:.0f} ms")
    if status.get("last_ok_age_s") is not None:
        parts.append(f"last reply {status['last_ok_age_s']:.1f} s ago")
    if status.get("error"):
        parts.append(f"ERR {status['error'][:40]}")
    return "  ".join(parts)
