"""Nemotron is the default classifier ("auto"): used when its endpoint answers at
startup, otherwise the hub falls back to local labels instead of refusing to
start. Loopback server only, no internet."""

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from haptic_hub import cli
from haptic_hub.config import HubConfig


class FakeInner:
    name = "fake-vision"

    def process(self, frame):  # never called here
        raise AssertionError


class _Models(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        data = json.dumps({"data": [{"id": "nemotron-3-nano-omni"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Models)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()
    srv.server_close()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for key in ("NIM_BASE_URL", "NIM_API_KEY", "NVIDIA_API_KEY", "NIM_MODEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))  # no real ~/.config/haptic-hub/nim.env


def parse(*argv):
    p = argparse.ArgumentParser()
    cli._add_classifier_args(p)
    return p.parse_args(list(argv))


def test_default_is_auto():
    assert parse().classifier == "auto"


def test_auto_uses_nemotron_when_reachable(server, monkeypatch):
    monkeypatch.setenv("NIM_BASE_URL", server)
    cfg = HubConfig()
    cli._apply_classifier_args(parse(), cfg)
    wrapped = cli._build_classifier("auto", cfg, FakeInner())
    try:
        assert wrapped.cloud is not None and wrapped.cloud.model == "nemotron-3-nano-omni"
        assert cfg.classifier.backend == "nim"
    finally:
        wrapped.cloud.close()


def test_auto_falls_back_to_local_without_endpoint(caplog):
    cfg = HubConfig()  # hosted API, no key -> setup error without any network call
    cli._apply_classifier_args(parse(), cfg)
    wrapped = cli._build_classifier("auto", cfg, FakeInner())
    assert wrapped.cloud is None
    assert cfg.classifier.backend == "local"  # the phone/dashboard see the truth
    assert "Nemotron classifier unavailable" in caplog.text


def test_explicit_nim_still_refuses_to_start_without_endpoint():
    cfg = HubConfig()
    cli._apply_classifier_args(parse("--classifier", "nim"), cfg)
    with pytest.raises(SystemExit, match="cloud classifier"):
        cli._build_classifier("nim", cfg, FakeInner())


def test_local_skips_the_probe():
    cfg = HubConfig()
    cli._apply_classifier_args(parse("--classifier", "local"), cfg)
    assert cli._build_classifier("local", cfg, FakeInner()).cloud is None


def test_default_env_file_is_loaded(server, monkeypatch, tmp_path):
    env = tmp_path / ".config" / "haptic-hub" / "nim.env"
    env.parent.mkdir(parents=True)
    env.write_text(f"NIM_BASE_URL={server}\n")
    cfg = HubConfig()
    cli._apply_classifier_args(parse(), cfg)
    wrapped = cli._build_classifier("auto", cfg, FakeInner())
    try:
        assert wrapped.cloud is not None
    finally:
        wrapped.cloud.close()
