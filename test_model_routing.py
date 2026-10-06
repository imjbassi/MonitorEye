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
        app.last_capture = (b'original image', 'original OCR', {'preset':'quick','custom':''})
        with patch.object(app, 'capture_obs_window') as capture, patch.object(app, 'ocr_screenshot') as ocr, patch.object(app, 'client') as client, patch.object(app, 'live_view', None), patch.object(app, 'TELEGRAM_ENABLED', False):
            client.analyze.return_value = 'answer'
            app.run_pipeline(retry=True)
            capture.assert_not_called()
            ocr.assert_not_called()
            args, kwargs = client.analyze.call_args
            self.assertEqual(args[0], b'original image')
            self.assertIn('original OCR', args[1])
            self.assertIn('Quick answer', args[1])
            self.assertEqual(kwargs['model'], 'claude-opus-5-5')
            self.assertEqual(kwargs['effort'], 'high')

    def test_normal_capture_routes_to_sonnet(self):
        with patch.object(app, 'capture_obs_window', return_value=b'new image'), patch.object(app, 'ocr_screenshot', return_value='new OCR'), patch.object(app, 'client') as client, patch.object(app, 'live_view', None), patch.object(app, 'TELEGRAM_ENABLED', False):
            client.analyze.return_value = 'answer'
            app.run_pipeline()
            self.assertEqual(app.last_capture, (b'new image', 'new OCR', {'preset':'auto','custom':''}))
            self.assertEqual(client.analyze.call_args.kwargs['model'], 'claude-sonnet-5-5')
            self.assertEqual(client.analyze.call_args.kwargs['effort'], 'medium')

    def test_f3_requires_previous_capture(self):
        with patch.object(app.threading, 'Thread') as thread:
            app.on_press(app.keyboard.Key.f3)
            thread.assert_not_called()

    def test_failed_capture_clears_previous_capture(self):
        app.last_capture = (b'old', '', {'preset':'auto','custom':''})
        with patch.object(app, 'capture_obs_window', return_value=None), patch.object(app, 'live_view', None), patch.object(app, 'client') as client:
            app.run_pipeline()
            self.assertIsNone(app.last_capture)
            client.analyze.assert_not_called()

    def test_selection_frozen_before_capture_and_reused_for_retry(self):
        from live_view import LiveView
        view=LiveView()
        view.select_prompt({'preset':'custom','custom':'Explain using Java only.'})
        def capture():
            view.select_prompt({'preset':'review','custom':''})
            return b'image'
        with patch.object(app,'capture_obs_window',side_effect=capture), patch.object(app,'ocr_screenshot',return_value='OCR'), patch.object(app,'client') as client, patch.object(app,'live_view',view), patch.object(app,'TELEGRAM_ENABLED',False):
            client.analyze.return_value='answer'
            app.run_pipeline()
            self.assertEqual(view.state['used_prompt'],'Custom')
            self.assertEqual(view.prompt_snapshot()['preset'],'review')
            self.assertIn('Explain using Java only.',client.analyze.call_args.args[1])
            app.run_pipeline(retry=True)
            self.assertIn('Explain using Java only.',client.analyze.call_args.args[1])
            self.assertEqual(client.analyze.call_args.kwargs['model'],'claude-opus-5-5')


    def test_project_capture_uses_project_and_preserves_snapshot_retry(self):
        original = (b'old snapshot', 'OCR', {'preset':'auto','custom':''})
        app.last_capture = original
        view = Mock()
        with patch.object(app, 'live_view', view), patch.object(app, 'client') as client:
            app.run_pipeline(project_id='current-project')
            view.project.capture.assert_called_once_with('current-project', app.capture_obs_window, app.ocr_screenshot)
            client.analyze.assert_not_called()
            self.assertEqual(app.last_capture, original)

    def test_hotkey_freezes_project_destination(self):
        view = Mock()
        view.capture_destination.return_value = 'session-at-keypress'
        with patch.object(app, 'live_view', view), patch.object(app.threading, 'Thread') as thread:
            app.on_press(app.keyboard.Key.f1)
            self.assertEqual(thread.call_args.kwargs['kwargs'], {'project_id':'session-at-keypress'})

    def test_target_switch_does_not_reset_context(self):
        from live_view import LiveView
        view = LiveView()
        self.addCleanup(view.close)
        view.update(project={'session_id':'project-one'})
        self.assertIsNone(view.capture_destination())
        view.select_capture_target('project')
        self.assertEqual(view.capture_destination(), 'project-one')
        view.select_capture_target('snapshot')
        self.assertIsNone(view.capture_destination())
        self.assertEqual(view.state['project']['session_id'], 'project-one')



if __name__ == '__main__':
    unittest.main()
