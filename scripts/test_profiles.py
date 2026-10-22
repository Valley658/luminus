import base64
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import accounts
from test_accounts import Handler


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.patch=patch.object(accounts.database,'DB',Path(self.folder.name)/'accounts.sqlite3')
        self.patch.start();accounts.init()
        with accounts.database.connect() as db:
            db.execute("INSERT INTO users(id,handle,name,created,is_admin) VALUES('owner','owner','리도',0,1)")
            db.execute("INSERT INTO users(id,handle,name,created,is_admin) VALUES('member','member','Member',0,0)")

    def tearDown(self):self.patch.stop();self.folder.cleanup()

    def request(self,uid,data):return Handler(data,accounts.session(uid).split(';')[0])

    def test_administrator_name_is_immutable_and_reserved(self):
        h=self.request('owner',{'name':'Changed'});accounts.post(h,'/api/auth/profile');self.assertEqual(h.status,403)
        h=self.request('member',{'name':'리도'});accounts.post(h,'/api/auth/profile');self.assertEqual(h.status,400)
        h=self.request('member',{'name':'Changed'});accounts.post(h,'/api/auth/profile');self.assertEqual(h.status,200)

    def test_gif_upload_preserves_all_animation_bytes_for_admin_and_member(self):
        gif=io.BytesIO()
        Image.new('RGB',(12,12),'red').save(gif,format='GIF',save_all=True,append_images=[Image.new('RGB',(12,12),'blue')],duration=100,loop=0)
        raw=gif.getvalue()
        for uid in ('owner','member'):
            h=self.request(uid,dict(image=base64.b64encode(raw).decode()));accounts.post(h,'/api/auth/avatar');self.assertEqual(h.status,200)
            result=self.request(uid,{});accounts.avatar_get(result)
            self.assertIn(('Content-Type','image/gif'),result.sent)
            self.assertEqual(result.wfile.getvalue(),raw)
            with Image.open(io.BytesIO(result.wfile.getvalue())) as image:self.assertEqual(image.n_frames,2)

    def test_invalid_image_is_rejected(self):
        h=self.request('member',dict(image=base64.b64encode(b'GIF89a bad image').decode()))
        accounts.post(h,'/api/auth/avatar');self.assertEqual(h.status,400)
