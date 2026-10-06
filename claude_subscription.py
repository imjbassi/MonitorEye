"""Claude Code subscription transport; no Anthropic API key is used."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from claude_session import ClaudeSession


class ClaudeSubscription:
    def __init__(self, model="claude-sonnet-5-5", timeout=120, effort="medium"):
        requested = os.getenv("CLAUDE_BIN", "claude")
        self.binary = shutil.which(requested)
        if not self.binary and requested == "claude":
            # launchd normally has a smaller PATH than an interactive terminal.
            for path in (Path.home() / ".local/bin/claude",
                         Path("/opt/homebrew/bin/claude"), Path("/usr/local/bin/claude")):
                if path.is_file() and os.access(path, os.X_OK):
                    self.binary = str(path)
                    break
        if not self.binary:
            raise RuntimeError("Claude Code not found. Install it or set CLAUDE_BIN to its full path.")
        if timeout <= 0:
            raise ValueError("CLAUDE_TIMEOUT must be greater than zero.")
        if effort not in ("low", "medium", "high"):
            raise ValueError("Effort must be low, medium, or high.")
        self.effort = effort
        self.model = model
        self.timeout = timeout
        self.sessions = {}
        self.lock = threading.RLock()
        self.closed = False

    @staticmethod
    def _environment():
        # Do not inherit API keys, gateways, provider overrides or injected tokens.
        # Use the user's ordinary Claude Code login from its credential store.
        return {key: value for key, value in os.environ.items()
                if not key.startswith(("ANTHROPIC_", "CLAUDE_CODE_USE_"))
                and key not in ("CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_SIMPLE")}

    def _run(self, args, timeout=20):
        with tempfile.TemporaryDirectory(prefix="monitoreye-auth-") as cwd:
            try:
                return subprocess.run(
                    [self.binary, "--safe-mode", "--setting-sources", "", *args],
                    capture_output=True, text=True, cwd=cwd, env=self._environment(), timeout=timeout)
            except (subprocess.TimeoutExpired, OSError) as exc:
                raise RuntimeError(f"Claude Code login check failed: {exc}") from exc

    def warm(self, system_prompt, model=None, effort=None):
        """Start a worker without sending an image or making a model request."""
        with self.lock:
            if self.closed:
                raise RuntimeError("Claude connection is closed.")
            model, effort = model or self.model, effort or self.effort
            if effort not in ("low", "medium", "high"):
                raise ValueError("Effort must be low, medium, or high.")
            key = (model, effort, system_prompt)
            session = self.sessions.get(key)
            if session and session.alive:
                return session
            if session:
                session.close()
                del self.sessions[key]
            self.check_auth()  # Only when creating/reconnecting a worker.
            if self.closed:
                raise RuntimeError("Claude connection is closed.")
            if len(self.sessions) >= 2:
                self.sessions.pop(next(iter(self.sessions))).close()
            args = [self.binary, "--safe-mode", "--setting-sources", "",
                    "--print", "--input-format", "stream-json", "--output-format", "stream-json",
                    "--verbose", "--include-partial-messages", "--model", model, "--effort", effort,
                    "--system-prompt", system_prompt, "--tools", "", "--strict-mcp-config",
                    "--no-session-persistence"]
            # Safe mode disables customizations; built-in /clear must remain enabled.
            session = ClaudeSession(args, self._environment(), self.timeout)
            self.sessions[key] = session
            if self.closed:
                session.close()
                raise RuntimeError("Claude connection is closed.")
            return session

    def reset(self):
        """Clear both conversations while retaining healthy worker processes."""
        with self.lock:
            for key, session in list(self.sessions.items()):
                try:
                    if not session.alive:
                        raise RuntimeError("Worker exited")
                    session.clear()
                except RuntimeError:
                    session.close()
                    del self.sessions[key]

    def close(self):
        # Stop workers even when a request holds the client lock.
        self.closed = True
        for session in list(self.sessions.values()):
            session.close()

    def check_auth(self):
        process = self._run(["auth", "status"], timeout=20)
        try:
            status = json.loads(process.stdout)
        except ValueError as exc:
            raise RuntimeError("Cannot read Claude Code login status. Update Claude Code and run 'claude auth login'.") from exc
        if not isinstance(status, dict) or not status.get("loggedIn"):
            raise RuntimeError("Sign in with your Claude subscription first: claude auth login")
        if process.returncode or status.get("authMethod") != "claude.ai" or status.get("apiProvider") != "firstParty":
            raise RuntimeError("Claude Code must use a Claude account login, not API/Console billing. Run 'claude auth login'.")

    def analyze(self, jpeg_bytes, prompt, system_prompt, on_text=None, model=None, effort=None):
        chosen_effort = effort or self.effort
        if chosen_effort not in ("low", "medium", "high"):
            raise ValueError("Effort must be low, medium, or high.")
        message = {
            "type": "user",
            "message": {"role": "user", "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/jpeg",
                    "data": base64.standard_b64encode(jpeg_bytes).decode("ascii")}},
                {"type": "text", "text": prompt},
            ]},
        }
        def receive(event):
            if not isinstance(event, dict) or event.get("type") != "stream_event":
                return
            delta = event.get("event", {}).get("delta", {})
            if delta.get("type") == "text_delta" and on_text:
                on_text(delta.get("text", ""))

        with self.lock:
            session = self.warm(system_prompt, model=model, effort=chosen_effort)
            response = session.request(message, receive)
        if response.get("is_error"):
            detail = response.get("result") or "; ".join(str(e) for e in response.get("errors", []))
            raise RuntimeError(f"Claude Code request failed: {detail or 'check login and subscription usage limits'}")
        answer = response.get("result")
        if not isinstance(answer, str) or not answer.strip():
            raise RuntimeError("Claude Code returned no answer.")
        return answer.strip()
