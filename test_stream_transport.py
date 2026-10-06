import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from claude_subscription import ClaudeSubscription


class StreamTransportTests(unittest.TestCase):
    def run_fake(self, script, timeout, callback):
        # Invoke a real child process, replacing only the Claude command prefix.
        real_popen = subprocess.Popen
        with tempfile.TemporaryDirectory() as cwd:
            path = Path(cwd) / 'fake.py'
            path.write_text(script)
            with patch('claude_subscription.shutil.which', return_value=sys.executable):
                client = ClaudeSubscription(timeout=timeout)
            def spawn(command, **kwargs):
                return real_popen([sys.executable, '-u', str(path)], **kwargs)
            with patch('claude_subscription.subprocess.Popen', side_effect=spawn):
                return client._run([], '{}\n', on_event=callback)

    def test_delivers_before_process_finishes(self):
        received = []
        started = time.monotonic()
        result = self.run_fake('''import sys, time
sys.stdin.read()
print('{"type":"stream_event","event":{"delta":{"type":"text_delta","text":"hi"}}}', flush=True)
time.sleep(0.5)
print('{"type":"result","result":"hi"}', flush=True)
''', 5, lambda event: received.append((time.monotonic(), event)))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(received), 2)
        self.assertGreater(received[1][0] - received[0][0], 0.3)

    def test_stalled_child_times_out(self):
        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, 'timed out'):
            self.run_fake('import time; time.sleep(10)', 0.15, lambda event: None)
        self.assertLess(time.monotonic()-started, 3)


if __name__ == '__main__':
    unittest.main()
