"""Person vs. object classification: local mapping, cloud labeler with a fake
client, the failure -> fault path, and the HTTP client against a loopback server.
No models, no hardware, no internet."""

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

from haptic_hub.app import Hub
from haptic_hub.belt.sim import SimBelt
from haptic_hub.config import ClassifierConfig, HubConfig
from haptic_hub.perception.classify import ClassifiedPerception, local_kind
from haptic_hub.perception.models import TrackedBox
from haptic_hub.perception.nim import (
    ChatClient,
    CloudError,
    NimLabeler,
    NimSetupError,
    build_request,
    parse_labels,
    render_marked_jpeg,
)
from haptic_hub.protocol import BeltMode
from haptic_hub.types import Frame, PerceptionResult, SectorRisk

PERSON_BOX = TrackedBox(1, "person", 0.9, 10, 10, 60, 120)
CHAIR_BOX = TrackedBox(2, "chair", 0.8, 200, 50, 300, 150)
UNTRACKED = TrackedBox(None, "person", 0.5, 100, 10, 140, 60)


class FakeInner:
    """Stands in for VisionPerception: returns fixed boxes in debug, like the real one."""

    name = "fake-vision"

    def __init__(self, boxes=(PERSON_BOX, CHAIR_BOX)):
        self.boxes = list(boxes)

    def process(self, frame):
        sectors = tuple(SectorRisk(0.5 if i == 2 else 0.0) for i in range(5))
        debug = {"infer_size": (384, 216), "boxes": list(self.boxes)}
        return PerceptionResult(frame.seq, frame.received_at, sectors, 5.0, debug)


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class FakeClient:
    """Records requests; answers with `reply` text or raises `error`."""

    def __init__(self, reply='{"1": "person", "2": "object"}', error=None):
        self.reply, self.error, self.bodies = reply, error, []

    def chat(self, body):
        self.bodies.append(body)
        if self.error is not None:
            raise self.error
        return {"choices": [{"message": {"content": self.reply}}]}


def frame(seq=1, t=0.0):
    return Frame(seq, np.zeros((216, 384, 3), np.uint8), t, source="test")


def labeler(client, clock, **cfg):
    return NimLabeler(client, "test-model", ClassifierConfig(backend="nim", **cfg), background=False, clock=clock)


# -- local ---------------------------------------------------------------


def test_local_kind_maps_coco_person_only():
    assert local_kind("person") == "person"
    for label in ("chair", "car", "dog", "bicycle"):
        assert local_kind(label) == "object"


def test_local_classifier_labels_every_box_and_keeps_risk():
    result = ClassifiedPerception(FakeInner([PERSON_BOX, CHAIR_BOX, UNTRACKED])).process(frame())
    kinds = [(c.box.label, c.kind, c.origin) for c in result.debug["classified"]]
    assert kinds == [("person", "person", "local"), ("chair", "object", "local"), ("person", "person", "local")]
    assert [s.risk for s in result.sectors] == [0.0, 0.0, 0.5, 0.0, 0.0]  # risk untouched
    assert result.debug["classifier"]["backend"] == "local"


def test_no_boxes_is_fine():
    result = ClassifiedPerception(FakeInner([])).process(frame())
    assert result.debug["classified"] == []


# -- reply parsing -------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"1": "person", "2": "object"}', {1: "person", 2: "object"}),
        ('```json\n{"1": "Person", "2": "OBJECT"}\n```', {1: "person", 2: "object"}),
        ('<think>hmm</think>{"labels": {"1": "object"}}', {1: "object"}),
        ('{"1": "person", "7": "person", "2": "dog"}', {1: "person"}),  # out of range / invalid kind dropped
        ("I can't tell.", {}),
        ("{not json}", {}),
    ],
)
def test_parse_labels(text, expected):
    assert parse_labels(text, 2) == expected


def test_request_disables_thinking_and_embeds_jpeg():
    jpeg = render_marked_jpeg(np.zeros((432, 768, 3), np.uint8), [PERSON_BOX, CHAIR_BOX], (384, 216), 512, 70)
    assert jpeg[:2] == b"\xff\xd8"
    body = build_request("m", jpeg, 2)
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["temperature"] == 0.0
    image = body["messages"][0]["content"][1]
    assert image["type"] == "image_url" and image["image_url"]["url"].startswith("data:image/jpeg;base64,")


# -- cloud labeler -------------------------------------------------------


def test_cloud_labels_override_local_per_track():
    clock = FakeClock()
    client = FakeClient('{"1": "person", "2": "object"}')
    perception = ClassifiedPerception(FakeInner([CHAIR_BOX, PERSON_BOX]), labeler(client, clock), clock)
    result = perception.process(frame())
    by_track = {c.box.track_id: (c.kind, c.origin) for c in result.debug["classified"]}
    # boxes are sent largest first: CHAIR (100x100) is #1, PERSON (50x110) is #2
    assert by_track == {2: ("person", "cloud"), 1: ("object", "cloud")}
    assert len(client.bodies) == 1
    assert result.debug["classifier"]["backend"] == "nim"


def test_untracked_boxes_keep_local_labels_and_are_not_sent():
    clock = FakeClock()
    client = FakeClient('{"1": "person"}')
    result = ClassifiedPerception(FakeInner([UNTRACKED]), labeler(client, clock), clock).process(frame())
    assert client.bodies == []
    assert [(c.kind, c.origin) for c in result.debug["classified"]] == [("person", "local")]


def test_requests_are_rate_limited():
    clock = FakeClock()
    client = FakeClient()
    perception = ClassifiedPerception(FakeInner(), labeler(client, clock, max_hz=2.0), clock)
    for _ in range(10):  # 10 frames within 0.3 s
        perception.process(frame())
        clock.t += 0.03
    assert len(client.bodies) == 1
    clock.t += 0.5
    perception.process(frame())
    assert len(client.bodies) == 2


def origins(result):
    return {c.origin for c in result.debug["classified"]}


def test_cloud_outage_falls_back_to_local_labels_then_recovers():
    clock = FakeClock()
    client = FakeClient()
    perception = ClassifiedPerception(FakeInner(), labeler(client, clock, stale_s=1.0), clock)
    assert origins(perception.process(frame())) == {"cloud"}

    client.error = CloudError("TimeoutError: timed out")
    clock.t += 0.6
    result = perception.process(frame())  # within the grace period: cached cloud labels
    assert origins(result) == {"cloud"} and not result.debug["classifier"].get("stale")
    clock.t += 0.6
    result = perception.process(frame())  # no raise: the belt's risk must survive
    assert origins(result) == {"local"}
    assert result.debug["classifier"]["stale"] is True
    assert "timed out" in result.debug["classifier"]["error"]
    assert [s.risk for s in result.sectors] == [0.0, 0.0, 0.5, 0.0, 0.0]

    client.error = None  # network comes back
    clock.t += 0.6
    result = perception.process(frame())
    assert origins(result) == {"cloud"} and not result.debug["classifier"].get("stale")


@pytest.mark.parametrize(
    "error", [CloudError("rate limited (HTTP 429)"), CloudError("ConnectionRefusedError"), ValueError("boom")]
)
def test_any_cloud_error_counts_as_stale(error):
    clock = FakeClock()
    perception = ClassifiedPerception(FakeInner(), labeler(FakeClient(error=error), clock, stale_s=0.5), clock)
    perception.process(frame())
    clock.t += 0.6
    result = perception.process(frame())
    assert result.debug["classifier"]["stale"] is True and origins(result) == {"local"}


def test_unparseable_reply_counts_as_failure():
    clock = FakeClock()
    perception = ClassifiedPerception(FakeInner(), labeler(FakeClient("no idea"), clock, stale_s=0.5), clock)
    perception.process(frame())
    clock.t += 0.6
    result = perception.process(frame())
    assert "no labels" in result.debug["classifier"]["error"] and origins(result) == {"local"}


def test_no_boxes_means_no_reply_is_owed():
    clock = FakeClock()
    client = FakeClient(error=CloudError("offline"))
    perception = ClassifiedPerception(FakeInner([]), labeler(client, clock, stale_s=0.5), clock)
    for _ in range(5):
        perception.process(frame())
        clock.t += 1.0
    assert client.bodies == []  # nothing to classify, nothing sent, no fault


def test_hung_request_in_background_still_goes_stale():
    """A request that never returns must not hold off the fault."""
    release = threading.Event()

    class HangingClient:
        def chat(self, body):
            release.wait(5)
            raise CloudError("released")

    cfg = ClassifierConfig(backend="nim", stale_s=0.2)
    lab = NimLabeler(HangingClient(), "m", cfg, background=True)
    perception = ClassifiedPerception(FakeInner(), lab)
    try:
        perception.process(frame())
        time.sleep(0.3)
        result = perception.process(frame())
        assert "no reply yet" in result.debug["classifier"]["error"] and origins(result) == {"local"}
    finally:
        release.set()
        lab.close()


# -- end to end: a cloud outage must not take the collision alerts away -------


class FakeSource:
    name = "fake"

    def describe(self):
        return {"source": self.name}

    async def run(self, slot):
        seq = 0
        while True:
            seq += 1
            slot.put(Frame(seq, np.zeros((216, 384, 3), np.uint8), time.monotonic(), source=self.name))
            await asyncio.sleep(1 / 30)


def test_cloud_outage_keeps_collision_alerts_with_local_labels():
    cfg = HubConfig()
    client = FakeClient()
    lab = NimLabeler(client, "m", ClassifierConfig(backend="nim", stale_s=0.3, max_hz=20), background=True)
    belt = SimBelt(5)
    hub = Hub(cfg, FakeSource(), ClassifiedPerception(FakeInner(), lab), belt)
    modes, cloud_before = [], set()

    async def scenario():
        task = asyncio.create_task(hub.run())
        for i in range(40):
            if i == 15:
                cloud_before.update(origins(hub.result))
                client.error = CloudError("ConnectionResetError")  # the network drops
            await asyncio.sleep(0.05)
            modes.append(belt.last.mode)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    try:
        asyncio.run(scenario())
    finally:
        lab.close()
    assert cloud_before == {"cloud"}  # healthy cloud: cloud labels
    # Cloud gone for 1.25 s (> stale_s): the obstacle in sector 2 is still felt.
    assert modes[-10:] == [BeltMode.LEVELS] * 10, modes
    assert hub.reason is None
    assert origins(hub.result) == {"local"} and hub.result.debug["classifier"]["stale"] is True


# -- HTTP client against a loopback server ----------------------------------


class _Handler(BaseHTTPRequestHandler):
    behavior = "ok"
    seen: list = []

    def log_message(self, *args):
        pass

    def _send(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.seen.append(("GET", self.path, self.headers.get("Authorization"), None))
        self._send(200, {"data": [{"id": "nemotron-3-nano-omni"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.seen.append(("POST", self.path, self.headers.get("Authorization"), body))
        if self.behavior == "slow":
            time.sleep(0.6)
        if self.behavior == "429":
            return self._send(429, {"error": "slow down"})
        self._send(200, {"choices": [{"message": {"content": '{"1": "person"}'}}]})


@pytest.fixture
def server():
    _Handler.behavior, _Handler.seen = "ok", []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()
    srv.server_close()


def test_from_config_discovers_model_and_sends_key(server, monkeypatch):
    _, url = server
    monkeypatch.setenv("NIM_BASE_URL", url)
    monkeypatch.setenv("NIM_API_KEY", "test-key")
    monkeypatch.delenv("NIM_MODEL", raising=False)
    lab = NimLabeler.from_config(ClassifierConfig(backend="nim"))
    lab.close()
    assert lab.model == "nemotron-3-nano-omni"
    reply = lab.client.chat(build_request(lab.model, b"\xff\xd8\xff\xd9", 1))
    assert reply["choices"][0]["message"]["content"] == '{"1": "person"}'
    method, path, auth, body = _Handler.seen[-1]
    assert (method, path, auth) == ("POST", "/v1/chat/completions", "Bearer test-key")
    assert body["model"] == "nemotron-3-nano-omni"


def test_from_config_rejects_unknown_model(server, monkeypatch):
    _, url = server
    monkeypatch.setenv("NIM_BASE_URL", url)
    monkeypatch.setenv("NIM_MODEL", "not-served")
    with pytest.raises(NimSetupError, match="not served"):
        NimLabeler.from_config(ClassifierConfig(backend="nim"))


def test_hosted_api_without_key_fails_at_startup(monkeypatch):
    for var in ("NIM_BASE_URL", "NIM_API_KEY", "NVIDIA_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(NimSetupError, match="NVIDIA_API_KEY"):
        NimLabeler.from_config(ClassifierConfig(backend="nim"))


def test_unreachable_endpoint_fails_at_startup(monkeypatch):
    with socket_free_port() as port:
        pass  # closed again: nothing listens there
    monkeypatch.setenv("NIM_BASE_URL", f"http://127.0.0.1:{port}/v1")
    with pytest.raises(NimSetupError, match="can't reach"):
        NimLabeler.from_config(ClassifierConfig(backend="nim", timeout_s=0.5))


def test_client_timeout_and_rate_limit_raise_cloud_error(server):
    _, url = server
    client = ChatClient(url, None, timeout_s=0.2)
    _Handler.behavior = "slow"
    with pytest.raises(CloudError, match="[Tt]ime"):
        client.chat({"model": "m"})
    _Handler.behavior = "429"
    with pytest.raises(CloudError, match="429"):
        client.chat({"model": "m"})
    _Handler.behavior = "ok"
    assert client.chat({"model": "m"})["choices"]  # reconnects after the failures


def socket_free_port():
    import contextlib
    import socket

    @contextlib.contextmanager
    def ctx():
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            yield s.getsockname()[1]

    return ctx()


def test_flags_beat_environment(server, monkeypatch):
    _, url = server
    monkeypatch.setenv("NIM_BASE_URL", "http://127.0.0.1:9/v1")  # would fail if used
    monkeypatch.setenv("NIM_MODEL", "not-served")
    lab = NimLabeler.from_config(ClassifierConfig(backend="nim", base_url=url, model="nemotron-3-nano-omni"))
    lab.close()
    assert lab.client.base_url == url and lab.model == "nemotron-3-nano-omni"


def test_low_confidence_boxes_are_uncertain_not_risk():
    """A box below detector_conf marks its sectors uncertain and adds no risk; a
    confident one drives risk as before. Neither is silently dropped."""
    from haptic_hub.config import PerceptionConfig
    from haptic_hub.perception.models import TrackedBox
    from haptic_hub.perception.vision import VisionPerception

    class Tracker:
        def __init__(self):
            self.i = 0

        def track(self, bgr):
            self.i += 1
            h = 100 + 8 * self.i  # both growing fast: TTC well under 1 s
            return [TrackedBox(1, "person", 0.2, 0, 10, 60, 10 + h), TrackedBox(2, "person", 0.9, 300, 10, 380, 10 + h)]

    vp = VisionPerception(PerceptionConfig(), None, Tracker())
    result = None
    for i in range(8):
        result = vp.process(Frame(i, np.zeros((683, 384, 3), np.uint8), i / 15, source="test"))
    assert [s.uncertain for s in result.sectors] == [True, False, False, False, False]
    assert result.sectors[0].risk == 0.0 and result.sectors[4].risk > 0.5
    assert [b.track_id for b in result.debug["boxes"]] == [2]
    assert [b.track_id for b in result.debug["uncertain_boxes"]] == [1]
