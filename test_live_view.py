import json
import urllib.error
import urllib.request
import unittest
from live_view import LiveView


class LiveViewTests(unittest.TestCase):
    def setUp(self):
        self.view = LiveView(port=0, host='127.0.0.1').start()

    def tearDown(self):
        self.view.close()

    def test_requires_private_link(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(self.view.url().split('?')[0], timeout=2)
        self.assertEqual(error.exception.code, 403)
        with urllib.request.urlopen(self.view.url(), timeout=2) as response:
            self.assertIn(b'EventSource', response.read())

    def test_stream_and_reconnect(self):
        url = self.view.url().replace('/?', '/events?')
        with urllib.request.urlopen(url, timeout=2) as response:
            state = json.loads(response.readline().decode().removeprefix('data: '))
            self.assertEqual(state['text'], '')
            response.readline()
            self.view.append('hello')
            state = json.loads(response.readline().decode().removeprefix('data: '))
            self.assertEqual(state['text'], 'hello')
        with urllib.request.urlopen(url, timeout=2) as response:
            state = json.loads(response.readline().decode().removeprefix('data: '))
            self.assertEqual(state['text'], 'hello')


if __name__ == '__main__':
    unittest.main()
