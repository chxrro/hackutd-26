"""Replays a session saved with `--record` (`--source replay:DIR`) at its
original timing, through the same pipeline.

Replayed results are NOT live results. The source name (and so the status
message, logs and the --show banner) says `replay:<session>`. Frames get a fresh
hub receive time when replayed, so the stale-frame safety checks behave as if
the frames arrived now. `capture_ms` is left empty: the original sender clock
reading would make any capture-to-receive number meaningless.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

import cv2

from haptic_hub.record import FRAMES_FILE, SESSION_FILE
from haptic_hub.sources.base import LatestFrameSlot
from haptic_hub.types import Frame

log = logging.getLogger(__name__)


def load_index(path: Path) -> list[dict[str, Any]]:
    lines = []
    with (path / FRAMES_FILE).open(encoding="utf-8") as f:
        for n, raw in enumerate(f, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                lines.append(json.loads(raw))
            except json.JSONDecodeError:
                log.warning("%s line %d is unreadable (cut off by a crash?); skipping", FRAMES_FILE, n)
    return lines


class ReplaySource:
    def __init__(self, path: str | Path, loop: bool = False, speed: float = 1.0) -> None:
        self.path = Path(path).expanduser()
        if not (self.path / FRAMES_FILE).is_file():
            raise FileNotFoundError(f"{self.path} has no {FRAMES_FILE}; is it a --record session folder?")
        self.index = load_index(self.path)
        if not self.index:
            raise ValueError(f"{self.path / FRAMES_FILE} lists no frames")
        self.name = f"replay:{self.path.name}"
        self.loop = loop
        self.speed = speed
        self.frames = 0
        self.missing = 0
        meta_file = self.path / SESSION_FILE
        self.meta = json.loads(meta_file.read_text()) if meta_file.is_file() else {}

    def describe(self) -> dict[str, Any]:
        return {"source": self.name, "recorded_from": self.meta.get("source"), "frames": self.frames, "of": len(self.index)}

    async def run(self, slot: LatestFrameSlot) -> None:
        duration = self.index[-1]["received_at"] - self.index[0]["received_at"]
        log.warning(
            "REPLAY (not live): %d frames over %.1f s from %s, recorded from %s at %s",
            len(self.index),
            duration,
            self.path,
            self.meta.get("source", "?"),
            self.meta.get("started_wall", "?"),
        )
        while True:
            await self._play_once(slot)
            if not self.loop:
                log.warning("replay of %s finished; no more frames (the belt will fault as stale)", self.path.name)
                return
            log.info("replay: looping %s", self.path.name)

    async def _play_once(self, slot: LatestFrameSlot) -> None:
        t0 = self.index[0]["received_at"]
        start = time.monotonic()
        for entry in self.index:
            image = await asyncio.to_thread(cv2.imread, str(self.path / entry["file"]))
            if image is None:
                self.missing += 1
                log.warning("replay: can't read %s; skipping", entry["file"])
                continue
            due = start + (entry["received_at"] - t0) / self.speed
            if (wait := due - time.monotonic()) > 0:
                await asyncio.sleep(wait)
            self.frames += 1
            slot.put(Frame(int(entry["seq"]), image, time.monotonic(), None, self.name))
