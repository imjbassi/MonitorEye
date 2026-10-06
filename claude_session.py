"""A serialized stream-json connection to a long-lived Claude Code process."""
import json
import queue
import subprocess
import tempfile
import threading
import time


class ClaudeSession:
    def __init__(self, command, env, timeout, cwd=None):
        self.timeout = timeout
        self.events = queue.Queue()
        self.used = False
        self.closed = False
        self.lock = threading.Lock()
        self.close_lock = threading.Lock()
        self.directory = tempfile.TemporaryDirectory(prefix="monitoreye-claude-") if cwd is None else None
        self.stderr = tempfile.TemporaryFile(mode="w+")
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=self.stderr, text=True, cwd=self.directory.name if self.directory else cwd, env=env)
        except Exception:
            self.stderr.close()
            if self.directory:
                self.directory.cleanup()
            raise
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                self.events.put(event)
        finally:
            self.events.put(None)

    @property
    def alive(self):
        return not self.closed and self.process.poll() is None

    def _exchange(self, message, callback=None):
        deadline = time.monotonic() + self.timeout
        # A large image can fill stdin; keep draining stdout concurrently.
        def write():
            try:
                self.process.stdin.write(json.dumps(message) + "\n")
                self.process.stdin.flush()
            except (OSError, ValueError):
                self.events.put(None)
        writer = threading.Thread(target=write, daemon=True)
        writer.start()
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("Claude Code timed out. Try again or increase CLAUDE_TIMEOUT.")
                try:
                    event = self.events.get(timeout=remaining)
                except queue.Empty:
                    raise RuntimeError("Claude Code timed out. Try again or increase CLAUDE_TIMEOUT.")
                if event is None:
                    raise RuntimeError("Claude Code disconnected. Press F1 or F3 to reconnect and retry.")
                if not isinstance(event, dict):
                    continue
                if event.get("type") == "result":
                    return event
                if callback:
                    callback(event)
        finally:
            # Kill before joining a blocked stdin writer on timeout/error.
            if writer.is_alive() and time.monotonic() >= deadline:
                self.close()
            writer.join(timeout=1)

    def request(self, message, callback=None, clear_before=True):
        with self.lock:
            try:
                if not self.alive:
                    raise RuntimeError("Claude Code disconnected. Retry to reconnect.")
                if clear_before:
                    self._clear()
                result = self._exchange(message, callback)
                self.used = True
                if result.get("is_error"):
                    self.close()
                return result
            except Exception:
                self.close()
                raise

    def _clear(self):
        if not self.used:
            return
        reset = self._exchange({"type": "user", "message": {"role": "user", "content": "/clear"}})
        # Fail closed if /clear becomes a model prompt or isn't supported.
        if reset.get("local_command") != "clear" or reset.get("is_error"):
            raise RuntimeError("Claude Code could not clear the previous capture. Update Claude Code and retry.")
        self.used = False

    def clear(self):
        with self.lock:
            try:
                self._clear()
            except Exception:
                self.close()
                raise

    def close(self):
        with self.close_lock:
            self._close()

    def _close(self):
        if self.closed:
            return
        self.closed = True
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process.wait()
        self.reader.join(timeout=1)
        self.process.stdout.close()
        self.process.stdin.close()
        self.stderr.close()
        if self.directory:
            self.directory.cleanup()
