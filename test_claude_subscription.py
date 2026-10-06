import base64
import json
import os
import subprocess
import unittest
from unittest.mock import patch

from claude_subscription import ClaudeSubscription


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        with patch('claude_subscription.shutil.which', return_value='/bin/claude'):
            self.client = ClaudeSubscription()

    def result(self, data, code=0):
        if isinstance(data, dict) and "loggedIn" not in data:
            data = {"type": "result", **data}
        return subprocess.CompletedProcess([], code, json.dumps(data), '')

    def auth(self):
        return self.result({'loggedIn': True, 'authMethod': 'claude.ai', 'apiProvider': 'firstParty'})

    @patch('claude_subscription.subprocess.run')
    def test_image_ocr_and_auth_isolation(self, run):
        response = self.result({'result': 'answer', 'is_error': False})
        response.stdout = '{"type":"system","subtype":"init"}\n' + response.stdout + '\n'
        run.side_effect = [self.auth(), response]
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'secret', 'ANTHROPIC_BASE_URL': 'gateway',
                                    'CLAUDE_CODE_USE_BEDROCK': '1', 'CLAUDE_CODE_OAUTH_TOKEN': 'token'}):
            self.assertEqual(self.client.analyze(b'jpeg', 'OCR text', 'system'), 'answer')
        call = run.call_args
        cmd = call.args[0]
        message = json.loads(call.kwargs['input'])['message']
        self.assertEqual(base64.b64decode(message['content'][0]['source']['data']), b'jpeg')
        self.assertEqual(message['content'][1]['text'], 'OCR text')
        self.assertEqual(cmd[cmd.index('--tools') + 1], '')
        self.assertIn('--no-session-persistence', cmd)
        self.assertEqual(cmd[cmd.index('--output-format') + 1], 'stream-json')
        self.assertIn('--verbose', cmd)
        self.assertNotIn('--bare', cmd)
        self.assertFalse(any(k.startswith('ANTHROPIC_') for k in call.kwargs['env']))
        self.assertNotIn('CLAUDE_CODE_USE_BEDROCK', call.kwargs['env'])
        self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN', call.kwargs['env'])
        self.assertFalse(os.path.exists(call.kwargs['cwd']))

    @patch('claude_subscription.subprocess.run')
    def test_auth_failure_never_sends_image(self, run):
        for status in ({'loggedIn': False}, {'loggedIn': True, 'authMethod': 'api_key'},
                       {'loggedIn': True, 'authMethod': 'claude.ai', 'apiProvider': 'bedrock'}):
            run.reset_mock()
            run.return_value = self.result(status)
            with self.assertRaises(RuntimeError):
                self.client.analyze(b'jpeg', 'text', 'system')
            self.assertEqual(run.call_count, 1)

    @patch('claude_subscription.subprocess.run')
    def test_failures(self, run):
        for response in (self.result({'is_error': True, 'result': 'Rate limit reached'}),
                         self.result({'errors': ['Authentication failed']}, 1),
                         self.result({'result': ''}), self.result([]),
                         subprocess.CompletedProcess([], 0, 'not json', '')):
            run.side_effect = [self.auth(), response]
            with self.assertRaises(RuntimeError):
                self.client.analyze(b'jpeg', 'text', 'system')

    @patch('claude_subscription.subprocess.run')
    def test_timeout(self, run):
        run.side_effect = [self.auth(), subprocess.TimeoutExpired('claude', 120)]
        with self.assertRaisesRegex(RuntimeError, 'timed out'):
            self.client.analyze(b'jpeg', 'text', 'system')

    def test_invalid_timeout(self):
        with patch('claude_subscription.shutil.which', return_value='/bin/claude'):
            with self.assertRaises(ValueError):
                ClaudeSubscription(timeout=0)


if __name__ == '__main__':
    unittest.main()
