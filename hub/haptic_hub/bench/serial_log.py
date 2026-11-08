"""Optional `--serial PORT`: read the belt's USB serial log while the bench runs.

The firmware has no BLE status or notify characteristic, so over BLE alone the
hub can't see the TTL lapse. But it prints every state change on USB serial
(`[belt] state -> link-lost`, `[ble] hub disconnected`, `[ble] rejected command
(...)`, see esp32/src/main.cpp). With the belt on USB, the bench times TTL
expiry from those lines and also catches rejected commands and reboots.

Read-only: the bench never writes to the port (the console is disabled while
the hub is connected anyway). POSIX only (macOS, Linux); stdlib termios, no
pyserial. Close `pio device monitor` first, or the two readers split the lines.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import select
import threading
import time

from haptic_hub.bench.links import EventLog, ObservedEvent

log = logging.getLogger(__name__)

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^\[belt\] state -> (\S+)"), "state"),
    (re.compile(r"^\[belt\] mode -> (\S+)"), "mode"),  # firmware from 2026-11-08 on
    (re.compile(r"^\[ble\] hub connected"), "connected"),
    (re.compile(r"^\[ble\] hub disconnected"), "disconnected"),
    (re.compile(r"^\[ble\] rejected command \((.+)\)"), "rejected"),
    (re.compile(r"^\[belt\] \d+ motors, self-test"), "boot"),
]


def parse_serial_line(line: str, t: float) -> ObservedEvent:
    text = line.strip()
    for pattern, kind in _PATTERNS:
        m = pattern.match(text)
        if m:
            return ObservedEvent(t, kind, m.group(1) if m.groups() else None, text)
    return ObservedEvent(t, "other", None, text)


def open_serial(path: str, baud: int = 115200) -> int:
    """Raw, read-only-in-practice tty at `baud`. HUPCL is cleared so closing the
    port doesn't drop DTR (which can reset an ESP32 dev board)."""
    import termios
    import tty

    speed = getattr(termios, f"B{baud}", None)
    if speed is None:
        raise OSError(f"baud {baud} not supported by this platform's termios")
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        tty.setraw(fd)
        attrs = termios.tcgetattr(fd)
        attrs[2] = (attrs[2] | termios.CLOCAL | termios.CREAD) & ~termios.HUPCL
        attrs[4] = attrs[5] = speed
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
    except BaseException:
        os.close(fd)
        raise
    return fd


class SerialObserver(EventLog):
    def __init__(self, port: str, baud: int = 115200) -> None:
        super().__init__(source=f"serial:{port}")
        self.port = port
        self.baud = baud
        self._fd: int | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._fd = open_serial(self.port, self.baud)
        self._thread = threading.Thread(target=self._read_loop, name="bench-serial", daemon=True)
        self._thread.start()

    async def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            await asyncio.to_thread(self._thread.join, 1.0)
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def _post(self, event: ObservedEvent) -> None:
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self.add, event)

    def _read_loop(self) -> None:
        assert self._fd is not None
        buf = b""
        while not self._stop.is_set():
            try:
                ready, _, _ = select.select([self._fd], [], [], 0.1)
                if not ready:
                    continue
                data = os.read(self._fd, 4096)
            except BlockingIOError:
                continue
            except OSError as exc:
                self._post(ObservedEvent(time.monotonic(), "error", str(exc), f"serial read failed: {exc}"))
                return
            now = time.monotonic()
            if not data:
                self._post(ObservedEvent(now, "error", "eof", "serial port closed (board unplugged?)"))
                return
            buf += data
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                text = raw.decode("utf-8", "replace").strip()
                if text:
                    self._post(parse_serial_line(text, now))
            if len(buf) > 4096:  # no newline for a long time: garbage at the wrong baud
                buf = b""
