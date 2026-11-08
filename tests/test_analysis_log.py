"""Persistence checks use temporary files and synthetic responses, never the real log."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from analysis_log import AnalysisLog
import webcam_reasoner as webcam


class AnalysisLogTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'private' / 'frames.jsonl'
        self.result = {
            'frame_id': 1, 'captured_at': 1794100000.0, 'received_at': 1794100001.0,
            'model': 'test-model', 'observation': 'A chair on the left.\nVisibility: uncertain — dim light.',
            'request_latency_ms': 900, 'frame_age_seconds': 1.0,
            'usage': {'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30,
                      'prompt_tokens_details': {'private': 'must-not-be-saved'}},
            'image': 'data:image/jpeg;base64,private-payload-must-not-be-saved',
            'request': {'Authorization': 'private-token-must-not-be-saved'},
        }

    def append(self, logger, session='first', **kwargs):
        logger.append_result(session, kwargs.pop('result', self.result),
                             active_at_completion=kwargs.pop('active', True), max_frame_age_seconds=3)

    def test_results_survive_reopening_and_new_sessions_without_overwrite(self):
        self.append(AnalysisLog(self.path), 'first')
        self.append(AnalysisLog(self.path), 'second', result={**self.result, 'frame_id': 2})
        rows = self.path.read_text().splitlines()
        self.assertEqual(len(rows), 2)
        records = [json.loads(line) for line in rows]
        self.assertEqual([record['session_id'] for record in records], ['first', 'second'])
        self.assertEqual(records[0]['observation'], self.result['observation'])
        self.assertEqual(records[1]['frame_id'], 2)
        self.assertTrue(records[0]['captured_at_utc'].endswith('+00:00'))

    def test_log_allowlist_excludes_images_requests_and_private_usage_details(self):
        self.append(AnalysisLog(self.path))
        text = self.path.read_text()
        self.assertNotIn('must-not-be-saved', text)
        record = json.loads(text)
        self.assertEqual(record['usage'], {'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30})
        self.assertEqual(record['request_latency_ms'], 900)
        self.assertFalse(record['stale_at_completion'])

    def test_private_file_and_stale_suppressed_analysis_metadata(self):
        self.append(AnalysisLog(self.path), active=False, result={**self.result, 'frame_age_seconds': 4.0})
        record = json.loads(self.path.read_text())
        self.assertTrue(record['stale_at_completion'])
        self.assertFalse(record['active_at_completion'])
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def session(self):
        session = webcam.Session('/unused/brev', AnalysisLog(self.path))
        session.model = 'test-model'
        session.pending = webcam.Frame('image-memory-only', time.time(), 1, 0)
        return session

    def test_completed_model_result_is_persisted_before_publish(self):
        session = self.session()
        def end_wait():
            session.cancelled.set()
        response = {'model': 'test-model', 'observation': 'Synthetic chair',
                    'request_latency_ms': 10, 'usage': {'total_tokens': 10}}
        with patch.object(webcam, 'interpret_image', return_value=response), patch.object(session.condition, 'wait', side_effect=end_wait), patch.object(session, 'command') as command:
            session.process()
        command.assert_not_called()
        self.assertEqual(session.saved_analyses, 1)
        self.assertTrue(session.result['persisted'])
        record = json.loads(self.path.read_text())
        self.assertEqual(record['observation'], 'Synthetic chair')
        self.assertEqual(record['session_id'], session.session_id)
        self.assertEqual(record['frame_id'], 1)
        self.assertTrue(record['active_at_completion'])

    def test_response_suppressed_by_pause_is_still_saved(self):
        session = self.session()
        def infer(*args):
            session.pause()
            session.cancelled.set()
            return {'model': 'test-model', 'observation': 'Suppressed synthetic response'}
        with patch.object(webcam, 'interpret_image', side_effect=infer):
            session.process()
        self.assertIsNone(session.result)
        self.assertEqual(session.saved_analyses, 1)
        self.assertFalse(json.loads(self.path.read_text())['active_at_completion'])

    def test_log_failure_is_reported_and_requests_gpu_shutdown(self):
        session = self.session()
        with patch.object(webcam, 'interpret_image', return_value={'observation': 'Unsaved synthetic response'}), patch.object(session.analysis_log, 'append_result', side_effect=OSError('disk full')), patch.object(session, 'stop') as stop:
            session.process()
        stop.assert_called_once()
        self.assertIn('Could not save frame analysis', session.error)
        self.assertIn('disk full', session.snapshot()['analysis_log']['error'])
        self.assertEqual(session.saved_analyses, 0)
        self.assertFalse(session.result['persisted'])
        self.assertEqual(self.path.read_text(), '')


if __name__ == '__main__':
    unittest.main()
