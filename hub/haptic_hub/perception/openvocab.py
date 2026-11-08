"""Open-vocabulary detector + ByteTrack: finds walking hazards that COCO has no
class for (poles, bollards, doors, stairs, trash cans, ...) from text prompts.

Drop-in for models.ObjectTracker (same ``track(bgr) -> list[TrackedBox]`` with
ByteTrack IDs), so VisionPerception, the TTC estimator and the person/object
classifier use it unchanged. Selected with ``--detector openvocab``.

Model: YOLOE from ultralytics (``yoloe-26s-seg.pt`` by default, see config.py).
At startup the prompts are turned into text embeddings once by a MobileCLIP2
text encoder (~250 MB, downloaded on first use, then freed); per frame it costs
a little more than a normal YOLO. Only the boxes are used; the masks the "-seg"
weights also produce are ignored. YOLO-World weights ("yolov8s-worldv2.pt")
work too; they encode prompts with OpenAI CLIP instead (see hub/README.md for
the licence difference).

Labels: a box's label is the prompt that matched it, except that person-like
prompts ("person", "pedestrian", "people") all become ``"person"``, which is
what classify.local_kind() treats as a person. "person" is always in the prompt
list, so people are still detected.

Prompts are not a guarantee: a prompt the model doesn't understand, glass, and
anything below the image (curbs, drop-offs) can produce no box at all.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

import numpy as np

from haptic_hub.perception.base import resolve_device
from haptic_hub.perception.models import TrackedBox

log = logging.getLogger(__name__)

PERSON = "person"
PERSON_PROMPTS = frozenset({"person", "pedestrian", "people"})


def normalize_prompts(prompts: Iterable[str]) -> tuple[str, ...]:
    """Trim, lowercase, collapse spaces, drop empties and duplicates (order kept),
    and put "person" first if no person-like prompt is present."""
    out: list[str] = []
    for prompt in prompts:
        p = " ".join(str(prompt).split()).lower()
        if p and p not in out:
            out.append(p)
    if not any(p in PERSON_PROMPTS for p in out):
        out.insert(0, PERSON)
    return tuple(out)


def parse_prompts(text: str) -> tuple[str, ...]:
    """Comma-separated prompts from the command line -> normalized tuple."""
    return normalize_prompts(text.split(","))


def label_for(prompt: str) -> str:
    """Prompt -> TrackedBox label. Person-like prompts become "person"."""
    return PERSON if prompt in PERSON_PROMPTS else prompt


def model_class(model_path: str) -> str:
    """ultralytics class for the weights: YOLO-World ("yolov8s-worldv2.pt") or YOLOE (anything else)."""
    return "YOLOWorld" if "world" in model_path.rsplit("/", 1)[-1].lower() else "YOLOE"


class OpenVocabTracker:
    """YOLOE with text prompts + ByteTrack. Same interface as models.ObjectTracker.

    ``model`` lets tests pass a fake with ``set_classes`` and ``track``; normally
    it is loaded from ``model_path``.
    """

    def __init__(
        self,
        model_path: str,
        prompts: Iterable[str],
        device: str = "auto",
        confidence: float = 0.25,
        imgsz: int = 640,
        model: Any = None,
    ) -> None:
        self.prompts = normalize_prompts(prompts)
        self.labels = tuple(label_for(p) for p in self.prompts)
        self.device = resolve_device(device)
        self.confidence = confidence
        self.imgsz = imgsz
        if model is None:
            import ultralytics

            log.info("loading open-vocabulary detector %s on %s", model_path, self.device)
            model = getattr(ultralytics, model_class(model_path))(model_path)
        log.info("open-vocabulary prompts: %s", ", ".join(self.prompts))
        model.set_classes(list(self.prompts))  # class index i <-> self.prompts[i]
        self._model = model

    def track(self, bgr: np.ndarray) -> list[TrackedBox]:
        results = self._model.track(
            bgr,
            persist=True,
            tracker="bytetrack.yaml",
            conf=self.confidence,
            imgsz=self.imgsz,
            device=self.device,
            verbose=False,
        )
        boxes = results[0].boxes
        if boxes is None or len(boxes) == 0:
            return []
        xyxy = boxes.xyxy.cpu().numpy()
        cls = boxes.cls.cpu().numpy().astype(int)
        conf = boxes.conf.cpu().numpy()
        ids = boxes.id.cpu().numpy().astype(int).tolist() if boxes.id is not None else [None] * len(cls)
        return [
            TrackedBox(tid, self.labels[int(c)], float(p), *map(float, xy))
            for tid, c, p, xy in zip(ids, cls, conf, xyxy, strict=True)
        ]
