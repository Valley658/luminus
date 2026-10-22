import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import accounts
import library
from test_accounts import Handler

class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.patch=patch.object(accounts.database,'DB',Path(self.folder.name)/'test.sqlite3');self.patch.start();accounts.init();library.init()
        with accounts.database.connect() as db:
            for uid in ('one','two'):db.execute('INSERT INTO users(id,handle,name,created) VALUES(?,?,?,0)',(uid,uid,uid))
            db.execute('INSERT INTO videos VALUES(?,?,?,?,?,?,?,?)',('abcdefghijk','video','Title','UCtest','Channel','https://i.ytimg.com/a.jpg','2026-10-22',0))
        self.cookie=accounts.session('one').split(';')[0];self.other=accounts.session('two').split(';')[0]
    def tearDown(self):self.patch.stop();self.folder.cleanup()
    def send(self,data,cookie=None):
        h=Handler(data,self.cookie if cookie is None else cookie);h.headers['Host']='127.0.0.1';library.post(h);return h
    def read(self,view,cookie=None,**extra):
        h=Handler({},self.cookie if cookie is None else cookie);library.get(h,dict(view=[view],**{k:[v] for k,v in extra.items()}));return h,json.loads(h.wfile.getvalue())
    def test_history_and_bookmarks_are_private_and_idempotent(self):
        for _ in range(2):self.assertEqual(self.send({'action':'history','video':'abcdefghijk'}).status,200)
        self.assertEqual(len(self.read('history')[1]['items']),1)
        self.assertEqual(self.read('history',self.other)[1]['items'],[])
        self.assertEqual(self.read('history','')[0].status,401)
        self.assertEqual(self.send({'action':'bookmark','video':'abcdefghijk'}).status,200)
        counts=self.read('mypage')[1]['counts'];self.assertEqual(counts['history'],1);self.assertEqual(counts['bookmarks'],1)
    def test_first_page_contains_own_items_without_client_fetch(self):
        self.send({'action':'bookmark','video':'abcdefghijk'})
        user=accounts.current(Handler({},self.cookie))
        _,html=library.page('bookmarks',user)
        self.assertIn('Title',html);self.assertIn('library-bootstrap',html);self.assertIn('/watch?v=abcdefghijk',html)
        _,guest=library.page('bookmarks')
        self.assertNotIn('/watch?v=abcdefghijk',guest);self.assertIn('로그인 후 이용해 주세요.',guest)
    def test_playlist_owner_checks(self):
        h=self.send({'action':'playlist-create','title':'My list'});pid=json.loads(h.wfile.getvalue())['id']
        self.assertEqual(self.send({'action':'playlist-add','playlist':pid,'video':'abcdefghijk'},self.other).status,403)
        self.assertEqual(self.send({'action':'playlist-add','playlist':pid,'video':'abcdefghijk'}).status,200)
        self.assertEqual(len(self.read('playlists',playlist=pid)[1]['items']),1)
        self.assertEqual(self.read('playlists',self.other,playlist=pid)[0].status,404)
    def test_gallery_validates_images_and_delete_permissions(self):
        self.assertEqual(self.send({'action':'gallery','title':'bad','image':base64.b64encode(b'not an image').decode()}).status,400)
        image=io.BytesIO();Image.new('RGB',(10,10)).save(image,'PNG')
        self.assertEqual(self.send({'action':'gallery','title':'Art','image':base64.b64encode(image.getvalue()).decode()}).status,200)
        items=self.read('gallery','')[1]['items'];self.assertEqual(len(items),1);self.assertNotIn('image/jpeg',json.dumps(items))
        self.assertEqual(self.send({'action':'gallery-delete','id':items[0]['id']},self.other).status,403)
        self.assertEqual(self.send({'action':'gallery-delete','id':items[0]['id']}).status,200)
    def test_highlight_timestamps_and_csrf(self):
        self.assertEqual(self.send({'action':'highlight','video':'abcdefghijk','title':'Moment','start':65}).status,200)
        self.assertEqual(self.read('highlights','')[1]['items'][0]['starts_at'],65)
        self.assertEqual(self.send({'action':'highlight','video':'abcdefghijk','title':'Bad','start':-1}).status,400)
        h=Handler({'action':'history','video':'abcdefghijk'},self.cookie,origin='https://evil.invalid');h.headers['Host']='127.0.0.1';library.post(h);self.assertEqual(h.status,403)

if __name__=='__main__':unittest.main()
