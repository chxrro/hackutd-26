"""Receive one sender's latest JPEG and display it on this computer."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import socket
import threading
import time

STALE_AFTER = 2.0
MAX_JPEG = 512 * 1024
VIEWER_HTML = Path(__file__).with_suffix(".html")


class FrameServer(ThreadingHTTPServer):
    def __init__(self, address):
        super().__init__(address, FrameHandler)
        self.token = secrets.token_hex(8)
        self.frame = None
        self.frame_lock = threading.Lock()

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(3)
        return connection, address


class FrameHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def respond(self, status, body=b"", content_type="text/plain", **headers):
        self.send_response(status)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if status != 204:
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        port = self.server.server_port
        if self.client_address[0] != "127.0.0.1" or self.headers.get("Host") not in {
            f"127.0.0.1:{port}", f"localhost:{port}"
        }:
            self.respond(403)
            return
        if self.path == "/":
            self.respond(200, VIEWER_HTML.read_bytes(), "text/html; charset=utf-8")
            return
        if self.path != "/frame":
            self.respond(404)
            return

        with self.server.frame_lock:
            frame = self.server.frame
            if frame and time.monotonic() - frame[0] >= STALE_AFTER:
                frame = self.server.frame = (frame[0], frame[1], None)
        if not frame:
            self.respond(204, **{"X-Frame-State": "waiting"})
        elif frame[2] is None:
            self.respond(204, **{"X-Frame-State": "stale"})
        else:
            self.respond(200, frame[2], "image/jpeg", **{"X-Frame-Source": frame[1]})

    def do_POST(self):
        if self.path != "/frame":
            self.respond(404)
            return
        expected = f"Bearer {self.server.token}".encode()
        authorization = self.headers.get("Authorization", "").encode("latin-1")
        if not secrets.compare_digest(authorization, expected):
            self.respond(401)
            return

        source = self.headers.get("X-Frame-Source")
        if self.headers.get_content_type() != "image/jpeg" or source not in ("test", "glasses"):
            self.respond(415)
            return

        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
            self.respond(411)
            return
        size = int(lengths[0]) if len(lengths[0]) <= 6 else MAX_JPEG + 1
        if size > MAX_JPEG:
            self.respond(413)
            return
        jpeg = self.rfile.read(size)
        if len(jpeg) != size or not (jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9")):
            self.respond(400)
            return

        with self.server.frame_lock:
            self.server.frame = (time.monotonic(), source, jpeg)
        self.respond(204)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen", default="127.0.0.1", choices=("127.0.0.1", "0.0.0.0"),
                        help="Use 0.0.0.0 to accept iPhone uploads on your local network.")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    with FrameServer((args.listen, args.port)) as server:
        print(f"Open on this computer: http://127.0.0.1:{server.server_port}", flush=True)
        print(f"iPhone pairing code: {server.token}", flush=True)
        if args.listen == "0.0.0.0":
            host = socket.gethostname()
            if host.endswith(".local"):
                print(f"Try on iPhone: http://{host}:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
