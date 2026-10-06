"""Read-only, token-protected LAN viewer for the latest MonitorEye answer."""
import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import socket
import threading
from urllib.parse import parse_qs, urlsplit


class LiveView:
    def __init__(self, port=8765, host="0.0.0.0"):
        self.token = secrets.token_urlsafe(24)
        self.condition = threading.Condition()
        self.version = 0
        self.state = {"status": "Ready — press F1 on your Mac", "text": "", "timing": ""}
        self.server = None
        self.port, self.host = port, host

    def update(self, **values):
        with self.condition:
            self.state.update(values)
            self.version += 1
            self.condition.notify_all()

    def append(self, text):
        with self.condition:
            self.state["text"] += text
            self.state["status"] = "Writing…"
            self.version += 1
            self.condition.notify_all()

    def start(self):
        view = self
        page = Path(__file__).with_name("live_view.html").read_bytes()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # Do not log the access token.

            def do_GET(self):
                url = urlsplit(self.path)
                token = parse_qs(url.query).get("token", [""])[0]
                if not hmac.compare_digest(token.encode(), view.token.encode()):
                    self.send_error(403, "Open the private viewer link printed by MonitorEye.")
                    return
                if url.path not in ("/", "/events"):
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Type", "text/html; charset=utf-8" if url.path == "/" else "text/event-stream")
                if url.path == "/":
                    self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                try:
                    if url.path == "/":
                        self.wfile.write(page)
                        return
                    version = -1
                    while True:
                        with view.condition:
                            view.condition.wait_for(lambda: view.version != version, timeout=15)
                            if version != view.version:
                                version = view.version
                                data = "data: " + json.dumps(view.state) + "\n\n"
                            else:
                                data = ": keepalive\n\n"
                        self.wfile.write(data.encode())
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return

        self.server = ThreadingHTTPServer((self.host, self.port), Handler)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def url(self, host="127.0.0.1"):
        return f"http://{host}:{self.port}/?token={self.token}"

    def phone_url(self):
        # UDP connect determines the LAN interface without sending a packet.
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("192.0.2.1", 80))
                host = sock.getsockname()[0]
        except OSError:
            host = socket.gethostname()
        return self.url(host)

    def close(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
