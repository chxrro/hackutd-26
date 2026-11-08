"""Camera session spending and request-boundary tests; never contact Brev."""
import base64
import json
import sys
import time
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import webcam_reasoner as webcam

# Header-only fixture is deliberately synthetic; this test does not validate JPEG decoding.
FRAME = 'data:image/jpeg;base64,' + base64.b64encode(b'\xff\xd8\xff' + b'x' * 128).decode()


class SessionTests(unittest.TestCase):
    def test_preview_does_not_start_or_bill_gpu(self):
        session = webcam.Session('/unused/brev')
        with patch.object(session, 'command') as command:
            self.assertEqual(session.snapshot()['stage'], 'idle')
            self.assertIsNone(session.deadline)
            session.stop()
            command.assert_not_called()

    def test_watchdog_stops_expired_session(self):
        session = webcam.Session('/unused/brev')
        session.deadline = 0
        with patch.object(session.cancelled, 'wait', return_value=False), patch.object(session, 'stop') as stop:
            session.watchdog()
            stop.assert_called_once()

    def test_stop_confirms_instance_already_stopped_by_vm_timer(self):
        session = webcam.Session('/unused/brev')
        session.started = 1
        with patch.object(session, 'command', side_effect=[RuntimeError('Already stopped'), 'hackutd-reasoner STOPPED']):
            session.stop()
        self.assertTrue(session.finished)
        self.assertEqual(session.snapshot()['stage'], 'stopped')
        with self.assertRaisesRegex(ValueError, 'session has ended'):
            session.submit(FRAME)

    def test_model_loading_requires_remote_shutdown_guard(self):
        session = webcam.Session('/unused/brev')
        session.deadline = 100
        def command(*args, **kwargs):
            if args[0] == 'exec':
                raise RuntimeError('Cannot install shutdown timer')
            return ''
        with patch.object(session, 'command', side_effect=command) as commands, patch.object(webcam.time, 'time', side_effect=[0, 0, 200]), patch.object(session.cancelled, 'wait', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'shutdown timer'):
                session.prepare()
        self.assertFalse(session.ssh_guard)
        self.assertFalse(any('start.sh' in str(call) for call in commands.call_args_list))

    def test_prepare_reuses_an_already_running_or_starting_instance(self):
        for status in ('RUNNING', 'STARTING'):
            session = webcam.Session('/unused/brev')
            session.deadline = time.time() + 100
            def command(*args, **kwargs):
                if args[0] == 'ls':
                    return f'hackutd-reasoner {status}'
                return ''
            with patch.object(session, 'command', side_effect=command) as commands, patch.object(session, 'start_tunnel'), patch.object(webcam, 'request_json', return_value={'data': [{'id': 'cosmos3-edge'}]}):
                session.prepare()
            self.assertEqual(session.model, 'cosmos3-edge')
            self.assertFalse(any(call.args[0] == 'start' for call in commands.call_args_list))

    def test_resource_exhaustion_retries_the_operation_without_restarting_instance(self):
        session = webcam.Session('/unused/brev')
        from subprocess import CompletedProcess
        results = [CompletedProcess([], 1, '', 'resource exhausted'), CompletedProcess([], 0, 'ok', '')]
        with patch.object(webcam.subprocess, 'run', side_effect=results) as run, patch.object(webcam.time, 'sleep'):
            self.assertEqual(session.command('exec', webcam.INSTANCE, 'true'), 'ok')
        self.assertEqual(run.call_count, 2)
        self.assertTrue(all(call.args[0][1] == 'exec' for call in run.call_args_list))

    def test_startup_uses_private_dedicated_tunnel(self):
        session = webcam.Session('/unused/brev')
        with patch.object(webcam.subprocess, 'Popen') as popen:
            session.start_tunnel()
        args = popen.call_args.args[0]
        self.assertIn('ControlPath=none', args)
        self.assertIn('127.0.0.1:8001:127.0.0.1:8000', args)

    def test_transient_inference_failure_continues_with_the_latest_frame(self):
        session = webcam.Session('/unused/brev')
        session.model = 'cosmos3-edge'
        session.pending = webcam.Frame(FRAME, time.time(), 1, 0)
        session.worker_running = True
        calls = []
        def interpret(*args):
            calls.append(args)
            if len(calls) == 1:
                session.pending = webcam.Frame(FRAME, time.time(), 2, 0)
                raise webcam.URLError('temporary resource exhaustion')
            session.cancelled.set()
            return {'observation': 'Synthetic test obstacle', 'request_latency_ms': 1, 'model': session.model}
        with patch.object(webcam, 'interpret_image', side_effect=interpret), patch.object(session.cancelled, 'wait', return_value=False), patch.object(session, 'stop') as stop:
            session.process()
        self.assertEqual(len(calls), 2)
        self.assertEqual(session.completed, 1)
        self.assertEqual(session.inference_failures, 1)
        stop.assert_not_called()

    def test_prepare_waits_for_prior_shutdown_before_starting(self):
        session = webcam.Session('/unused/brev')
        session.deadline = time.time() + 300
        listings = iter(['hackutd-reasoner STOPPING', 'hackutd-reasoner STOPPED'])
        def command(*args, **kwargs):
            return next(listings) if args[0] == 'ls' else ''
        with patch.object(session, 'command', side_effect=command) as commands, patch.object(session.cancelled, 'wait', return_value=False), patch.object(session, 'start_tunnel'), patch.object(webcam, 'request_json', return_value={'data': [{'id': 'cosmos3-edge'}]}):
            session.prepare()
        operations = [call.args[0] for call in commands.call_args_list]
        self.assertEqual(operations[:3], ['ls', 'ls', 'start'])
        self.assertIn('--detached', commands.call_args_list[2].args)
        self.assertEqual(session.model, 'cosmos3-edge')

    def test_failed_inference_requests_shutdown(self):
        session = webcam.Session('/unused/brev')
        session.model = 'cosmos3-edge'
        with patch.object(webcam, 'interpret_image', side_effect=RuntimeError('Unavailable')), patch.object(session, 'stop') as stop:
            session.pending = webcam.Frame(FRAME, time.time(), 1, 0)
            session.process()
            stop.assert_called_once()
        self.assertFalse(session.busy)
        self.assertEqual(session.error, 'Unavailable')

    def test_busy_worker_keeps_only_latest_pending_frame(self):
        session = webcam.Session('/unused/brev')
        session.started = time.time()
        session.worker_running = True
        session.busy = True
        session.submit('frame-1')
        session.submit('frame-2')
        session.submit('frame-3')
        self.assertEqual(session.pending.image, 'frame-3')
        self.assertEqual(session.snapshot()['frames']['pending'], 1)
        self.assertEqual(session.replaced, 2)

    def test_out_of_order_upload_cannot_replace_a_newer_capture(self):
        session = webcam.Session('/unused/brev')
        session.started = time.time()
        session.worker_running = True
        captured = time.time()
        session.submit('newer', captured)
        with self.assertRaisesRegex(ValueError, 'older'):
            session.submit('older', captured - .1)
        self.assertEqual(session.pending.image, 'newer')
        self.assertEqual(session.out_of_order, 1)

    def test_stale_timestamp_is_rejected_before_gpu_start(self):
        session = webcam.Session('/unused/brev')
        for timestamp in [time.time() - 4, time.time() + 10, float('nan'), float('inf'), True]:
            with self.subTest(timestamp=timestamp), self.assertRaises(ValueError):
                session.submit(FRAME, timestamp)
        self.assertIsNone(session.started)

    def test_inference_is_sequential_and_skips_replaced_frames(self):
        session = webcam.Session('/unused/brev')
        session.started = time.time()
        session.worker_running = True
        session.model = 'cosmos3-edge'
        first_entered, release_first, third_entered = threading.Event(), threading.Event(), threading.Event()
        calls = []
        active = 0
        peak = 0
        def infer(base, model, image):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            calls.append(image)
            if image == 'first':
                first_entered.set()
                self.assertTrue(release_first.wait(3))
            else:
                third_entered.set()
            active -= 1
            return {'observation': image, 'request_latency_ms': 100}
        session.submit('first')
        with patch.object(webcam, 'interpret_image', side_effect=infer):
            worker = threading.Thread(target=session.process)
            worker.start()
            try:
                self.assertTrue(first_entered.wait(3))
                session.submit('second')
                session.submit('third')
                release_first.set()
                self.assertTrue(third_entered.wait(3))
            finally:
                with session.condition:
                    session.cancelled.set()
                    session.condition.notify_all()
                release_first.set()
                worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(calls, ['first', 'third'])
        self.assertEqual(peak, 1)
        self.assertEqual(session.completed, 2)

    def test_startup_uses_newest_frame_instead_of_initial_frame(self):
        session = webcam.Session('/unused/brev')
        session.started = time.time()
        session.worker_running = True
        session.submit('old')
        def prepare():
            session.model = 'cosmos3-edge'
            session.submit('new')
        def infer(*args):
            with session.condition:
                session.cancelled.set()
                session.condition.notify_all()
            return {'observation': 'new'}
        with patch.object(session, 'prepare', side_effect=prepare), patch.object(webcam, 'interpret_image', side_effect=infer) as request:
            session.process()
        self.assertEqual(request.call_args.args[2], 'new')

    def test_expired_pending_frame_does_not_reach_model(self):
        session = webcam.Session('/unused/brev')
        session.model = 'cosmos3-edge'
        session.pending = webcam.Frame(FRAME, time.time() - 10, 1, 0)
        def finish_wait():
            session.cancelled.set()
        with patch.object(session.condition, 'wait', side_effect=finish_wait), patch.object(webcam, 'interpret_image') as request:
            session.process()
        request.assert_not_called()
        self.assertEqual(session.expired, 1)

    def test_pause_discards_pending_and_late_uploads(self):
        session = webcam.Session('/unused/brev')
        session.started = time.time()
        session.worker_running = True
        old_time = time.time() - .1
        session.submit(FRAME, old_time)
        session.pause()
        self.assertIsNone(session.pending)
        self.assertTrue(session.paused)
        with self.assertRaisesRegex(ValueError, 'predates'):
            session.submit(FRAME, old_time)
        self.assertEqual(session.discarded_on_pause, 1)

    def test_pause_suppresses_inflight_observation(self):
        session = webcam.Session('/unused/brev')
        session.model = 'cosmos3-edge'
        session.pending = webcam.Frame(FRAME, time.time(), 1, 0)
        def infer(*args):
            session.pause()
            session.cancelled.set()
            return {'observation': 'Must not publish'}
        with patch.object(webcam, 'interpret_image', side_effect=infer):
            session.process()
        self.assertIsNone(session.result)
        self.assertEqual(session.completed, 1)

    def test_observation_becomes_stale_without_a_new_response(self):
        session = webcam.Session('/unused/brev')
        session.result = {'captured_at': time.time() - 4, 'observation': 'Old chair'}
        result = session.snapshot()
        self.assertEqual(result['freshness'], 'stale')
        self.assertTrue(result['result']['stale'])

    def test_processed_rate_counts_completed_requests(self):
        session = webcam.Session('/unused/brev')
        now = time.monotonic()
        session.completions.extend([now - 2.5, now - 1.25, now])
        self.assertEqual(session.snapshot()['processed_fps'], 0.8)



    def test_watchdog_retries_a_failed_stop_until_confirmed(self):
        session = webcam.Session('/unused/brev')
        session.started, session.deadline = 1, 10**12
        session.cancelled.set()
        session.stage = 'stop-error'
        def stop():
            session.finished = True
        with patch.object(session, 'stop', side_effect=stop) as stop_call:
            session.watchdog()
        stop_call.assert_called_once()

    def test_watchdog_waits_for_an_in_progress_stop_without_starting_another(self):
        session = webcam.Session('/unused/brev')
        session.started, session.deadline = 1, 10**12
        session.cancelled.set()
        session.stop_started, session.stage = True, 'stopping'
        def finish(_):
            session.finished = True
        with patch.object(session, 'stop') as stop, patch.object(webcam.time, 'sleep', side_effect=finish):
            session.watchdog()
        stop.assert_not_called()

    def test_stop_matches_the_exact_instance_name(self):
        session = webcam.Session('/unused/brev')
        session.started = 1
        listings = ['hackutd-reasoner-2 STOPPED\nhackutd-reasoner RUNNING', 'hackutd-reasoner STOPPED']
        responses = iter(['stopping', *listings])
        with patch.object(session, 'command', side_effect=lambda *a, **k: next(responses)) as command, patch.object(webcam.time, 'sleep'):
            session.stop()
        self.assertTrue(session.finished)
        self.assertEqual([c.args[0] for c in command.call_args_list], ['stop', 'ls', 'ls'])

    def test_stop_during_startup_never_leaves_a_tunnel_behind(self):
        session = webcam.Session('/unused/brev')
        session.deadline = time.time() + 300
        original_update = session.update
        def update(stage, status):
            original_update(stage, status)
            if stage == 'connecting':
                session.cancelled.set()  # Stop lands after model loading, before the tunnel.
        with patch.object(session, 'command', return_value='hackutd-reasoner RUNNING'), patch.object(session, 'update', side_effect=update), patch.object(session, 'start_tunnel') as tunnel:
            session.prepare()
        tunnel.assert_not_called()

    def test_non_json_reply_is_a_temporary_failure_not_a_shutdown(self):
        session = webcam.Session('/unused/brev')
        session.model = 'cosmos3-edge'
        session.pending = webcam.Frame(FRAME, time.time(), 1, 0)
        session.worker_running = True
        calls = []
        def interpret(*args):
            calls.append(args)
            if len(calls) == 1:
                session.pending = webcam.Frame(FRAME, time.time(), 2, 0)
                raise json.JSONDecodeError('Expecting value', '<html>', 0)
            session.cancelled.set()
            return {'observation': 'Synthetic test obstacle', 'request_latency_ms': 1, 'model': session.model}
        with patch.object(webcam, 'interpret_image', side_effect=interpret), patch.object(session.cancelled, 'wait', return_value=False), patch.object(session, 'stop') as stop:
            session.process()
        self.assertEqual(session.inference_failures, 1)
        stop.assert_not_called()

    def test_local_ports_avoid_the_hub(self):
        self.assertNotIn(webcam.PAGE_PORT, {8765, 8000})
        self.assertNotIn(webcam.TUNNEL_PORT, {8765, 8000})
        self.assertIn(f':{webcam.TUNNEL_PORT}/', webcam.BASE_URL)


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.session = webcam.Session('/unused/brev')
        self.server = webcam.ThreadingHTTPServer(('127.0.0.1', 0), webcam.make_handler(self.session, 'test-token'))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_camera_page_and_status_are_free_of_gpu_actions(self):
        with patch.object(self.session, 'command') as command:
            with urlopen(self.base) as response:
                self.assertIn(b'Enable MacBook camera', response.read())
            with urlopen(self.base + '/status') as response:
                self.assertEqual(json.load(response)['stage'], 'idle')
            command.assert_not_called()

    def test_browser_scheduler_is_served_without_starting_gpu(self):
        with patch.object(self.session, 'command') as command:
            with urlopen(self.base + '/webcam_reasoner.js') as response:
                self.assertIn('javascript', response.headers['Content-Type'])
                self.assertIn(b'LatestCaptureLoop', response.read())
            command.assert_not_called()

    def test_missing_capture_timestamp_is_rejected_before_start(self):
        request = Request(self.base + '/analyze', data=json.dumps({'image': FRAME}).encode(), headers={
            'Origin': self.base, 'X-Session-Token': 'test-token',
        })
        with self.assertRaises(HTTPError) as caught:
            urlopen(request)
        self.assertEqual(caught.exception.code, 400)
        self.assertIsNone(self.session.started)

    def test_pause_discards_pending_work_without_stopping_or_starting_gpu(self):
        self.session.pending = webcam.Frame(FRAME, time.time(), 1, 0)
        request = Request(self.base + '/pause', data=b'{}', headers={
            'Origin': self.base, 'X-Session-Token': 'test-token',
        })
        with patch.object(self.session, 'command') as command, urlopen(request) as response:
            self.assertEqual(response.status, 202)
            self.assertTrue(json.load(response)['paused'])
            command.assert_not_called()
        self.assertIsNone(self.session.pending)

    def test_camera_health_report_never_starts_gpu_or_records_images(self):
        request = Request(self.base + '/camera-status', data=json.dumps({
            'event': 'error', 'error_name': 'NotAllowedError', 'image': 'must not be stored',
        }).encode(), headers={'Origin': self.base, 'X-Session-Token': 'test-token'})
        with patch.object(self.session, 'command') as command, urlopen(request) as response:
            state = json.load(response)
            self.assertEqual(state['camera_client']['error_name'], 'NotAllowedError')
            self.assertNotIn('image', state['camera_client'])
            self.assertIsNone(self.session.started)
            command.assert_not_called()

    def test_cross_origin_start_is_rejected(self):
        request = Request(self.base + '/analyze', data=json.dumps({'image': FRAME, 'captured_at': time.time()}).encode(), headers={
            'Origin': 'https://example.invalid', 'X-Session-Token': 'test-token',
        })
        with self.assertRaises(HTTPError) as caught:
            urlopen(request)
        self.assertEqual(caught.exception.code, 403)
        self.assertIsNone(self.session.started)

    def test_invalid_frame_is_rejected_before_start(self):
        request = Request(self.base + '/analyze', data=b'{"image":"not a camera image"}', headers={
            'Origin': self.base, 'X-Session-Token': 'test-token',
        })
        with self.assertRaises(HTTPError) as caught:
            urlopen(request)
        self.assertEqual(caught.exception.code, 400)
        self.assertIsNone(self.session.started)

    def test_valid_frame_is_dispatched_without_persisting_it(self):
        with patch.object(self.session, 'submit') as submit:
            request = Request(self.base + '/analyze', data=json.dumps({'image': FRAME, 'captured_at': time.time()}).encode(), headers={
                'Origin': self.base, 'X-Session-Token': 'test-token',
            })
            with urlopen(request) as response:
                self.assertEqual(response.status, 202)
            submit.assert_called_once()
            self.assertEqual(submit.call_args.args[0], FRAME)
            self.assertLess(abs(submit.call_args.args[1] - time.time()), 2)


if __name__ == '__main__':
    unittest.main()
