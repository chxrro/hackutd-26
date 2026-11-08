"""Append-only frame analysis records. Never serialize camera payloads or requests."""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ANALYSIS_LOG = Path(__file__).resolve().parents[1] / 'data' / 'analysis' / 'frames.jsonl'
TOKEN_FIELDS = ('prompt_tokens', 'completion_tokens', 'total_tokens')


def utc_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec='milliseconds')


class AnalysisLog:
    """Each completed append is flushed/fsynced before it is acknowledged."""
    def __init__(self, path: Path):
        self.path = Path(path).expanduser().resolve()
        self.lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._create_private_file()

    def _create_private_file(self):
        descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)

    def append_result(self, session_id: str, result: dict, *, active_at_completion: bool,
                      max_frame_age_seconds: float, image: str | None = None) -> None:
        record = analysis_record(session_id, result, active_at_completion=active_at_completion,
                                 max_frame_age_seconds=max_frame_age_seconds)
        line = json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n'
        with self.lock:
            with self.path.open('a', encoding='utf-8') as output:
                output.write(line)
                output.flush()
                os.fsync(output.fileno())


def analysis_record(session_id, result, *, active_at_completion, max_frame_age_seconds):
    usage = result.get('usage')
    if not isinstance(usage, dict):
        usage = {}
    safe_usage = {key: usage[key] for key in TOKEN_FIELDS
                  if isinstance(usage.get(key), int) and not isinstance(usage[key], bool)
                  and usage[key] >= 0}
    # Deliberate allowlist: never dump the frame, request, or entire API response.
    record = {
        'schema_version': 1,
        'type': 'frame_analysis',
        'session_id': session_id,
        'frame_id': result['frame_id'],
        'captured_at': result['captured_at'],
        'captured_at_utc': utc_timestamp(result['captured_at']),
        'completed_at': result['received_at'],
        'completed_at_utc': utc_timestamp(result['received_at']),
        'model': result.get('model'),
        'observation': result['observation'],
        'request_latency_ms': result.get('request_latency_ms'),
        'frame_age_seconds': result['frame_age_seconds'],
        'stale_at_completion': result['frame_age_seconds'] > max_frame_age_seconds,
        'active_at_completion': active_at_completion,
        'usage': safe_usage,
    }
    collision = result.get('collision')
    if isinstance(collision, dict):
        top_fields = ('schema_version', 'state', 'candidate_state', 'reason', 'camera_motion',
                      'input_format', 'validated', 'range_m', 'ttc_seconds')
        object_fields = ('track_id', 'label', 'box', 'direction', 'relative_motion',
                         'independent_motion','state','reason','range_m','ttc_seconds','motion_source',
                         'motion_status','scale_ratio','horizontal_velocity','flow_points','flow_inlier_fraction')
        record['collision'] = {key: collision[key] for key in top_fields if key in collision}
        record['collision']['objects'] = [
            {key: obj[key] for key in object_fields if key in obj}
            for obj in collision.get('objects', []) if isinstance(obj, dict)]
        for collection, fields in (
            ('temporal_frames', ('frame_id', 'captured_at')),
            ('missing_tracks', ('track_id', 'label', 'state', 'seconds_since_seen')),
        ):
            record['collision'][collection] = [
                {key: obj[key] for key in fields if key in obj}
                for obj in collision.get(collection, []) if isinstance(obj, dict)]
    return record
