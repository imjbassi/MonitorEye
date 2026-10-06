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

    def post_prompt(self, data, token=None):
        request=urllib.request.Request(self.view.url().split('?')[0]+'prompt',data=json.dumps(data).encode(),headers={'Content-Type':'application/json','X-MonitorEye-Token':self.view.token if token is None else token},method='POST')
        return urllib.request.urlopen(request,timeout=2)

    def test_prompt_requires_token_and_validates_input(self):
        for data,token,code in [({'preset':'quick'},'wrong',403),({'preset':'missing'},None,400),({'preset':'custom','custom':''},None,400),({'preset':'custom','custom':'a'*6001},None,400),([],None,400)]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.post_prompt(data,token)
            self.assertEqual(error.exception.code,code)
        self.assertEqual(self.view.prompt_snapshot()['preset'],'auto')

    def test_project_endpoints_require_token(self):
        for path in ('project/start','project/message','project/stop','capture-target'):
            request=urllib.request.Request(self.view.url().split('?')[0]+path,data=b'{}',headers={'Content-Type':'application/json'},method='POST')
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request,timeout=2)
            self.assertEqual(error.exception.code,403)
        self.assertEqual(self.view.project.state['messages'],[])

    def test_start_screenshot_session_without_path(self):
        from unittest.mock import patch
        with patch.object(self.view.project, 'start', return_value={'session_id':'new'}) as start:
            request = urllib.request.Request(self.view.url().split('?')[0]+'project/start',
                data=b'{"source":"screenshots","model":"sonnet"}',
                headers={'Content-Type':'application/json','X-MonitorEye-Token':self.view.token},method='POST')
            with urllib.request.urlopen(request,timeout=2) as response:
                self.assertEqual(json.load(response)['session_id'], 'new')
            start.assert_called_once_with(None, 'sonnet', 'screenshots')
            self.assertEqual(self.view.state['capture_target'], 'project')

    def test_custom_selection_snapshot_is_independent(self):
        with self.post_prompt({'preset':'custom','custom':'Use Python.'}) as response:
            self.assertEqual(json.load(response)['custom'],'Use Python.')
        snapshot=self.view.prompt_snapshot()
        with self.post_prompt({'preset':'quick'}) as response:
            response.read()
        self.assertEqual(snapshot,{'preset':'custom','custom':'Use Python.'})
        self.assertEqual(self.view.prompt_snapshot()['preset'],'quick')
        self.assertEqual(self.view.state['text'],'')


if __name__ == '__main__':
    unittest.main()
