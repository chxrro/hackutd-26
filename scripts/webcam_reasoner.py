#!/usr/bin/env python3
"""Local webcam test. Starts the existing Brev VM only when an image is submitted."""
from __future__ import annotations

import argparse
import base64
import json
import math
import secrets
import shutil
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError

from reasoner import interpret_image, request_json
from analysis_log import AnalysisLog
from session_recording import SessionRecording, DEFAULT_SESSIONS_DIR
from collision import CollisionEngine, summary as collision_summary

INSTANCE = 'hackutd-reasoner'
REMOTE_DIR = '/home/ubuntu/workspace/hackutd-26/reasoner'
# Local ports avoid the hub: 8765 is its glasses WebSocket, and 8000 is the
# hub's documented port-forward for its cloud classifier. The VM side stays 8000.
TUNNEL_PORT = 8001
PAGE_PORT = 8770
BASE_URL = f'http://127.0.0.1:{TUNNEL_PORT}/v1'
SESSION_SECONDS = 20 * 60
COMPUTE_HOURLY = 1.4544  # Recorded allocation price, not a live billing query.
MAX_BODY = 700_000
MAX_FRAME_AGE_SECONDS = 3.0  # Prototype freshness limit, not a safety threshold.
RATE_WINDOW_SECONDS = 10.0
STOP_RETRY_SECONDS = 30.0
# Transport-level failures that are worth retrying with a fresh frame.
TRANSIENT_ERRORS = (URLError, TimeoutError, ConnectionError, HTTPException, json.JSONDecodeError)


def instance_row(listing: str) -> list[str]:
    """Columns of the `brev ls` row whose first column is exactly INSTANCE."""
    return next((line.split() for line in listing.splitlines() if line.split()[:1] == [INSTANCE]), [])


def decode_frame(payload: dict) -> str:
    image = payload.get('image', '')
    if not isinstance(image, str) or not image.startswith('data:image/jpeg;base64,'):
        raise ValueError('Capture a JPEG frame from the camera first.')
    raw = base64.b64decode(image.split(',', 1)[1], validate=True)
    if not 100 < len(raw) <= 512_000 or not raw.startswith(b'\xff\xd8\xff'):
        raise ValueError('Invalid JPEG frame or frame exceeds 500 KB.')
    return image


def capture_time(value: object) -> float:
    """Browser and server run on the same Mac; require a recent finite timestamp."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('captured_at must be a finite Unix timestamp in seconds.')
    age = time.time() - value
    if age < -0.5 or age > MAX_FRAME_AGE_SECONDS:
        raise ValueError('Frame timestamp is in the future or stale; capture a new frame.')
    return float(value)


@dataclass(frozen=True)
class Frame:
    image: str
    captured_at: float
    frame_id: int
    generation: int


class Session:
    def __init__(self, brev: str, analysis_log: AnalysisLog | None = None, collision_engine=None):
        self.brev = brev
        self.analysis_log = analysis_log
        self.collision_engine = collision_engine
        self.session_id = getattr(analysis_log, 'session_id', None) or secrets.token_hex(16)
        self.saved_analyses = 0
        self.log_error = None
        self.lock = threading.RLock()
        self.launch_lock = threading.Lock()
        self.condition = threading.Condition(self.lock)
        self.pending = None
        self.worker_running = False
        self.generation = 0
        self.paused = False
        self.paused_at = float("-inf")
        self.accepted = 0
        self.replaced = 0
        self.out_of_order = 0
        self.latest_capture_at = float("-inf")
        self.expired = 0
        self.completed = 0
        self.discarded_on_pause = 0
        self.completions = deque(maxlen=120)
        self.cancelled = threading.Event()
        self.started = None
        self.ended_at = None
        self.deadline = None
        self.status = 'Preview only. No analysis has started in this local session.'
        self.stage = 'idle'
        self.busy = False
        self.result = None
        self.error = None
        self.model = None
        self.stop_started = False
        self.ssh_guard = False
        self.finished = False
        self.camera_client = None
        self.tunnel = None
        self.inference_failures = 0

    def command(self, *args, timeout=60):
        for attempt in range(4):
            result = subprocess.run(
                [self.brev, *args], capture_output=True, text=True, timeout=timeout,
            )
            if not result.returncode:
                return result.stdout
            detail = (result.stderr or result.stdout or 'No diagnostic output.')[-1200:]
            if 'resource exhausted' not in detail.lower() or attempt == 3:
                raise RuntimeError(f'Brev {args[0]} failed: {detail}')
            # Retry the operation, not the paid instance. This also works during
            # Stop, whose cancellation flag is already set.
            time.sleep(2 ** attempt)

    def start_tunnel(self):
        # A dedicated SSH process avoids stale multiplexed forwarding channels
        # surviving VM restarts. The port stays loopback-only.
        self.tunnel = subprocess.Popen([
            'ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
            '-o', 'ControlMaster=no', '-o', 'ControlPath=none',
            '-o', 'ExitOnForwardFailure=yes', '-N',
            '-L', f'127.0.0.1:{TUNNEL_PORT}:127.0.0.1:8000', INSTANCE,
        ], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


    def snapshot(self):
        with self.lock:
            now = time.time()
            elapsed = 0 if self.started is None else (self.ended_at or now) - self.started
            result = None if self.result is None else dict(self.result)
            freshness = 'unavailable'
            if result is not None:
                age = max(0, now - result['captured_at'])
                freshness = ('stopped' if self.cancelled.is_set() else 'unavailable' if self.error or self.stage == 'retrying' else 'paused' if self.paused
                             else 'stale' if age > MAX_FRAME_AGE_SECONDS else 'recent')
                result['current_frame_age_seconds'] = round(age, 3)
                result['stale'] = freshness != 'recent'
            recent = [t for t in self.completions if time.monotonic() - t <= RATE_WINDOW_SECONDS]
            rate = None
            if len(recent) >= 2 and recent[-1] > recent[0]:
                rate = round((len(recent) - 1) / (recent[-1] - recent[0]), 2)
            if self.cancelled.is_set() or self.paused:
                rate = 0.0
            return {
                'stage': self.stage, 'status': self.status, 'busy': self.busy,
                'result': result, 'freshness': freshness, 'paused': self.paused,
                'error': self.error, 'finished': self.finished,
                'remaining_seconds': None if self.deadline is None else max(0, int(self.deadline - now)),
                'estimated_compute_usd': round(elapsed * COMPUTE_HOURLY / 3600, 3),
                'remote_shutdown_guard': self.ssh_guard,
                'camera_client': self.camera_client,
                'inference_failures': self.inference_failures,
                'analysis_log': {'enabled': self.analysis_log is not None,
                                 'path': str(self.analysis_log.path) if self.analysis_log else None,
                                 'saved_this_session': self.saved_analyses,
                                 'error': self.log_error},
                'target_capture_fps': 1,
                'processed_fps': rate,
                'max_frame_age_seconds': MAX_FRAME_AGE_SECONDS,
                'frames': {'accepted': self.accepted, 'completed': self.completed,
                           'replaced': self.replaced, 'expired': self.expired,
                           'out_of_order': self.out_of_order,
                           'discarded_on_pause': self.discarded_on_pause,
                           'pending': int(self.pending is not None)},
            }

    def submit(self, image: str, captured_at: float | None = None):
        captured = capture_time(time.time() if captured_at is None else captured_at)
        with self.condition:
            if self.cancelled.is_set() or self.finished:
                raise ValueError('This session has ended. Restart the local server for another test.')
            if captured <= self.paused_at:
                raise ValueError('Frame predates the last pause; capture a new frame.')
            if captured <= self.latest_capture_at:
                self.out_of_order += 1
                raise ValueError('Frame is older than the latest accepted capture; capture a new frame.')
            self.latest_capture_at = captured
            self.accepted += 1
            if self.pending is not None:
                self.replaced += 1
            self.pending = Frame(image, captured, self.accepted, self.generation)
            self.paused = False
            self.error = None
            if self.started is None:
                self.started = time.time()
                self.deadline = self.started + SESSION_SECONDS
                threading.Thread(target=self.watchdog, daemon=True).start()
            if not self.worker_running:
                self.worker_running = True
                self.busy = True
                threading.Thread(target=self.process, daemon=True).start()
            self.condition.notify_all()

    def pause(self):
        """Discard pending work; an in-flight request can finish but cannot publish."""
        with self.condition:
            self.paused = True
            self.paused_at = time.time()
            self.generation += 1
            if self.pending is not None:
                self.discarded_on_pause += 1
            self.pending = None
            self.condition.notify_all()

    def update(self, stage, status):
        with self.lock:
            self.stage, self.status = stage, status

    def watchdog(self):
        """Stop at the deadline, then keep retrying a failed shutdown until Brev
        confirms STOPPED, so a closed page cannot leave a failed stop unattended."""
        while not self.cancelled.wait(1):
            if time.time() >= self.deadline:
                self.stop()
                break
        while True:
            with self.lock:
                finished, stopping = self.finished, self.stop_started
                failed = self.stage == 'stop-error' and not stopping
            if finished or not (failed or stopping):
                return
            if failed:
                self.stop()
            else:
                time.sleep(STOP_RETRY_SECONDS)

    def prepare(self):
        self.update('starting', 'Starting the GPU. The 20-minute timer includes startup.')
        with self.launch_lock:
            if self.cancelled.is_set():
                return
            row = instance_row(self.command('ls', timeout=20))
            while 'STOPPING' in row:
                self.update('waiting', 'Waiting for the previous GPU shutdown before restarting.')
                if self.cancelled.wait(5):
                    return
                if time.time() >= self.deadline - 120:
                    raise RuntimeError('Previous GPU shutdown did not finish before the session deadline.')
                row = instance_row(self.command('ls', timeout=20))
            if not any(status in row for status in ('RUNNING', 'STARTING')):
                self.command('start', INSTANCE, '--detached', timeout=30)
        # Brev may return before SSH becomes ready.
        while not self.cancelled.is_set() and time.time() < self.deadline - 90:
            try:
                self.command('refresh', timeout=30)
                # A second timer on the VM survives browser closure or Mac sleep.
                # Removing the prior timer permits another explicitly started test.
                remaining = max(30, int(self.deadline - time.time()))
                guard = (
                    'sudo -n systemctl stop hackutd-camera-autostop.timer 2>/dev/null; '
                    'sudo -n systemd-run --unit=hackutd-camera-autostop --collect '
                    f'--on-active={remaining}s /usr/sbin/shutdown -h now'
                )
                self.command('exec', INSTANCE, guard, timeout=35)
                self.ssh_guard = True
                break
            except (RuntimeError, subprocess.TimeoutExpired):
                if self.cancelled.wait(8):
                    return
        if self.cancelled.is_set():
            return
        if not self.ssh_guard:
            raise RuntimeError('Could not install the VM shutdown timer; stopping to limit spending.')
        self.update('loading', 'Loading Cosmos3-Edge from the retained cache.')
        self.command('exec', INSTANCE, f'cd {REMOTE_DIR} && bash start.sh', timeout=150)
        if self.cancelled.is_set():
            return
        self.update('connecting', 'Connecting the private model tunnel.')
        with self.condition:
            # Under the lock stop() uses, so a concurrent stop always sees the tunnel.
            if self.cancelled.is_set():
                return
            self.start_tunnel()
        while not self.cancelled.is_set() and time.time() < self.deadline - 60:
            if self.tunnel is not None and self.tunnel.poll() is not None:
                detail = self.tunnel.stderr.read().decode(errors='replace')[-600:]
                raise RuntimeError(f'Private SSH tunnel exited: {detail}')
            try:
                listing = request_json(BASE_URL, '/models', None, 5)
                models = listing.get('data', [])
                if models and models[0].get('id') == 'cosmos3-edge':
                    self.model = models[0]['id']
                    return
            except (OSError, ValueError):
                pass
            if self.cancelled.wait(4):
                return
        if not self.cancelled.is_set():
            raise RuntimeError('The model did not become ready before the session deadline.')

    def process(self):
        try:
            if self.model is None:
                self.prepare()
            while not self.cancelled.is_set():
                with self.condition:
                    while self.pending is None and not self.cancelled.is_set():
                        self.busy = False
                        self.update('ready', 'Waiting for a frame. The GPU timer is still running.')
                        self.condition.wait()
                    if self.cancelled.is_set():
                        return
                    frame, self.pending = self.pending, None
                    if time.time() - frame.captured_at > MAX_FRAME_AGE_SECONDS:
                        self.expired += 1
                        continue
                    self.busy = True
                    self.update('analyzing', 'Checking recent frames for a possible collision; experimental assessment.')
                try:
                    result = (self.collision_engine.analyze(BASE_URL, self.model, frame)
                              if self.collision_engine else interpret_image(BASE_URL, self.model, frame.image))
                except TRANSIENT_ERRORS as error:
                    if isinstance(error, HTTPError) and error.code not in {408, 429, 500, 502, 503, 504}:
                        raise
                    with self.condition:
                        self.inference_failures += 1
                        self.error = f'Temporary inference failure; retrying a fresh frame: {error}'
                        self.update('retrying', 'Inference unavailable; waiting to retry the latest frame.')
                    if self.cancelled.wait(1):
                        return
                    continue
                with self.condition:
                    self.error = None
                result.update({
                    'frame_id': frame.frame_id,
                    'captured_at': frame.captured_at,
                    'frame_age_seconds': round(time.time() - frame.captured_at, 3),
                    'received_at': time.time(),
                })
                if result.get('collision') and result['frame_age_seconds'] > MAX_FRAME_AGE_SECONDS:
                    result['collision']['candidate_state'] = result['collision']['state']
                    result['collision'].update(state='unknown', reason='stale_frame')
                    result['observation'] = collision_summary(result['collision'])
                with self.condition:
                    self.completed += 1
                    self.completions.append(time.monotonic())
                    active_at_completion = not self.cancelled.is_set() and frame.generation == self.generation
                if self.analysis_log is not None:
                    try:
                        self.analysis_log.append_result(
                            self.session_id, result, active_at_completion=active_at_completion,
                            max_frame_age_seconds=MAX_FRAME_AGE_SECONDS, image=frame.image,
                        )
                    except (OSError, ValueError, TypeError) as error:
                        with self.lock:
                            self.log_error = f'Could not save frame analysis: {error}'
                            # Keep the observation available for manual recovery.
                            if active_at_completion:
                                self.result = {**result, 'persisted': False}
                        raise RuntimeError(self.log_error) from error
                    with self.lock:
                        self.saved_analyses += 1
                with self.condition:
                    if not self.cancelled.is_set() and frame.generation == self.generation:
                        self.result = {**result, 'persisted': self.analysis_log is not None}
        except Exception as error:
            with self.lock:
                self.error = str(error)
            # Inference failure must not leave a paid GPU session running unattended.
            self.stop()
        finally:
            with self.lock:
                self.busy = False
                self.worker_running = False

    def stop(self):
        with self.condition:
            self.cancelled.set()
            if self.tunnel is not None and self.tunnel.poll() is None:
                self.tunnel.terminate()
            self.pending = None
            self.condition.notify_all()
            if self.started is None:
                self.finished = True
                self.update('stopped', 'Local session ended. No GPU start was requested.')
                return
            if self.stop_started:
                return
            self.stop_started = True
        self.update('stopping', 'Stopping the Brev instance; checking cloud state.')
        try:
            # A stop cannot race ahead of the start command.
            with self.launch_lock:
                try:
                    self.command('stop', INSTANCE, timeout=60)
                except (RuntimeError, subprocess.TimeoutExpired):
                    pass  # The VM timer may already have stopped it; verify below.
            for attempt in range(60):
                if 'STOPPED' in instance_row(self.command('ls', timeout=20)):
                    with self.lock:
                        self.finished = True
                        self.ended_at = time.time()
                        self.update('stopped', 'Brev confirmed STOPPED. Storage remains billable.')
                    return
                if attempt in {12, 24, 36, 48}:
                    try:
                        self.command('stop', INSTANCE, timeout=30)
                    except (RuntimeError, subprocess.TimeoutExpired):
                        pass
                time.sleep(5)
            raise RuntimeError('Stop accepted, but STOPPED has not yet been confirmed.')
        except Exception as error:
            with self.lock:
                self.error = f'{self.error + " " if self.error else ""}Shutdown: {error}'
                self.update('stop-error', 'Check Brev and run: brev stop hackutd-reasoner')
                self.stop_started = False  # Allow the Stop button to retry.


class SessionHost:
    """Own the active session, so stop/shutdown always target the current worker."""
    def __init__(self, current, factory=None):
        self.current = current
        self.factory = factory
        self.lock = threading.RLock()

    def snapshot(self):
        with self.lock:
            value = self.current.snapshot()
            value['session_id'] = self.current.session_id
            value['can_start_new_session'] = bool(
                self.factory and self.current.finished and not self.current.worker_running)
            return value

    def new_session(self):
        with self.lock:
            with self.current.lock:
                if not self.factory or not self.current.finished or self.current.worker_running:
                    raise ValueError('Wait for the previous session to finish stopping before starting a new one.')
                # Build first. A recording/disk failure leaves the old session intact.
                replacement = self.factory()
            self.current = replacement
            return self.snapshot()

    def stop(self):
        with self.lock:
            current = self.current
        current.stop()


def make_handler(session, token):
    host = session if isinstance(session, SessionHost) else SessionHost(session)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Never log camera payloads.

        def respond(self, code, payload, content_type='application/json'):
            body = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Permissions-Policy', 'camera=(self), microphone=()')
            self.end_headers()
            self.wfile.write(body)

        def local_request(self):
            return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

        def do_GET(self):
            if not self.local_request():
                self.respond(403, {'error': 'Use the printed 127.0.0.1 address.'})
            elif self.path == '/':
                page = Path(__file__).with_name('webcam_reasoner.html').read_text()
                self.respond(200, page.replace('__TOKEN__', token), 'text/html; charset=utf-8')
            elif self.path == '/webcam_reasoner.js':
                self.respond(200, Path(__file__).with_name('webcam_reasoner.js').read_text(), 'text/javascript; charset=utf-8')
            elif self.path == '/status':
                self.respond(200, host.snapshot())
            else:
                self.respond(404, {'error': 'Not found.'})

        def do_POST(self):
            origin = self.headers.get('Origin')
            expected = f'http://127.0.0.1:{self.server.server_port}'
            if not self.local_request() or origin != expected or self.headers.get('X-Session-Token') != token:
                self.respond(403, {'error': 'Local session authorization required.'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= MAX_BODY:
                    raise ValueError('Request is empty or exceeds the camera frame limit.')
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError('Expected a JSON object.')
                with host.lock:
                    session = host.current
                    # A page from an earlier recording must never start/stop the
                    # new session, even if an old upload arrives after rollover.
                    if host.factory and payload.get('session_id') != session.session_id:
                        self.respond(409, {'error': 'Analysis session changed. Refresh the page before continuing.'})
                        return
                    if self.path == '/new-session':
                        host.new_session()
                    elif self.path == '/analyze':
                        session.submit(decode_frame(payload), capture_time(payload.get('captured_at')))
                    elif self.path == '/camera-status':
                        event = payload.get('event')
                        if event not in {'requesting', 'ready', 'error', 'live', 'hidden', 'visible'}:
                            raise ValueError('Unknown camera status event.')
                        with session.lock:
                            session.camera_client = {'event': event,
                                'error_name': str(payload.get('error_name', ''))[:80],
                                'received_at': time.time()}
                    elif self.path == '/pause':
                        session.pause()
                    elif self.path == '/stop':
                        threading.Thread(target=session.stop, daemon=True).start()
                    else:
                        self.respond(404, {'error': 'Not found.'})
                        return
                    self.respond(202, host.snapshot())
            except OSError as error:
                self.respond(500, {'error': f'Cannot create the new recording: {error}'})
            except (ValueError, TypeError) as error:
                self.respond(400, {'error': str(error)})
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=PAGE_PORT)
    parser.add_argument('--sessions-dir', type=Path, default=DEFAULT_SESSIONS_DIR,
                        help='Private numbered recording folders (JPEGs, JSONL, observations)')
    parser.add_argument('--analysis-log', type=Path,
                        help='Also append text/metadata to this legacy aggregate JSONL file')
    args = parser.parse_args()
    brev = shutil.which('brev') or str(Path.home() / '.local/bin/brev')
    if not Path(brev).is_file():
        parser.error('Install and authenticate the Brev CLI first.')
    def create_session():
        recording = SessionRecording(args.sessions_dir, aggregate_path=args.analysis_log)
        return Session(brev, recording, CollisionEngine())
    try:
        host = SessionHost(create_session(), create_session)
    except (OSError, ImportError) as error:
        parser.error(f'Cannot open the session recording: {error}')
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(host, secrets.token_urlsafe(32)))
    def interrupt(*_):
        raise KeyboardInterrupt
    # Closing the terminal (SIGHUP) must also request a GPU stop.
    signal.signal(signal.SIGTERM, interrupt)
    if hasattr(signal, 'SIGHUP'):
        signal.signal(signal.SIGHUP, interrupt)
    print(f'Camera test: http://127.0.0.1:{server.server_port}', flush=True)
    print(f'Session recording: {host.current.analysis_log.path}', flush=True)
    print('Preview is local. Start live or Analyze starts a 20-minute GPU session (~$0.49 compute + storage).', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
        host.stop()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
