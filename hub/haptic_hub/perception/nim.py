"""Cloud person/object labels from a Nemotron vision model.

Talks to any OpenAI-compatible chat endpoint that accepts images: vLLM or a NIM
container on a Brev GPU instance, or NVIDIA's hosted API (build.nvidia.com).
Only the standard library is used for HTTP, with one kept-alive connection.

The local detector stays the fast path. This module labels its tracked boxes in
the background: draw numbered boxes on a downscaled frame, ask the model which
are people, cache the answer per track ID. One request is in flight at a time,
at most ``max_hz`` per second, so a slow endpoint lowers the label rate instead
of delaying collision alerts. If no reply arrives for ``stale_s`` while there are
boxes to label, ``stale_reason`` says so and the hub plays the fault pattern.
"""

from __future__ import annotations

import base64
import http.client
import json
import logging
import os
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import urlsplit

import cv2
import numpy as np

from haptic_hub.perception.classify import KINDS

log = logging.getLogger(__name__)

HOSTED_URL = "https://integrate.api.nvidia.com/v1"
HOSTED_HOST = "integrate.api.nvidia.com"
# Verified in the hosted catalog (GET /v1/models) on 2026-11-07; accepts image_url input.
HOSTED_DEFAULT_MODEL = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"


class CloudError(RuntimeError):
    """A request failed: no network, timeout, HTTP error, rate limit or unusable reply."""


class NimSetupError(RuntimeError):
    """The endpoint can't be used at all (wrong URL, missing key, no models)."""


class ChatClient:
    """Minimal OpenAI-compatible client over http.client. Not thread-safe; one
    worker thread owns it."""

    def __init__(self, base_url: str, api_key: str | None, timeout_s: float) -> None:
        parts = urlsplit(base_url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise NimSetupError(f"bad endpoint URL {base_url!r}; expected e.g. http://localhost:8000/v1")
        self.base_url = base_url.rstrip("/")
        self._https = parts.scheme == "https"
        self._host = parts.hostname
        self._port = parts.port
        self._prefix = parts.path.rstrip("/")
        self._api_key = api_key
        self.timeout_s = timeout_s
        self._conn: http.client.HTTPConnection | None = None

    def get(self, path: str) -> dict[str, Any]:
        return self._request("GET", path)

    def chat(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/chat/completions", body)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if self._conn is None:
            cls = http.client.HTTPSConnection if self._https else http.client.HTTPConnection
            self._conn = cls(self._host, self._port, timeout=self.timeout_s)
        try:
            self._conn.request(method, self._prefix + path, body=data, headers=headers)
            resp = self._conn.getresponse()
            raw = resp.read()
        except (OSError, http.client.HTTPException) as exc:  # includes TimeoutError
            self.close()  # the connection may be half-used; start fresh next time
            raise CloudError(f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__) from exc
        if resp.status == 429:
            raise CloudError("rate limited (HTTP 429)")
        if resp.status >= 400:
            raise CloudError(f"HTTP {resp.status}: {raw[:200].decode(errors='replace')}")
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise CloudError(f"non-JSON reply: {raw[:120]!r}") from exc


def render_marked_jpeg(
    image: np.ndarray, boxes: Sequence[Any], infer_size: tuple[int, int], width: int, quality: int
) -> bytes:
    """Downscale the BGR frame and draw boxes numbered 1..N (set-of-marks prompt).
    Boxes are in inference-image pixels (infer_size = (w, h))."""
    h0, w0 = image.shape[:2]
    width = min(width, w0)
    img = cv2.resize(image, (width, max(1, round(h0 * width / w0))), interpolation=cv2.INTER_AREA)
    s = width / infer_size[0]
    font = cv2.FONT_HERSHEY_SIMPLEX
    for i, box in enumerate(boxes, start=1):
        p1 = (int(box.x1 * s), int(box.y1 * s))
        p2 = (int(box.x2 * s), int(box.y2 * s))
        cv2.rectangle(img, p1, p2, (0, 255, 255), 2)
        text = str(i)
        (tw, th), _ = cv2.getTextSize(text, font, 0.6, 2)
        cv2.rectangle(img, p1, (p1[0] + tw + 6, p1[1] + th + 6), (0, 0, 0), -1)
        cv2.putText(img, text, (p1[0] + 3, p1[1] + th + 3), font, 0.6, (0, 255, 255), 2)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise CloudError("JPEG encoding failed")
    return buf.tobytes()


def build_request(model: str, jpeg: bytes, n_boxes: int) -> dict[str, Any]:
    example = json.dumps({str(i): ("person" if i == 1 else "object") for i in range(1, min(n_boxes, 2) + 1)})
    prompt = (
        f"Numbered yellow boxes are drawn on this camera frame. For each box number from 1 to {n_boxes}, "
        'answer "person" if the box mainly contains a human (any pose, partly visible, or only part of the body), '
        'otherwise "object" (furniture, vehicles, animals, signs, poles and anything else). '
        f"Reply with only a JSON object mapping each box number to its answer, for example {example}."
    )
    return {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()},
                    },
                ],
            }
        ],
        "max_tokens": 16 + 10 * n_boxes,
        "temperature": 0.0,
        "stream": False,
        # Nemotron reasoning models think by default; that costs hundreds of tokens.
        "chat_template_kwargs": {"enable_thinking": False},
    }


def reply_text(reply: dict[str, Any]) -> str:
    try:
        message = reply["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise CloudError(f"unexpected reply shape: {str(reply)[:120]}") from exc
    return message.get("content") or message.get("reasoning_content") or ""


def parse_labels(text: str, n_boxes: int) -> dict[int, str]:
    """Model text -> {box number: kind}. Ignores anything it can't read."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match is None:
        return {}
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return {}
    if isinstance(data, dict) and len(data) == 1 and isinstance(next(iter(data.values())), dict):
        data = next(iter(data.values()))  # {"labels": {...}}
    if not isinstance(data, dict):
        return {}
    out: dict[int, str] = {}
    for key, value in data.items():
        try:
            number = int(str(key).strip().lstrip("#"))
        except ValueError:
            continue
        kind = str(value).strip().lower()
        if 1 <= number <= n_boxes and kind in KINDS:
            out[number] = kind
    return out


class NimLabeler:
    """Background cloud labeling for ClassifiedPerception (implements its CloudBackend)."""

    name = "nim"

    def __init__(
        self,
        client: Any,  # ChatClient or a test double with .chat(body) -> dict
        model: str,
        cfg: Any,  # config.ClassifierConfig
        background: bool = True,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.model = model
        self.cfg = cfg
        self._clock = clock
        self._background = background
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._job: tuple[bytes, list[int]] | None = None
        self._busy = False
        self._closed = False
        self._last_sent = float("-inf")
        self._labels: dict[int, tuple[str, float]] = {}  # track_id -> (kind, last seen)
        self._last_ok: float | None = None
        self._last_error: str | None = None
        self._demand_since: float | None = None
        self.rtts_ms: deque[float] = deque(maxlen=200)
        self.errors = 0
        if background:
            threading.Thread(target=self._loop, name="nim-labeler", daemon=True).start()

    @classmethod
    def from_config(cls, cfg: Any) -> NimLabeler:
        # explicit config (CLI flag) > environment > hosted default
        base_url = cfg.base_url or os.environ.get("NIM_BASE_URL") or HOSTED_URL
        api_key = os.environ.get("NIM_API_KEY") or os.environ.get("NVIDIA_API_KEY")
        hosted = urlsplit(base_url).hostname == HOSTED_HOST
        if hosted and not api_key:
            raise NimSetupError(
                "NVIDIA's hosted API needs a key: export NVIDIA_API_KEY=nvapi-... "
                "(or point NIM_BASE_URL at your own server, e.g. http://localhost:8000/v1)"
            )
        client = ChatClient(base_url, api_key, cfg.timeout_s)
        try:
            listed = [m["id"] for m in client.get("/models").get("data", [])]
        except CloudError as exc:
            raise NimSetupError(f"can't reach {client.base_url}/models: {exc}") from exc
        model = cfg.model or os.environ.get("NIM_MODEL")
        if not model:
            model = HOSTED_DEFAULT_MODEL if hosted else (listed[0] if listed else "")
        if not model:
            raise NimSetupError(f"{client.base_url} lists no models; set NIM_MODEL")
        if listed and model not in listed:
            raise NimSetupError(f"model {model!r} not served by {client.base_url}; it lists {listed[:10]}")
        log.info("cloud classifier: %s at %s (max %.1f req/s)", model, client.base_url, cfg.max_hz)
        return cls(client, model, cfg)

    # -- called from the perception thread ---------------------------------

    def submit(self, image: Any, boxes: Sequence[Any], infer_size: tuple[int, int], now: float) -> None:
        tracked = [b for b in boxes if b.track_id is not None]
        with self._lock:
            if not tracked:
                self._demand_since = None  # nothing needs a label: no reply is owed
                return
            if self._demand_since is None:
                self._demand_since = now
            for b in tracked:  # keep labels of tracks that are still visible
                if b.track_id in self._labels:
                    self._labels[b.track_id] = (self._labels[b.track_id][0], now)
            if self._busy or now - self._last_sent < 1.0 / self.cfg.max_hz:
                return
            self._busy = True
            self._last_sent = now
        chosen = sorted(tracked, key=lambda b: (b.x2 - b.x1) * (b.y2 - b.y1), reverse=True)[: self.cfg.max_boxes]
        try:
            jpeg = render_marked_jpeg(image, chosen, infer_size, self.cfg.image_width, self.cfg.jpeg_quality)
        except Exception:
            with self._lock:
                self._busy = False
            raise
        job = (jpeg, [b.track_id for b in chosen])
        if not self._background:
            self._run(job)
            return
        with self._lock:
            self._job = job
        self._wake.set()

    def labels(self, now: float) -> dict[int, str]:
        with self._lock:
            for tid in [t for t, (_, seen) in self._labels.items() if now - seen > self.cfg.label_ttl_s]:
                del self._labels[tid]
            return {tid: kind for tid, (kind, _) in self._labels.items()}

    def stale_reason(self, now: float) -> str | None:
        with self._lock:
            if self._demand_since is None:
                return None
            since = self._demand_since if self._last_ok is None else max(self._last_ok, self._demand_since)
            waited = now - since
            if waited <= self.cfg.stale_s:
                return None
            return f"cloud classifier: no labels for {waited:.1f} s ({self._last_error or 'no reply yet'})"

    def status(self, now: float) -> dict[str, Any]:
        with self._lock:
            return {
                "backend": "nim",
                "model": self.model,
                "rtt_ms": self.rtts_ms[-1] if self.rtts_ms else None,
                "last_ok_age_s": None if self._last_ok is None else now - self._last_ok,
                "error": self._last_error,
            }

    def close(self) -> None:
        self._closed = True
        self._wake.set()

    # -- worker thread -----------------------------------------------------

    def _loop(self) -> None:
        while not self._closed:
            self._wake.wait()
            self._wake.clear()
            with self._lock:
                job, self._job = self._job, None
            if job is not None:
                self._run(job)

    def _run(self, job: tuple[bytes, list[int]]) -> None:
        jpeg, track_ids = job
        started = self._clock()
        try:
            text = reply_text(self.client.chat(build_request(self.model, jpeg, len(track_ids))))
            found = parse_labels(text, len(track_ids))
            if not found:
                raise CloudError(f"no labels in reply: {text[:80]!r}")
        except Exception as exc:  # noqa: BLE001 - every failure must surface as staleness
            why = str(exc) or type(exc).__name__
            with self._lock:
                changed = why != self._last_error
                self._last_error = why
                self._busy = False
                self.errors += 1
            if changed:
                log.warning("cloud classifier request failed: %s", why)
            return
        done = self._clock()
        with self._lock:
            for number, kind in found.items():
                self._labels[track_ids[number - 1]] = (kind, done)
            if self._last_error is not None:
                log.info("cloud classifier recovered")
            self._last_ok = done
            self._last_error = None
            self._busy = False
            self.rtts_ms.append((done - started) * 1000)
