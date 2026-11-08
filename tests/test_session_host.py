"""Session rollover and stale-page isolation, without camera or cloud calls."""
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import webcam_reasoner as webcam
from session_recording import SessionRecording


class SessionHostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        def factory():
            return webcam.Session('/unused/brev', SessionRecording(self.root))
        self.factory = factory
        self.previous = factory()
        self.host = webcam.SessionHost(self.previous, factory)

    def finish(self):
        self.previous.cancelled.set()
        self.previous.finished = True
        self.previous.stage = 'stopped'

    def test_rollover_allocates_next_recording_without_starting_compute(self):
        self.finish()
        with patch.object(webcam.Session, 'command') as command:
            state = self.host.new_session()
        command.assert_not_called()
        self.assertEqual(state['stage'], 'idle')
        self.assertEqual(state['session_id'], 'session_000002')
        self.assertFalse(state['can_start_new_session'])
        self.assertIsNone(self.host.current.deadline)
        self.assertTrue(self.previous.analysis_log.path.exists())
        with patch.object(self.previous, 'stop') as old_stop, patch.object(self.host.current, 'stop') as current_stop:
            self.host.stop()
        old_stop.assert_not_called()
        current_stop.assert_called_once()

    def test_active_stopping_and_inflight_sessions_cannot_be_replaced(self):
        for stage in ('idle', 'analyzing', 'stopping', 'stop-error'):
            self.previous.stage = stage
            with self.assertRaises(ValueError):
                self.host.new_session()
            self.assertIs(self.host.current, self.previous)
        self.finish()
        self.previous.worker_running = True
        self.assertFalse(self.host.snapshot()['can_start_new_session'])
        with self.assertRaises(ValueError):
            self.host.new_session()

    def test_recording_failure_preserves_current_session(self):
        self.finish()
        with patch.object(self.host, 'factory', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.host.new_session()
        self.assertIs(self.host.current, self.previous)

    def test_http_rollover_rejects_old_pages_and_accepts_new_frame(self):
        from test_webcam_reasoner import FRAME
        self.finish()
        server = webcam.ThreadingHTTPServer(('127.0.0.1', 0), webcam.make_handler(self.host, 'test-token'))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        def post(path, session_id, **payload):
            request = Request(base + path, json.dumps({'session_id': session_id, **payload}).encode(),
                              headers={'Origin': base, 'X-Session-Token': 'test-token'})
            with urlopen(request) as response:
                return json.load(response)
        try:
            initial_id = self.previous.session_id
            state = post('/new-session', initial_id)
            self.assertEqual(state['session_id'], 'session_000002')
            for path in ('/analyze', '/stop', '/pause', '/new-session'):
                with self.assertRaises(HTTPError) as caught:
                    post(path, initial_id, image=FRAME, captured_at=time.time())
                self.assertEqual(caught.exception.code, 409)
            with patch.object(webcam.Session, 'process'), patch.object(webcam.Session, 'watchdog'), patch.object(webcam.Session, 'command') as command:
                state = post('/analyze', state['session_id'], image=FRAME, captured_at=time.time())
            command.assert_not_called()
            self.assertEqual(state['frames']['accepted'], 1)
            self.assertIsNotNone(self.host.current.deadline)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
