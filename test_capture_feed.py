from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
from PIL import Image
from capture_feed import CaptureFeed


class CaptureFeedTests(unittest.TestCase):
    def run_feed(self, script):
        directory=tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path=Path(directory.name)/'camera.py';path.write_text(script)
        real_popen=subprocess.Popen
        def spawn(command, **kwargs):
            return real_popen([sys.executable,'-u',str(path)],**kwargs)
        patcher=patch('capture_feed.subprocess.Popen',side_effect=spawn)
        factory=patcher.start();self.addCleanup(patcher.stop)
        resolver=Mock(return_value='0')
        feed=CaptureFeed(resolver,'USB Video','2x1','30','uyvy422')
        self.addCleanup(feed.close)
        return feed,resolver,factory,Path(directory.name)/'frame.png'

    def test_keeps_device_open_and_waits_for_new_frame(self):
        feed,resolver,factory,path=self.run_feed('''import sys,time
for i in range(1,200):
 data=bytes([i,0,0])*2
 sys.stdout.buffer.write(data[:2]);sys.stdout.buffer.flush()
 time.sleep(.01)
 sys.stdout.buffer.write(data[2:]);sys.stdout.buffer.flush()
 time.sleep(.04)
''')
        feed.snapshot(path,timeout=1)
        first=Image.open(path).getpixel((0,0))[0]
        feed.snapshot(path,timeout=1)
        second=Image.open(path).getpixel((0,0))[0]
        self.assertGreater(second,first)
        self.assertEqual(factory.call_count,1)
        self.assertEqual(resolver.call_count,1)

    def test_disconnect_reconnects_and_does_not_return_partial_frame(self):
        feed,resolver,factory,path=self.run_feed("import sys;sys.stdout.buffer.write(b'xx')")
        with self.assertRaisesRegex(RuntimeError,'unavailable'):
            feed.snapshot(path,timeout=.2)
        self.assertEqual(factory.call_count,2)
        self.assertEqual(resolver.call_count,2)
        self.assertFalse(path.exists())

    def test_stalled_feed_is_killed(self):
        feed,resolver,factory,path=self.run_feed('import time;time.sleep(10)')
        with self.assertRaises(RuntimeError):
            feed.snapshot(path,timeout=.1)
        self.assertIsNone(feed.process)
        self.assertFalse(path.exists())
