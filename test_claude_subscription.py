import base64
import json
import os
import subprocess
import unittest
from unittest.mock import patch, Mock
from claude_subscription import ClaudeSubscription


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.which = patch('claude_subscription.shutil.which', return_value='/bin/claude')
        self.which.start()
        self.client = ClaudeSubscription()

    def tearDown(self):
        self.client.close()
        self.which.stop()

    def auth(self, **overrides):
        status = dict(loggedIn=True, authMethod='claude.ai', apiProvider='firstParty')
        status.update(overrides)
        return subprocess.CompletedProcess([], 0, json.dumps(status), '')

    @patch('claude_subscription.ClaudeSession')
    @patch('claude_subscription.subprocess.run')
    def test_image_ocr_auth_isolation_and_reuse(self, run, factory):
        run.return_value = self.auth()
        worker = factory.return_value
        worker.alive = True
        worker.request.return_value = {'result':'answer'}
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY':'secret', 'ANTHROPIC_BASE_URL':'gateway',
                                    'CLAUDE_CODE_USE_BEDROCK':'1', 'CLAUDE_CODE_OAUTH_TOKEN':'token'}):
            for _ in range(2):
                self.assertEqual(self.client.analyze(b'jpeg','OCR text','system'),'answer')
        self.assertEqual(run.call_count, 1)
        self.assertEqual(factory.call_count, 1)
        command, env, timeout = factory.call_args.args
        self.assertEqual(command[command.index('--tools')+1], '')
        self.assertEqual(command[command.index('--effort')+1], 'medium')
        self.assertIn('--safe-mode', command)
        self.assertIn('--no-session-persistence', command)
        self.assertNotIn('--bare', command)
        self.assertFalse(any(k.startswith('ANTHROPIC_') for k in env))
        self.assertNotIn('CLAUDE_CODE_USE_BEDROCK', env)
        self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN', env)
        message = worker.request.call_args.args[0]['message']
        self.assertEqual(base64.b64decode(message['content'][0]['source']['data']),b'jpeg')
        self.assertEqual(message['content'][1]['text'],'OCR text')

    @patch('claude_subscription.ClaudeSession')
    @patch('claude_subscription.subprocess.run')
    def test_auth_failure_never_starts_worker(self, run, factory):
        for overrides in ({'loggedIn':False}, {'authMethod':'api_key'}, {'apiProvider':'bedrock'}):
            run.return_value = self.auth(**overrides)
            with self.assertRaises(RuntimeError):
                self.client.analyze(b'image','prompt','system')
        factory.assert_not_called()

    @patch('claude_subscription.ClaudeSession')
    @patch('claude_subscription.subprocess.run')
    def test_dead_worker_reauthenticates_and_reconnects(self, run, factory):
        run.return_value = self.auth()
        first, second = Mock(alive=True), Mock(alive=True)
        factory.side_effect = [first, second]
        self.client.warm('system')
        first.alive = False
        self.client.warm('system')
        first.close.assert_called_once()
        self.assertEqual(run.call_count, 2)

    @patch('claude_subscription.ClaudeSession')
    @patch('claude_subscription.subprocess.run')
    def test_model_workers_are_separate_and_reset_retains_both(self, run, factory):
        run.return_value = self.auth()
        factory.side_effect = [Mock(alive=True), Mock(alive=True)]
        self.client.warm('system')
        self.client.warm('system',model='claude-opus-5-5',effort='high')
        self.assertEqual(len(self.client.sessions),2)
        self.client.reset()
        self.assertEqual(len(self.client.sessions),2)
        for worker in self.client.sessions.values():
            worker.clear.assert_called_once()

    def test_error_empty_response_and_streaming(self):
        worker = Mock()
        with patch.object(self.client,'warm',return_value=worker):
            for result in ({'is_error':True,'result':'limit'}, {'result':''}):
                worker.request.return_value = result
                with self.assertRaises(RuntimeError):
                    self.client.analyze(b'image','prompt','system')
            def request(message, receive):
                receive({'type':'stream_event','event':{'delta':{'type':'text_delta','text':'hi'}}})
                return {'result':'hi'}
            worker.request.side_effect = request
            chunks=[]
            self.assertEqual(self.client.analyze(b'image','prompt','system',on_text=chunks.append),'hi')
            self.assertEqual(chunks,['hi'])

    def test_invalid_configuration(self):
        with self.assertRaises(ValueError):
            ClaudeSubscription(timeout=0)
        with self.assertRaises(ValueError):
            ClaudeSubscription(effort='unknown')
