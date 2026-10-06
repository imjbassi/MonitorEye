import unittest
from unittest.mock import patch, Mock
import monitor_eye_mac as app
from claude_subscription import ClaudeSubscription


class RoutingTests(unittest.TestCase):
    def tearDown(self):
        app.last_capture = None
        app.capturing = False
        app.current_keys.clear()

    def test_retry_reuses_capture_and_routes_to_opus(self):
        app.last_capture = (b'original image', 'original OCR')
        with patch.object(app, 'capture_obs_window') as capture, patch.object(app, 'ocr_screenshot') as ocr, patch.object(app, 'client') as client, patch.object(app, 'live_view'), patch.object(app, 'TELEGRAM_ENABLED', False):
            client.analyze.return_value = 'answer'
            app.run_pipeline(retry=True)
            capture.assert_not_called()
            ocr.assert_not_called()
            args, kwargs = client.analyze.call_args
            self.assertEqual(args[0], b'original image')
            self.assertIn('original OCR', args[1])
            self.assertEqual(kwargs['model'], 'claude-opus-5-5')
            self.assertEqual(kwargs['effort'], 'high')

    def test_normal_capture_routes_to_sonnet(self):
        with patch.object(app, 'capture_obs_window', return_value=b'new image'), patch.object(app, 'ocr_screenshot', return_value='new OCR'), patch.object(app, 'client') as client, patch.object(app, 'live_view'), patch.object(app, 'TELEGRAM_ENABLED', False):
            client.analyze.return_value = 'answer'
            app.run_pipeline()
            self.assertEqual(app.last_capture, (b'new image', 'new OCR'))
            self.assertEqual(client.analyze.call_args.kwargs['model'], 'claude-sonnet-5-5')
            self.assertEqual(client.analyze.call_args.kwargs['effort'], 'medium')

    def test_f3_requires_previous_capture(self):
        with patch.object(app.threading, 'Thread') as thread:
            app.on_press(app.keyboard.Key.f3)
            thread.assert_not_called()

    def test_failed_capture_clears_previous_capture(self):
        app.last_capture = (b'old', '')
        with patch.object(app, 'capture_obs_window', return_value=None), patch.object(app, 'live_view'), patch.object(app, 'client') as client:
            app.run_pipeline()
            self.assertIsNone(app.last_capture)
            client.analyze.assert_not_called()

    def test_effort_passed_to_cli(self):
        with patch('claude_subscription.shutil.which', return_value='/bin/claude'):
            client = ClaudeSubscription()
        with patch.object(client, 'check_auth'), patch.object(client, '_run') as run:
            run.return_value = Mock(stdout='{"type":"result","result":"ok"}\n', returncode=0)
            client.analyze(b'image', 'prompt', 'system', model='claude-opus-5-5', effort='high')
            command = run.call_args.args[0]
            self.assertEqual(command[command.index('--model')+1], 'claude-opus-5-5')
            self.assertEqual(command[command.index('--effort')+1], 'high')


if __name__ == '__main__':
    unittest.main()
