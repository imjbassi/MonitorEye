import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from claude_session import ClaudeSession


class StreamTransportTests(unittest.TestCase):
    def session(self, script, timeout=2):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name)/'fake.py'
        path.write_text(script)
        session = ClaudeSession([sys.executable,'-u',str(path)],os.environ.copy(),timeout)
        self.addCleanup(session.close)
        return session

    def message(self, text):
        return {'type':'user','message':{'role':'user','content':text}}

    def test_persistent_reset_and_incremental_delivery(self):
        session = self.session('''import sys,json,time
for line in sys.stdin:
 m=json.loads(line)['message']['content']
 if m=='/clear':
  print(json.dumps({'type':'result','local_command':'clear','result':''}),flush=True)
 else:
  print(json.dumps({'type':'stream_event','text':m}),flush=True)
  time.sleep(.3)
  print(json.dumps({'type':'result','result':m}),flush=True)
''')
        pid = session.process.pid
        for text in ('red','blue'):
            times=[]
            result = session.request(self.message(text),lambda event: times.append(time.monotonic()))
            self.assertEqual(result['result'],text)
            self.assertEqual(len(times),1)
            self.assertGreater(time.monotonic()-times[0],.2)
            self.assertEqual(session.process.pid,pid)
            self.assertTrue(session.alive)

    def test_project_mode_keeps_context(self):
        session = self.session('import sys,json\nseen=[]\nfor line in sys.stdin:\n text=json.loads(line)["message"]["content"]\n seen.append(text)\n print(json.dumps({"type":"result","result":"|".join(seen)}),flush=True)\n')
        session.request(self.message('first'),clear_before=False)
        result=session.request(self.message('second'),clear_before=False)
        self.assertEqual(result['result'],'first|second')

    def test_reset_failure_closes_before_next_image(self):
        session = self.session('''import sys,json
for line in sys.stdin:
 print(json.dumps({'type':'result','result':'not a reset'}),flush=True)
''')
        session.request(self.message('first'))
        with self.assertRaisesRegex(RuntimeError,'clear'):
            session.request(self.message('second'))
        self.assertFalse(session.alive)

    def test_timeout_kills_worker(self):
        session = self.session('import time;time.sleep(10)',.15)
        start=time.monotonic()
        with self.assertRaisesRegex(RuntimeError,'timed out'):
            session.request(self.message('test'))
        self.assertFalse(session.alive)
        self.assertLess(time.monotonic()-start,3)

    def test_crash_and_error_close_worker(self):
        session=self.session('import sys;sys.exit(1)')
        with self.assertRaisesRegex(RuntimeError,'disconnected'):
            session.request(self.message('test'))
        self.assertFalse(session.alive)
