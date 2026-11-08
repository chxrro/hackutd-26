"""Private numbered recordings: ordinary small JPEGs paired with completed analyses."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import tempfile
import threading
import time
from pathlib import Path

from analysis_log import AnalysisLog, analysis_record, utc_timestamp

DEFAULT_SESSIONS_DIR = Path(__file__).resolve().parents[1] / 'data' / 'sessions'


def private_write(path: Path, payload: bytes):
    """Install a complete private file; callers never see a partially encoded JPEG."""
    fd, temporary = tempfile.mkstemp(prefix='.writing-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def append_synced(path: Path, text: str):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, 'a', encoding='utf-8') as output:
        output.write(text)
        output.flush()
        os.fsync(output.fileno())


class SessionRecording:
    """One analysis session = one folder; pause/resume retains the same session.

    The JSONL is the authoritative index. A crash/disk error may leave an
    unindexed JPEG or observation; only indexed, acknowledged pairs are complete.
    """
    def __init__(self, root=DEFAULT_SESSIONS_DIR, *, aggregate_path=None):
        from PIL import Image  # Fail before starting a paid session if missing.
        self.Image = Image
        self.lock = threading.Lock()
        root = Path(root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        numbers = [int(p.name[8:]) for p in root.iterdir()
                   if p.name.startswith('session_') and p.name[8:].isdigit()]
        number = max(numbers, default=0) + 1
        while True:
            self.directory = root / f'session_{number:06d}'
            try:
                self.directory.mkdir(mode=0o700)
                break
            except FileExistsError:
                number += 1
        self.session_id = self.directory.name
        self.path = self.directory / 'frames.jsonl'
        self.images = self.directory / 'images'
        self.images.mkdir(mode=0o700)
        self.observations = self.directory / 'observations.txt'
        self.aggregate = AnalysisLog(aggregate_path) if aggregate_path else None
        append_synced(self.path, '')
        append_synced(self.observations, '')
        manifest = {
            'schema_version': 2, 'session_id': self.session_id,
            'created_at_utc': utc_timestamp(time.time()),
            'session_boundary': 'server start or explicit New analysis session; pause/resume stays in this folder',
            'frames_index': 'frames.jsonl', 'observations': 'observations.txt',
            'archive': {'format': 'JPEG', 'max_width': 320, 'max_height': 240,
                        'quality': 75, 'resized_after_inference': True},
            'retention': 'local until manually deleted; contains private camera images',
        }
        private_write(self.directory / 'session.json',
                      (json.dumps(manifest, indent=2) + '\n').encode())

    def append_result(self, session_id, result, *, active_at_completion,
                      max_frame_age_seconds, image=None):
        if session_id != self.session_id:
            raise ValueError('Recording session ID mismatch')
        record = analysis_record(session_id, result, active_at_completion=active_at_completion,
                                 max_frame_age_seconds=max_frame_age_seconds)
        if not isinstance(image, str) or not image.startswith('data:image/jpeg;base64,'):
            raise ValueError('Analyzed JPEG is required for session recording')
        frame_id = result['frame_id']
        if isinstance(frame_id, bool) or not isinstance(frame_id, int) or frame_id < 1:
            raise ValueError('frame_id must be a positive integer')
        raw = base64.b64decode(image.split(',', 1)[1], validate=True)
        with self.Image.open(io.BytesIO(raw)) as original:
            # Also bound decompression for non-browser callers.
            if original.width * original.height > 4_000_000:
                raise ValueError('Archival source exceeds four megapixels')
            source_size = original.size
            reduced = original.convert('RGB')
            reduced.thumbnail((320, 240), self.Image.Resampling.LANCZOS)
            output = io.BytesIO()
            reduced.save(output, 'JPEG', quality=75, optimize=True, exif=b'')
        payload = output.getvalue()
        name = f'frame_{frame_id:06d}.jpg'
        record.update({'schema_version': 2, 'image': {
            'path': f'images/{name}', 'width': reduced.width, 'height': reduced.height,
            'source_width': source_size[0], 'source_height': source_size[1],
            'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest(),
            'format': 'JPEG', 'resized_after_inference': True,
        }})
        # Serialize before writing anything, rejecting NaN/invalid metadata.
        line = json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n'
        with self.lock:
            if (self.images / name).exists():
                raise ValueError(f'Frame {frame_id} has already been recorded')
            private_write(self.images / name, payload)
            append_synced(self.observations, result['observation'] + '\n\n')
            append_synced(self.path, line)
            if self.aggregate:
                self.aggregate.append_result(session_id, result,
                    active_at_completion=active_at_completion,
                    max_frame_age_seconds=max_frame_age_seconds)
