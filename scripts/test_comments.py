import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import accounts
import comments
from test_accounts import Handler

class CommentsTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.patch=patch.object(accounts.database,'DB',Path(self.folder.name)/'test.sqlite3');self.patch.start()
        accounts.init();comments.init();comments.attempts.clear()
        with accounts.database.connect() as db:
            for uid in ('one','two'):db.execute('INSERT INTO users(id,handle,name,created) VALUES(?,?,?,0)',(uid,uid,uid))
        self.cookie=accounts.session('one').split(';')[0]
    def tearDown(self):self.patch.stop();self.folder.cleanup()
    def handler(self,data,cookie=''):
        h=Handler(data,cookie);h.headers['Host']='127.0.0.1';return h
    def test_session_author_identity_escaping_and_owner_delete(self):
        h=self.handler({'video':'abcdefghijk','body':'<script>text</script>','user_id':'two'},self.cookie);comments.post(h,'/api/comments');self.assertEqual(h.status,200)
        h=self.handler({},self.cookie);comments.get(h,{'video':['abcdefghijk']});item=json.loads(h.wfile.getvalue())['items'][0]
        self.assertEqual(item['author'],'one');self.assertTrue(item['canDelete'])
        other=accounts.session('two').split(';')[0];h=self.handler({'id':item['id']},other);comments.post(h,'/api/comments/delete');self.assertEqual(h.status,403)
        h=self.handler({'id':item['id']},self.cookie);comments.post(h,'/api/comments/delete');self.assertEqual(h.status,200)
    def test_author_title_is_resolved_and_masked_on_server(self):
        cid='UC'+'a'*22
        with patch.object(comments.youtube_shorts,'api',return_value={'items':[{'id':cid,'snippet':{'title':'댓글작성자이름'}}]}):
            names=comments.author_names([{'authorChannelId':{'value':cid},'authorDisplayName':'@handle'}])
        self.assertNotIn('댓글작성자이름',names[cid]);self.assertIn('▧',names[cid]);self.assertNotIn('@',names[cid])
        with patch.object(comments.youtube_shorts,'api') as api:
            self.assertEqual(comments.author_names([{'authorChannelId':{'value':cid}}]),names);api.assert_not_called()
        self.assertEqual(comments.masked_name('김'),'▧')
        self.assertNotIn('홍길동',comments.masked_name('홍길동'))

    def test_guest_csrf_and_limits(self):
        h=self.handler({'video':'abcdefghijk','body':'hi'});comments.post(h,'/api/comments');self.assertEqual(h.status,401)
        h=self.handler({'video':'abcdefghijk','body':'a'*501},self.cookie);comments.post(h,'/api/comments');self.assertEqual(h.status,400)
        h=self.handler({},self.cookie);h.headers['Origin']='https://evil.invalid';comments.post(h,'/api/comments');self.assertEqual(h.status,403)
    def test_render_preloads_shared_youtube_cache_and_escapes_bootstrap(self):
        data=dict(items=[dict(author='홍▧',body='</script><script>bad</script>')],refreshing=False,nextCursor='')
        with patch.object(comments,'youtube',return_value=data) as fetch:
            body=comments.render('abcdefghijk')
        fetch.assert_called_once_with('abcdefghijk','')
        self.assertIn('youtube-comments-bootstrap',body)
        self.assertNotIn('</script><script>bad',body)
        self.assertIn('\\u003c/script>',body)
    def test_cached_youtube_pages_survive_new_requests(self):
        import io
        payload={'items':[{'snippet':{'topLevelComment':{'id':'yt1','snippet':{'authorDisplayName':'Author','textDisplay':'Text','likeCount':3}}}}],'nextPageToken':'next'}
        with patch.object(comments.youtube_shorts,'api_key',return_value='unused'),patch.object(comments.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(payload).encode())):
            import hashlib
            key=hashlib.sha256(b'masked-v2:abcdefghijk:').hexdigest();comments.refresh('abcdefghijk','',key)
        with patch.object(comments.threading,'Thread') as thread:
            data=comments.youtube('abcdefghijk','');self.assertEqual(data['items'][0]['body'],'Text');self.assertEqual(data['nextCursor'],'next');thread.assert_not_called()

if __name__=='__main__':unittest.main()
