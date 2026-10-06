from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from project_session import ProjectSession, ProjectBusy


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        subprocess.run(['git','init','-q',self.directory.name],check=True)
        self.project=ProjectSession(lambda state:None)
        self.addCleanup(self.project.close)
        auth_patch=patch('project_session.ClaudeSubscription')
        self.auth=auth_patch.start().return_value
        self.auth.binary='/bin/claude';self.auth._environment.return_value={}
        self.addCleanup(auth_patch.stop)
        worker_patch=patch('project_session.ClaudeSession')
        self.factory=worker_patch.start()
        self.worker=self.factory.return_value
        self.worker.request.return_value={'result':'answer'}
        self.addCleanup(worker_patch.stop)

    def idle(self):
        deadline=time.monotonic()+3
        while self.project.state['busy']:
            self.assertLess(time.monotonic(),deadline)
            time.sleep(.01)

    def start(self):
        self.project.start(self.directory.name, source='repository')
        self.idle()
        self.assertTrue(self.project.state['ready'])

    def test_scope_and_context_retained(self):
        self.start()
        command=self.factory.call_args.args[0]
        self.assertEqual(command[command.index('--tools')+1],'Read,Glob,Grep')
        self.assertIn('--restricted',command)
        self.assertEqual(command[command.index('--permission-mode')+1],'dontAsk')
        self.assertEqual(self.factory.call_args.kwargs['cwd'],str(Path(self.directory.name).resolve()))
        session_id=self.project.state['session_id']
        for i in range(2):
            self.project.send(f'message {i}',str(i),session_id);self.idle()
        self.assertEqual(self.factory.call_count,1)
        self.assertEqual(len(self.project.state['messages']),4)
        for call in self.worker.request.call_args_list:
            self.assertFalse(call.kwargs['clear_before'])

    def test_duplicate_send_and_stale_session(self):
        self.start();sid=self.project.state['session_id']
        self.project.send('hello','id',sid);self.idle()
        self.project.send('hello','id',sid)
        self.assertEqual(self.worker.request.call_count,1)
        with self.assertRaises(ValueError):self.project.send('hello','new','old-session')

    def test_new_session_clears_context_and_closes_worker(self):
        self.start();sid=self.project.state['session_id']
        self.project.send('hello','id',sid);self.idle()
        self.project.start(self.directory.name,'opus', source='repository');self.idle()
        self.assertNotEqual(sid,self.project.state['session_id'])
        self.assertEqual(self.project.state['messages'],[])
        self.worker.close.assert_called()
        command=self.factory.call_args.args[0]
        self.assertEqual(command[command.index('--model')+1],'claude-opus-5-5')

    def test_stop_ignores_late_response(self):
        gate=threading.Event();entered=threading.Event();finished=threading.Event()
        def delayed(*args,**kwargs):
            entered.set();gate.wait(2);finished.set();return {'result':'late text'}
        self.worker.request.side_effect=delayed
        self.start();self.project.send('hello','id',self.project.state['session_id'])
        self.assertTrue(entered.wait(1))
        with self.assertRaises(ProjectBusy):self.project.start(self.directory.name, source='repository')
        self.project.stop();gate.set();finished.wait(1)
        self.assertFalse(self.project.state['ready'])
        self.assertFalse(self.project.state['busy'])
        self.assertNotIn('late text',str(self.project.state))

    def test_invalid_repo_and_prompt(self):
        with self.assertRaises(ValueError):self.project.start('relative/path', source='repository')
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):self.project.start(folder, source='repository')
        self.start()
        with self.assertRaises(ValueError):self.project.send('','id',self.project.state['session_id'])

    def test_error_requires_explicit_new_session(self):
        self.worker.request.side_effect=RuntimeError('connection lost')
        self.start();self.project.send('hello','id',self.project.state['session_id']);self.idle()
        self.assertFalse(self.project.state['ready'])
        self.assertIn('Start a new session',self.project.state['status'])

    def test_screenshots_need_no_repo_or_tools_and_retain_context(self):
        self.project.start()
        self.idle()
        sid = self.project.state['session_id']
        command = self.factory.call_args.args[0]
        self.assertEqual(command[command.index('--tools') + 1], '')
        self.assertIsNone(self.factory.call_args.kwargs['cwd'])
        self.assertEqual(self.project.state['repo'], '')
        self.project.capture(sid, lambda: b'first jpeg', lambda: 'first OCR')
        self.project.capture(sid, lambda: b'second jpeg', lambda: 'second OCR')
        self.project.send('Connect the two screenshots.', 'followup', sid)
        self.idle()
        self.assertEqual(self.factory.call_count, 1)
        calls = self.worker.request.call_args_list
        self.assertEqual(len(calls), 3)
        import base64
        for i, jpeg in enumerate((b'first jpeg', b'second jpeg')):
            blocks = calls[i].args[0]['message']['content']
            self.assertEqual(base64.b64decode(blocks[0]['source']['data']), jpeg)
            self.assertIn('OCR', blocks[1]['text'])
            self.assertIn(f'Screenshot {i+1}', blocks[1]['text'])
        self.assertTrue(all(not call.kwargs['clear_before'] for call in calls))
        self.assertEqual(self.project.state['screenshots'], 2)
        self.assertNotIn('base64', str(self.project.state))

    def test_capture_failure_keeps_prior_context(self):
        self.project.start(); self.idle()
        sid = self.project.state['session_id']
        self.project.capture(sid, lambda: None, lambda: '')
        self.assertTrue(self.project.state['ready'])
        self.assertFalse(self.project.state['busy'])
        self.assertEqual(self.project.state['screenshots'], 0)
        self.assertIn('Capture failed', self.project.state['status'])
        self.worker.request.assert_not_called()
        self.project.capture(sid, lambda: b'jpeg', lambda: '')
        self.assertEqual(self.project.state['screenshots'], 1)

    def test_capture_reserves_session_and_discards_after_stop(self):
        self.project.start(); self.idle()
        sid = self.project.state['session_id']
        capture = Mock(return_value=b'jpeg')
        with self.assertRaises(ValueError):
            self.project.capture('old-session', capture, lambda: '')
        capture.assert_not_called()
        def capture_then_stop():
            with self.assertRaises(ProjectBusy):
                self.project.send('followup', 'id', sid)
            with self.assertRaises(ProjectBusy):
                self.project.capture(sid, capture, lambda: '')
            self.project.stop()
            return b'jpeg'
        self.project.capture(sid, capture_then_stop, lambda: '')
        self.worker.request.assert_not_called()
        self.assertEqual(self.project.state['screenshots'], 0)
