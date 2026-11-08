"""Protocol checks use a local fake API; they do not run a vision model."""
import base64
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CLIENT = Path(__file__).resolve().parents[1] / 'scripts' / 'reasoner.py'
PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j6wAAAABJRU5ErkJggg=='
)


class ReasonerProtocolTests(unittest.TestCase):
    def setUp(self):
        self.finish_reason = 'stop'
        self.received = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send_json(self, body):
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

            def do_GET(self):
                if self.path == '/v1/models':
                    self.send_json({'data': [{'id': 'cosmos3-edge'}]})
                else:
                    self.send_error(404)

            def do_POST(self):
                owner.received = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                self.send_json({'choices': [{
                    'message': {'content': 'A chair is visible on the left.'},
                    'finish_reason': owner.finish_reason,
                }], 'usage': {'completion_tokens': 10}})

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.folder = tempfile.TemporaryDirectory()
        self.image = Path(self.folder.name) / 'test.png'
        self.image.write_bytes(PNG)
        self.base = f'http://127.0.0.1:{self.server.server_port}/v1'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.folder.cleanup()

    def run_client(self, *args):
        return subprocess.run(
            [sys.executable, str(CLIENT), '--base-url', self.base, *args],
            capture_output=True, text=True, timeout=10,
        )

    def test_model_discovery_and_image_request(self):
        result = self.run_client('image', str(self.image))
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output['model'], 'cosmos3-edge')
        self.assertIn('chair', output['observation'])
        content = self.received['messages'][0]['content']
        self.assertEqual(base64.b64decode(content[0]['image_url']['url'].split(',', 1)[1]), PNG)
        self.assertFalse(self.received['chat_template_kwargs']['enable_thinking'])

    def test_truncated_observation_is_rejected(self):
        self.finish_reason = 'length'
        result = self.run_client('image', str(self.image))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertIn('Incomplete response', result.stderr)

    def test_nonlocal_plaintext_endpoint_is_rejected(self):
        result = subprocess.run(
            [sys.executable, str(CLIENT), '--base-url', 'http://example.invalid/v1', 'health'],
            capture_output=True, text=True, timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('SSH tunnel or HTTPS', result.stderr)
        self.assertIsNone(self.received)


if __name__ == '__main__':
    unittest.main()
