"""Person vs. object labels for the boxes the detector already tracks.

Wraps any Perception without changing it: runs the inner perception, reads the
tracked boxes it leaves in ``result.debug["boxes"]`` and adds

  debug["classified"]  list[ClassifiedBox], one per box, same order
  debug["classifier"]  dict describing the backend's state (for the debug window)

Backends:
  local  the detector's own COCO class ("person" -> person, anything else -> object)
  nim    a Nemotron vision model behind an OpenAI-compatible endpoint (perception/nim.py),
         run in the background at a capped rate; boxes it hasn't labeled yet keep
         their local label and are marked as such

The labels do not change the per-sector risk or the belt output. If the cloud
backend is selected and stops answering while there are boxes to label, every
box falls back to its local label and debug["classifier"] says why ("stale",
"error"). Collision alerts carry on: a labeling outage must not take away the
belt's depth and TTC risk. If labels ever drive the belt, an unavailable
classifier has to become a fault (raise ClassifierUnavailable) instead.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

from haptic_hub.perception.base import Perception
from haptic_hub.types import Frame, PerceptionResult

PERSON = "person"
OBJECT = "object"
KINDS = (PERSON, OBJECT)


class ClassifierUnavailable(RuntimeError):
    """The selected classifier can't vouch for its labels (cloud down, timed out, rate limited).
    Not raised while labels don't drive the belt (see the module docstring)."""


@dataclass(frozen=True)
class ClassifiedBox:
    box: Any  # perception.models.TrackedBox, in inference-image pixels
    kind: str  # PERSON | OBJECT
    origin: str  # "local" (detector class) | "cloud" (remote model)


def local_kind(label: str) -> str:
    """COCO detector class -> person/object."""
    return PERSON if label == PERSON else OBJECT


class CloudBackend(Protocol):
    name: str

    def submit(self, image: Any, boxes: Sequence[Any], infer_size: tuple[int, int], now: float) -> None:
        """Hand over the newest frame and its boxes. Must not block on the network."""

    def labels(self, now: float) -> dict[int, str]:
        """track_id -> kind for every track the cloud has labeled recently."""

    def stale_reason(self, now: float) -> str | None:
        """Why the labels can't be trusted right now, or None if they can."""

    def status(self, now: float) -> dict[str, Any]: ...


class ClassifiedPerception:
    def __init__(
        self,
        inner: Perception,
        cloud: CloudBackend | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.inner = inner
        self.cloud = cloud
        self._clock = clock
        self.name = f"{inner.name}+{cloud.name if cloud else 'local'}-classifier"

    def process(self, frame: Frame) -> PerceptionResult:
        result = self.inner.process(frame)
        boxes = list(result.debug.get("boxes", ()))
        now = self._clock()

        cloud_labels: dict[int, str] = {}
        status: dict[str, Any] = {"backend": "local"}
        if self.cloud is not None:
            infer_size = result.debug.get("infer_size", frame.image.shape[1::-1])
            self.cloud.submit(frame.image, boxes, infer_size, now)
            status = self.cloud.status(now)
            if (why := self.cloud.stale_reason(now)) is None:
                cloud_labels = self.cloud.labels(now)
            else:  # can't vouch for cloud labels: every box keeps its local one
                status = {**status, "stale": True, "error": why}

        classified = []
        for box in boxes:
            kind = cloud_labels.get(box.track_id) if box.track_id is not None else None
            if kind is not None:
                classified.append(ClassifiedBox(box, kind, "cloud"))
            else:
                classified.append(ClassifiedBox(box, local_kind(box.label), "local"))
        return replace(result, debug={**result.debug, "classified": classified, "classifier": status})


def build_classified(inner: Perception, cfg: Any) -> ClassifiedPerception:
    """cfg is a config.ClassifierConfig. The nim backend probes its endpoint here,
    so a wrong URL or missing key fails at startup instead of mid-walk."""
    if cfg.backend == "local":
        return ClassifiedPerception(inner)
    if cfg.backend == "nim":
        from haptic_hub.perception.nim import NimLabeler

        return ClassifiedPerception(inner, NimLabeler.from_config(cfg))
    raise ValueError(f"unknown classifier {cfg.backend!r}; choose local or nim")
