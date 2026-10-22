import io
import unittest
from unittest.mock import patch
import legacy_ui

class Handler:
    path='/'
    headers={}
    def __init__(self):self.wfile=io.BytesIO();self.sent={}
    def send_response(self,status):self.status=status
    def send_header(self,key,value):self.sent[key]=value
    def end_headers(self):pass
    def send_error(self,status):self.status=status

class LegacyUITests(unittest.TestCase):
    def render(self,path):
        item=dict(id='abcdefghijk',title='<script>unsafe</script>',thumbnail='https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg',channelName='Channel')
        with patch.object(legacy_ui.accounts,'current',return_value=None),patch.object(legacy_ui.youtube_shorts,'shorts_page',return_value={'items':[item]}),patch.object(legacy_ui.youtube_shorts,'video_feed',return_value={'items':[item]}):
            handler=Handler();handler.path=path;legacy_ui.serve(handler,path.split('?')[0]);return handler,handler.wfile.getvalue().decode()
    def test_home_uses_original_markup_without_missing_scripts(self):
        handler,body=self.render('/')
        self.assertEqual(handler.status,200);self.assertIn('id="sidebarList"',body);self.assertIn('id="ytFixedTop"',body);self.assertIn('id="videoGrid"',body)
        self.assertNotIn('/static/js/script.js',body);self.assertNotIn('<script>unsafe</script>',body);self.assertNotIn('onclick=',body)
        self.assertIn('legacy.js?v=',body);self.assertIn('private, no-store',handler.sent['Cache-Control'])
    def test_old_shell_library_does_not_reveal_guest_private_data(self):
        handler,body=self.render('/history');self.assertEqual(handler.status,200);self.assertIn('로그인 후 이용해 주세요.',body);self.assertIn('library-bootstrap',body)

if __name__=='__main__':unittest.main()
