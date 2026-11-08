#!/usr/bin/env python3
"""Run local CPU tracking/range experiments over a saved numbered session. No Brev."""
import argparse
import hashlib
import json
import os
import time
from pathlib import Path

from fetch_perception_model import MODEL_PATH, MODEL_SHA256
from perception import MarkerRange, NanoDetector, ObjectTracker
from session_recording import append_synced, private_write


def replay(session_path, *, model_path=MODEL_PATH, calibration=None, marker_size_m=None, annotate=False):
    import cv2
    session_path = Path(session_path).expanduser().resolve()
    detector = NanoDetector(model_path)
    if bool(calibration) != (marker_size_m is not None):
        raise ValueError('Supply both --calibration and --marker-size-m')
    marker = MarkerRange(calibration, marker_size_m) if calibration else None
    tracker = ObjectTracker()
    number = 1
    while True:
        output = session_path / f'perception_{number:06d}.jsonl'
        try:
            fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            break
        except FileExistsError:
            number += 1
    annotation_dir = session_path / f'perception_{number:06d}_images'
    if annotate:
        annotation_dir.mkdir(mode=0o700)
    append_synced(output, json.dumps({'type': 'replay_configuration', 'schema_version': 1,
        'mode': 'offline_replay', 'source_index': 'frames.jsonl', 'model_sha256': MODEL_SHA256,
        'marker_size_m': marker_size_m,
        'calibration_sha256': hashlib.sha256(Path(calibration).read_bytes()).hexdigest() if calibration else None,
        'limitations': 'Experimental association/marker range; ordinary-object range and collision risk unknown. '
                       'Replay uses reduced archival JPEGs; no belt output.'}) + '\n')
    with (session_path / 'frames.jsonl').open() as index:
        for line in index:
            row = json.loads(line)
            image_path = (session_path / row['image']['path']).resolve()
            if not image_path.is_relative_to(session_path):
                raise ValueError('Image path escapes the session folder')
            raw = image_path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != row['image']['sha256']:
                raise ValueError(f'Image checksum mismatch for frame {row["frame_id"]}')
            import numpy as np
            image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError(f'Cannot decode frame {row["frame_id"]}')
            started = time.monotonic()
            detections = detector.detect(image)
            if marker:
                detections.extend(marker.detect(image))
            state = tracker.update(detections, row['captured_at'], image.shape[1])
            result = {'schema_version': 1, 'type': 'frame_perception', 'mode': 'offline_replay',
                'session_id': row['session_id'], 'frame_id': row['frame_id'],
                'captured_at': row['captured_at'], 'image': row['image']['path'],
                'original_stale_at_completion': row['stale_at_completion'],
                'original_active_at_completion': row['active_at_completion'],
                'processing_ms': round((time.monotonic() - started) * 1000, 2), **state}
            if annotate:
                for obj in state['objects']:
                    x1, y1, x2, y2 = map(round, obj['box'])
                    cv2.rectangle(image, (x1, y1), (x2, y2), (0, 200, 255), 1)
                    label = f'{obj["track_id"]} {obj["label"]}'
                    cv2.putText(image, label, (x1, max(12, y1-3)), cv2.FONT_HERSHEY_SIMPLEX, .35, (0, 200, 255), 1)
                ok, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if not ok:
                    raise OSError('Could not encode annotation')
                target = annotation_dir / f'frame_{row["frame_id"]:06d}.jpg'
                private_write(target, encoded.tobytes())
                result['annotation'] = str(target.relative_to(session_path))
            append_synced(output, json.dumps(result, allow_nan=False) + '\n')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('session', type=Path)
    parser.add_argument('--model', type=Path, default=MODEL_PATH)
    parser.add_argument('--calibration', type=Path)
    parser.add_argument('--marker-size-m', type=float)
    parser.add_argument('--annotate', action='store_true')
    args = parser.parse_args()
    print(replay(args.session, model_path=args.model, calibration=args.calibration,
                 marker_size_m=args.marker_size_m, annotate=args.annotate))


if __name__ == '__main__':
    main()
