"""Claude Code subscription transport; no Anthropic API key is used."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import queue
import threading
import time


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

    @staticmethod
    def _environment():
        # Do not inherit API keys, gateways, provider overrides or injected tokens.
        # Use the user's ordinary Claude Code login from its credential store.
        return {key: value for key, value in os.environ.items()
                if not key.startswith(("ANTHROPIC_", "CLAUDE_CODE_USE_"))
                and key not in ("CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_SIMPLE")}

    def _run(self, args, payload=None, timeout=None, on_event=None):
        # Avoid loading project context. Safe mode preserves subscription auth;
        # --bare would disable it. No settings-based API key helpers are loaded.
        with tempfile.TemporaryDirectory(prefix="monitoreye-claude-") as cwd:
            try:
                if on_event is not None:
                    return self._stream(args, payload, cwd, on_event)
                return subprocess.run(
                    [self.binary, "--safe-mode", "--setting-sources", "", *args],
                    input=payload, capture_output=True, text=True,
                    cwd=cwd, env=self._environment(),
                    timeout=timeout if timeout is not None else self.timeout,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("Claude Code timed out. Try again or increase CLAUDE_TIMEOUT.") from exc
            except OSError as exc:
                raise RuntimeError(f"Could not start Claude Code: {exc}") from exc

    def _stream(self, args, payload, cwd, on_event):
        command = [self.binary, "--safe-mode", "--setting-sources", "", *args]
        # Drain stdout while writing the image so neither pipe can deadlock.
        with tempfile.TemporaryFile(mode="w+") as stderr:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=stderr, text=True, cwd=cwd, env=self._environment())
            events = queue.Queue()

            def read_output():
                try:
                    for line in process.stdout:
                        events.put(line)
                finally:
                    events.put(None)

            def write_input():
                try:
                    process.stdin.write(payload)
                    process.stdin.close()
                except (BrokenPipeError, OSError, ValueError):
                    pass

            reader = threading.Thread(target=read_output, daemon=True)
            writer = threading.Thread(target=write_input, daemon=True)
            reader.start()
            writer.start()
            deadline = time.monotonic() + self.timeout
            lines = []
            try:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, self.timeout)
                    try:
                        line = events.get(timeout=remaining)
                    except queue.Empty:
                        raise subprocess.TimeoutExpired(command, self.timeout)
                    if line is None:
                        break
                    lines.append(line)
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    on_event(event)
                process.wait(timeout=max(0.01, deadline - time.monotonic()))
                stderr.seek(0)
                return subprocess.CompletedProcess(command, process.returncode, "".join(lines), stderr.read())
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()
                writer.join(timeout=1)
                reader.join(timeout=1)
                process.stdout.close()
                process.stdin.close()

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
        # Recheck in case the user changed accounts since startup.
        self.check_auth()
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

        process = self._run([
            "--print", "--input-format", "stream-json", "--output-format", "stream-json",
            "--verbose", "--include-partial-messages",
            "--model", model or self.model, "--effort", chosen_effort,
            "--system-prompt", system_prompt,
            "--tools", "", "--strict-mcp-config", "--disable-slash-commands",
            "--no-session-persistence",
        ], json.dumps(message) + "\n", on_event=receive if on_text else None)
        try:
            events = [json.loads(line) for line in process.stdout.splitlines() if line.strip()]
            response = next((event for event in reversed(events)
                             if isinstance(event, dict) and event.get("type") == "result"), None)
        except ValueError as exc:
            raise RuntimeError("Claude Code returned an invalid response. Check 'claude --version' and your login.") from exc
        if not isinstance(response, dict):
            raise RuntimeError("Claude Code returned no final result. Check your login and update Claude Code.")
        if process.returncode or response.get("is_error"):
            detail = response.get("result") or "; ".join(str(e) for e in response.get("errors", []))
            raise RuntimeError(f"Claude Code request failed: {detail or 'check login and subscription usage limits'}")
        answer = response.get("result")
        if not isinstance(answer, str) or not answer.strip():
            raise RuntimeError("Claude Code returned no answer.")
        return answer.strip()
