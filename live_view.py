"""Token-protected LAN viewer for the latest MonitorEye answer."""
import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import socket
import threading
from urllib.parse import parse_qs, urlsplit
from prompt_presets import default_selection, validate_selection
from project_session import ProjectSession, ProjectBusy
from copy import deepcopy


class LiveView:
    def __init__(self, port=8765, host="0.0.0.0"):
        self.token = secrets.token_urlsafe(24)
        self.condition = threading.Condition()
        self.version = 0
        self.state = {"status": "Ready — press F1 on your Mac", "text": "", "timing": "", "used_prompt": "", "selection": default_selection(),
                      "capture_target": "snapshot", "capture_notice": ""}
        self.project = ProjectSession(lambda state: self.update(project=state))
        self.state["project"] = deepcopy(self.project.state)
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

    def prompt_snapshot(self):
        with self.condition:
            return dict(self.state["selection"])

    def select_prompt(self, value):
        selection = validate_selection(value)
        self.update(selection=selection)
        return selection

    def select_capture_target(self, target):
        if target not in ('snapshot', 'project'):
            raise ValueError('Choose Snapshot or Project session for F1.')
        self.update(capture_target=target, capture_notice='')
        return {'capture_target':target}

    def capture_destination(self):
        # Freeze the destination and session identity at the physical key press.
        with self.condition:
            return self.state['project']['session_id'] if self.state['capture_target'] == 'project' else None

    def start(self):
        view = self
        page = Path(__file__).with_name("live_view.html").read_bytes()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # Do not log the access token.

            def do_POST(self):
                route = urlsplit(self.path).path
                if route not in ("/prompt", "/capture-target", "/project/start", "/project/message", "/project/stop"):
                    self.send_error(404)
                    return
                token = self.headers.get("X-MonitorEye-Token", "")
                if not hmac.compare_digest(token.encode(), view.token.encode()):
                    self.send_error(403)
                    return
                if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                    self.send_error(415)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 65536:
                        raise ValueError("Invalid request size.")
                    self.connection.settimeout(5)
                    value = json.loads(self.rfile.read(length))
                    if not isinstance(value, dict):
                        raise ValueError("Expected a JSON object.")
                    if route == "/prompt":
                        result = view.select_prompt(value)
                    elif route == "/capture-target":
                        result = view.select_capture_target(value.get('target'))
                    elif route == "/project/start":
                        result = view.project.start(value.get("repo"), value.get("model", "sonnet"), value.get('source', 'screenshots'))
                        view.select_capture_target('project')
                    elif route == "/project/message":
                        result = view.project.send(value.get("text"), value.get("request_id"), value.get("session_id"))
                    else:
                        result = view.project.stop()
                    body = json.dumps(result).encode()
                except (ValueError, UnicodeError, ProjectBusy) as exc:
                    body = json.dumps({"error":str(exc)}).encode()
                    self.send_response(409 if isinstance(exc, ProjectBusy) else 400)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                except OSError:
                    self.send_error(408)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

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
        self.project.close()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
