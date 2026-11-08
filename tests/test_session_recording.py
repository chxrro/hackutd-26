"""Synthetic JPEG/worker tests; no camera, network, cloud or real user files."""
import base64
import concurrent.futures
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from PIL import Image
from session_recording import SessionRecording
import webcam_reasoner as webcam


def jpeg(width=640, height=480):
    output = io.BytesIO()
    Image.new('RGB', (width, height), (30, 100, 90)).save(output, 'JPEG')
    return 'data:image/jpeg;base64,' + base64.b64encode(output.getvalue()).decode()


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'sessions'
        self.result = {'frame_id': 1, 'captured_at': time.time(), 'received_at': time.time(),
            'frame_age_seconds': .1, 'observation': 'Synthetic test target', 'model': 'test'}

    def save(self, recording, **kwargs):
        recording.append_result(recording.session_id, self.result, image=jpeg(),
                                active_at_completion=True, max_frame_age_seconds=3, **kwargs)

    def test_jpeg_and_observation_are_paired_private_and_reduced(self):
        recording = SessionRecording(self.root)
        self.save(recording)
        record = json.loads(recording.path.read_text())
        path = recording.directory / record['image']['path']
        with Image.open(path) as image:
            self.assertEqual(image.size, (320, 240))
            self.assertEqual(image.getexif(), {})
        self.assertEqual(record['image']['source_width'], 640)
        self.assertEqual(record['image']['source_height'], 480)
        self.assertLess(path.stat().st_size, len(base64.b64decode(jpeg().split(',')[1])))
        self.assertEqual(recording.observations.read_text(), 'Synthetic test target\n\n')
        for private in (recording.path, path, recording.observations, recording.directory / 'session.json'):
            self.assertEqual(os.stat(private).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(recording.directory).st_mode & 0o777, 0o700)
        self.assertNotIn('base64', recording.path.read_text())

    def test_concurrent_allocations_and_existing_sessions_are_preserved(self):
        first = SessionRecording(self.root)
        self.save(first)
        before = first.path.read_bytes()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            recordings = list(pool.map(lambda _: SessionRecording(self.root), range(8)))
        self.assertEqual(len({r.session_id for r in recordings}), 8)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),
                         [f'session_{n:06d}' for n in range(1, 10)])
        self.assertEqual(first.path.read_bytes(), before)
        with self.assertRaisesRegex(ValueError, 'already been recorded'):
            self.save(first)
        self.assertEqual(first.path.read_bytes(), before)

    def test_invalid_image_produces_no_record_or_image(self):
        recording = SessionRecording(self.root)
        with self.assertRaises(ValueError):
            recording.append_result(recording.session_id, self.result, image='not a jpeg',
                                    active_at_completion=False, max_frame_age_seconds=3)
        self.assertEqual(recording.path.read_text(), '')
        self.assertEqual(list(recording.images.iterdir()), [])

    def test_inference_gets_original_then_saves_even_when_paused(self):
        recording = SessionRecording(self.root)
        session = webcam.Session('/unused/brev', recording)
        session.model = 'synthetic'
        original = jpeg()
        session.pending = webcam.Frame(original, time.time(), 7, 0)
        def inference(base, model, image):
            self.assertEqual(image, original)
            self.assertEqual(list(recording.images.iterdir()), [])
            session.pause()
            session.cancelled.set()
            return {'model': model, 'observation': 'Synthetic paused completion'}
        with patch.object(webcam, 'interpret_image', side_effect=inference):
            session.process()
        row = json.loads(recording.path.read_text())
        self.assertEqual(row['frame_id'], 7)
        self.assertFalse(row['active_at_completion'])
        self.assertEqual(session.saved_analyses, 1)
        self.assertIsNone(session.result)

    def test_image_write_failure_not_acknowledged_and_stops_gpu(self):
        recording = SessionRecording(self.root)
        session = webcam.Session('/unused/brev', recording)
        session.model = 'synthetic'
        session.pending = webcam.Frame(jpeg(), time.time(), 1, 0)
        with patch.object(webcam, 'interpret_image', return_value={'observation': 'Synthetic unsaved'}), patch('session_recording.private_write', side_effect=OSError('disk full')), patch.object(session, 'stop') as stop:
            session.process()
        stop.assert_called_once()
        self.assertEqual(session.saved_analyses, 0)
        self.assertFalse(session.result['persisted'])
        self.assertEqual(recording.path.read_text(), '')


if __name__ == '__main__':
    unittest.main()
