"""Synthetic identity/geometry cases; no claims of real-world range accuracy."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
try:
    import cv2
    import numpy as np
except ImportError:
    raise unittest.SkipTest("Install requirements-perception.txt for CPU geometry tests")
from perception import MarkerRange, ObjectTracker


def detection(label='chair', box=None):
    return {'label': label, 'box': box or [10, 10, 50, 80], 'confidence': .9,
            'range': {'status': 'unknown', 'meters': None}}


class TrackerTests(unittest.TestCase):
    def test_identity_class_direction_and_looming_are_separate_from_risk(self):
        tracker = ObjectTracker()
        first = tracker.update([detection()], 10, 300)['objects'][0]
        second = tracker.update([detection(box=[10, 5, 55, 90])], 11, 300)['objects'][0]
        self.assertEqual(first['track_id'], second['track_id'])
        self.assertEqual(second['direction'], 'left')
        self.assertEqual(second['motion_evidence'], 'image_expansion')
        self.assertEqual(second['collision_risk'], 'unknown')
        third = tracker.update([detection('person')], 12, 300)['objects'][0]
        self.assertNotEqual(third['track_id'], first['track_id'])

    def test_missing_track_then_timeout_uses_capture_time(self):
        tracker = ObjectTracker()
        first = tracker.update([detection()], 10, 300)['objects'][0]
        missing = tracker.update([], 11, 300)
        self.assertEqual(missing['missing_tracks'][0]['state'], 'missing')
        self.assertEqual(missing['path_state'], 'unverified')
        next_obj = tracker.update([detection()], 15, 300)['objects'][0]
        self.assertNotEqual(next_obj['track_id'], first['track_id'])
        with self.assertRaises(ValueError):
            tracker.update([], 14, 300)

    def test_one_to_one_matching_for_multiple_objects(self):
        tracker = ObjectTracker()
        original = tracker.update([detection(), detection(box=[45, 10, 90, 80])], 10, 300)
        following = tracker.update([detection(box=[12, 10, 52, 80]), detection(box=[47, 10, 92, 80])], 11, 300)
        self.assertEqual([o['track_id'] for o in original['objects']], [o['track_id'] for o in following['objects']])
        self.assertEqual(len({o['track_id'] for o in following['objects']}), 2)


class MarkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'camera.json'
        self.path.write_text(json.dumps({'image_width': 640, 'image_height': 480,
            'camera_matrix': [[600, 0, 320], [0, 600, 240], [0, 0, 1]],
            'distortion_coefficients': [0, 0, 0, 0, 0]}))
        self.ranger = MarkerRange(self.path, .2)

    def marker_image(self, size=120):
        image = np.full((480, 640, 3), 255, dtype=np.uint8)
        marker = cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50), 0, size)
        start_x, start_y = 320-size//2, 240-size//2
        image[start_y:start_y+size, start_x:start_x+size] = cv2.cvtColor(marker, cv2.COLOR_GRAY2BGR)
        return image

    def test_synthetic_known_size_pose_and_scaled_intrinsics(self):
        image = self.marker_image()
        objects = self.ranger.detect(image)
        self.assertEqual(len(objects), 1)
        result = objects[0]['range']
        self.assertEqual(result['status'], 'experimental')
        self.assertAlmostEqual(result['meters'], 1.0, delta=.03)
        self.assertFalse(result['validated_on_real_measurements'])
        small = self.ranger.detect(cv2.resize(image, (320, 240)))[0]['range']
        self.assertAlmostEqual(small['meters'], result['meters'], delta=.03)

    def test_too_small_marker_unknown_and_wrong_crop_rejected(self):
        objects = self.ranger.detect(self.marker_image(18))
        self.assertEqual(objects[0]['range']['status'], 'unknown')
        self.assertIsNone(objects[0]['range']['meters'])
        with self.assertRaisesRegex(ValueError, 'aspect ratio'):
            self.ranger.detect(np.zeros((320, 640, 3), dtype=np.uint8))
        self.assertEqual(self.ranger.detect(np.zeros((480, 640, 3), dtype=np.uint8)), [])


class RangeEvaluationTests(unittest.TestCase):
    def test_unknown_ranges_count_against_coverage(self):
        from evaluate_ranges import evaluate
        rows = [{'type': 'frame_perception', 'frame_id': 1, 'objects': [
            {'label': 'aruco_4x4_50:0', 'range': {'meters': 1.1}},
            {'label': 'person', 'range': {'meters': None}},
            {'label': 'person', 'range': {'meters': None}}]}]
        report = evaluate(rows, [{'frame_id': 1, 'label': 'aruco_4x4_50:0', 'meters': 1.0},
                                 {'frame_id': 2, 'label': 'aruco_4x4_50:0', 'meters': 2.0}])
        self.assertEqual(report['coverage'], .5)
        self.assertAlmostEqual(report['mean_absolute_error_m'], .1)
        self.assertFalse(report['safety_validated'])
        with self.assertRaises(ValueError):
            evaluate(rows, [{'frame_id': 1, 'label': 'target', 'meters': 0}])


class ReplayIntegrationTests(unittest.TestCase):
    def test_real_cpu_model_replay_pairs_frames_and_numbers_outputs(self):
        # Actual downloaded detector and synthetic pixels: no real-camera claim.
        from fetch_perception_model import MODEL_PATH
        if not MODEL_PATH.exists():
            self.skipTest('Run scripts/fetch_perception_model.py for actual detector replay')
        import base64
        import io
        from PIL import Image
        from session_recording import SessionRecording
        from replay_perception import replay
        with tempfile.TemporaryDirectory() as folder:
            recording = SessionRecording(Path(folder))
            source = io.BytesIO()
            Image.new('RGB', (640, 480), (255, 255, 255)).save(source, 'JPEG')
            data_url = 'data:image/jpeg;base64,' + base64.b64encode(source.getvalue()).decode()
            for frame_id in (1, 2, 3):
                recording.append_result(recording.session_id,
                    {'frame_id': frame_id, 'captured_at': 100 + frame_id, 'received_at': 100.1 + frame_id,
                     'frame_age_seconds': .1, 'observation': 'Synthetic blank test'}, image=data_url,
                    active_at_completion=True, max_frame_age_seconds=3)
            result = replay(recording.directory, annotate=True)
            rows = [json.loads(line) for line in result.read_text().splitlines()]
            self.assertEqual([row['frame_id'] for row in rows[1:]], [1, 2, 3])
            self.assertEqual(rows[1]['collision_risk'], 'unknown')
            self.assertTrue((recording.directory / rows[1]['annotation']).exists())
            second = replay(recording.directory)
            self.assertEqual(second.name, 'perception_000002.jsonl')
            image = recording.directory / 'images/frame_000001.jpg'
            image.write_bytes(b'corruption')
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                replay(recording.directory)


if __name__ == '__main__':
    unittest.main()
