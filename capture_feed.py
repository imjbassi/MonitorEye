"""Keep an AVFoundation device open and retain only its latest complete frame."""
from collections import deque
import subprocess
import threading
import time


class CaptureFeed:
    def __init__(self, resolve_device, name, size, framerate, pixel_format):
        self.resolve_device, self.name = resolve_device, name
        self.width, self.height = map(int, size.split('x'))
        if min(self.width, self.height) <= 0:
            raise ValueError('VIDEO_SIZE must have positive dimensions.')
        self.size, self.framerate, self.pixel_format = size, framerate, pixel_format
        self.process = None
        self.condition = threading.Condition()
        self.lifecycle = threading.Lock()
        self.latest = None
        self.sequence = 0
        self.ended = True
        self.closed = False
        self.errors = deque(maxlen=8)
        self.readers = []

    def start(self):
        with self.lifecycle:
            if self.closed:
                raise RuntimeError('Capture feed is closed.')
            if self.process and self.process.poll() is None and not self.ended:
                return
            self._stop()
            device = self.resolve_device(self.name)
            command = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                       '-f', 'avfoundation', '-pixel_format', self.pixel_format,
                       '-video_size', self.size, '-framerate', self.framerate,
                       '-i', device, '-an', '-vf', f'fps=10,scale={self.width}:{self.height}',
                       '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1']
            self.errors.clear()
            self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            stdin=subprocess.DEVNULL, bufsize=0)
            with self.condition:
                self.latest = None
                self.ended = False
            proc = self.process
            self.readers = [threading.Thread(target=self._read_frames, args=(proc,), daemon=True),
                            threading.Thread(target=self._read_errors, args=(proc,), daemon=True)]
            for thread in self.readers:
                thread.start()

    def _read_errors(self, proc):
        for line in proc.stderr:
            self.errors.append(line.decode(errors='replace').strip())

    def _read_frames(self, proc):
        size = self.width * self.height * 3
        try:
            while True:
                frame = bytearray()
                while len(frame) < size:
                    part = proc.stdout.read(size - len(frame))
                    if not part:
                        return
                    frame.extend(part)
                with self.condition:
                    self.latest = bytes(frame)
                    self.sequence += 1
                    self.condition.notify_all()
        finally:
            with self.condition:
                self.ended = True
                self.latest = None
                self.condition.notify_all()

    def snapshot(self, path, timeout=5):
        """Wait for the next full frame; never hand out a stale cached frame."""
        # Reopen once after a disconnect/stall, resolving the device index again.
        for attempt in range(2):
            self.start()
            with self.condition:
                previous = self.sequence
                ready = self.condition.wait_for(
                    lambda: self.sequence > previous or self.ended, timeout=timeout)
                frame = self.latest if ready and not self.ended and self.sequence > previous else None
            if frame is not None:
                from PIL import Image
                Image.frombytes('RGB', (self.width, self.height), frame).save(path, format='PNG')
                return
            with self.lifecycle:
                self._stop()
        detail = '; '.join(self.errors) or 'No fresh frames received'
        raise RuntimeError(f'Capture feed unavailable: {detail}. Check device connection, video mode and Camera permission.')

    def _stop(self):
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.process.kill()
            self.process.wait()
            for thread in self.readers:
                thread.join(timeout=1)
            self.process.stdout.close()
            self.process.stderr.close()
            self.process = None
        with self.condition:
            self.latest = None
            self.ended = True
            self.condition.notify_all()

    def close(self):
        with self.lifecycle:
            self.closed = True
            self._stop()
