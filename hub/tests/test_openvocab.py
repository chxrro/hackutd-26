"""Open-vocabulary detector: prompt handling, label -> person/object mapping, and
the wiring into the unchanged perception pipeline, with a fake YOLOE model.
No weights, no torch, no network."""

import numpy as np
import pytest

from haptic_hub import perception
from haptic_hub.config import PerceptionConfig
from haptic_hub.perception import build_perception, build_tracker
from haptic_hub.perception import openvocab as ov
from haptic_hub.perception.classify import OBJECT, PERSON, ClassifiedPerception, local_kind
from haptic_hub.perception.openvocab import OpenVocabTracker, label_for, model_class, normalize_prompts, parse_prompts
from haptic_hub.perception.vision import VisionPerception
from haptic_hub.types import Frame


class FakeArray:
    """Mimics a torch tensor's .cpu().numpy()."""

    def __init__(self, values):
        self.values = np.asarray(values)

    def cpu(self):
        return self

    def numpy(self):
        return self.values


class FakeBoxes:
    def __init__(self, xyxy, cls, conf, ids):
        self.xyxy, self.cls, self.conf = FakeArray(xyxy), FakeArray(cls), FakeArray(conf)
        self.id = None if ids is None else FakeArray(ids)

    def __len__(self):
        return len(self.cls.values)


class FakeResult:
    def __init__(self, boxes):
        self.boxes = boxes


class FakeYOLOE:
    """Records set_classes/track calls and returns fixed detections (class indices
    refer to the prompt list it was given)."""

    def __init__(self, detections=(), ids=True):
        self.detections = list(detections)  # (class index, conf, x1, y1, x2, y2)
        self.ids = ids
        self.classes = None
        self.track_kwargs = []

    def set_classes(self, classes):
        self.classes = list(classes)

    def track(self, image, **kwargs):
        self.track_kwargs.append(kwargs)
        if not self.detections:
            return [FakeResult(FakeBoxes(np.zeros((0, 4)), [], [], None))]
        cls = [d[0] for d in self.detections]
        conf = [d[1] for d in self.detections]
        xyxy = [d[2:] for d in self.detections]
        ids = list(range(1, len(cls) + 1)) if self.ids else None
        return [FakeResult(FakeBoxes(xyxy, cls, conf, ids))]


# -- prompts ---------------------------------------------------------------


def test_prompts_are_cleaned_deduped_and_keep_order():
    assert normalize_prompts(["person", " Glass  Door ", "pole", "", "POLE", "door"]) == (
        "person",
        "glass door",
        "pole",
        "door",
    )


def test_person_is_added_when_missing():
    assert normalize_prompts(["pole", "door"]) == ("person", "pole", "door")


def test_person_like_prompt_counts_as_person():
    assert normalize_prompts(["pedestrian", "pole"]) == ("pedestrian", "pole")


def test_parse_prompts_from_command_line():
    assert parse_prompts("pole, door ,,stairs") == ("person", "pole", "door", "stairs")


def test_default_config_prompts_are_normalized_and_include_person():
    prompts = PerceptionConfig().openvocab_prompts
    assert normalize_prompts(prompts) == tuple(prompts)
    assert prompts[0] == "person"
    for hazard in ("pole", "bollard", "door", "stairs", "curb", "trash can", "sign", "low wall"):
        assert hazard in prompts


# -- labels ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("prompt", "label", "kind"),
    [
        ("person", "person", PERSON),
        ("pedestrian", "person", PERSON),
        ("people", "person", PERSON),
        ("pole", "pole", OBJECT),
        ("glass door", "glass door", OBJECT),
        ("trash can", "trash can", OBJECT),
    ],
)
def test_label_maps_to_person_or_object(prompt, label, kind):
    assert label_for(prompt) == label
    assert local_kind(label_for(prompt)) == kind


# -- tracker ---------------------------------------------------------------


def test_tracker_sets_prompts_and_returns_tracked_boxes():
    model = FakeYOLOE([(0, 0.9, 10, 20, 50, 200), (2, 0.4, 100, 30, 120, 210)])
    tracker = OpenVocabTracker("unused.pt", ["pedestrian", "Pole", "door"], device="cpu", confidence=0.3, model=model)

    assert model.classes == ["pedestrian", "pole", "door"]
    boxes = tracker.track(np.zeros((216, 384, 3), np.uint8))
    assert [(b.track_id, b.label) for b in boxes] == [(1, "person"), (2, "door")]
    assert boxes[1].confidence == pytest.approx(0.4)
    assert (boxes[1].x1, boxes[1].y1, boxes[1].x2, boxes[1].y2) == (100, 30, 120, 210)
    kwargs = model.track_kwargs[0]
    assert kwargs["persist"] is True and kwargs["tracker"] == "bytetrack.yaml"
    assert kwargs["conf"] == 0.3 and kwargs["device"] == "cpu"


def test_tracker_without_ids_or_boxes():
    tracker = OpenVocabTracker("unused.pt", ["pole"], device="cpu", model=FakeYOLOE([(1, 0.5, 0, 0, 10, 10)], ids=False))
    assert [(b.track_id, b.label) for b in tracker.track(np.zeros((8, 8, 3), np.uint8))] == [(None, "pole")]
    empty = OpenVocabTracker("unused.pt", ["pole"], device="cpu", model=FakeYOLOE())
    assert empty.track(np.zeros((8, 8, 3), np.uint8)) == []


@pytest.mark.parametrize(
    ("path", "cls"),
    [
        ("yoloe-26s-seg.pt", "YOLOE"),
        ("yoloe-26n-seg.pt", "YOLOE"),
        ("weights/yolov8s-worldv2.pt", "YOLOWorld"),
        ("my-world/yoloe-11s-seg.pt", "YOLOE"),
    ],
)
def test_model_class_from_weights_name(path, cls):
    assert model_class(path) == cls


# -- wiring ----------------------------------------------------------------


def test_build_perception_selects_openvocab(monkeypatch):
    made = {}

    class SpyTracker:
        def __init__(self, model_path, prompts, device, confidence):
            made.update(model_path=model_path, prompts=prompts, device=device, confidence=confidence)

    monkeypatch.setattr(ov, "OpenVocabTracker", SpyTracker)
    cfg = PerceptionConfig(detector="openvocab", openvocab_prompts=("person", "pole"), device="cpu")
    result = build_perception("detector", cfg)

    assert isinstance(result, VisionPerception)
    assert isinstance(result._tracker, SpyTracker)
    # The detector runs at uncertain_conf so low-scoring boxes can be reported as uncertain;
    # VisionPerception only lets boxes >= openvocab_conf drive the belt.
    assert made == {"model_path": cfg.openvocab_model, "prompts": ("person", "pole"), "device": "cpu", "confidence": 0.15}


def test_default_detector_is_still_coco_yolo(monkeypatch):
    from haptic_hub.perception import models

    made = []
    monkeypatch.setattr(models, "ObjectTracker", lambda *a: made.append(a) or "yolo-tracker")
    assert PerceptionConfig().detector == "yolo"
    assert build_tracker(PerceptionConfig(device="cpu")) == "yolo-tracker"
    assert made == [("yolo11n.pt", "cpu", 0.15)]  # uncertain_conf; >= detector_conf (0.35) drives the belt


def test_unknown_detector_fails_before_loading_models():
    with pytest.raises(ValueError, match="unknown detector"):
        build_perception("vision", PerceptionConfig(detector="nope"))


def test_openvocab_boxes_flow_through_vision_and_classifier():
    """Real VisionPerception + ClassifiedPerception, fake YOLOE: a pole in the
    center and a person on the left come out labeled object/person, and the
    boxes cover the matching belt sectors."""
    prompts = ("person", "pole")
    model = FakeYOLOE([(0, 0.8, 10, 40, 60, 200), (1, 0.7, 170, 20, 210, 210)])
    tracker = OpenVocabTracker("unused.pt", prompts, device="cpu", model=model)
    cfg = PerceptionConfig(detector="openvocab", openvocab_prompts=prompts)
    pipeline = ClassifiedPerception(VisionPerception(cfg, None, tracker))

    result = pipeline.process(Frame(1, np.zeros((216, 384, 3), np.uint8), 10.0, source="test"))

    kinds = [(c.box.label, c.kind, c.origin) for c in result.debug["classified"]]
    assert kinds == [("person", PERSON, "local"), ("pole", OBJECT, "local")]
    labels = [s.label for s in result.sectors]
    assert labels[0] == "person" and labels[2] == "pole"
    assert perception.DETECTORS == ("yolo", "openvocab")
