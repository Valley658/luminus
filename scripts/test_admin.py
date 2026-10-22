import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import accounts
import admin
import admin_operations
from test_accounts import Handler


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.patch=patch.object(accounts.database,'DB',Path(self.directory.name)/'accounts.sqlite3')
        self.patch.start();accounts.init();admin.init();admin_operations.init()
        with accounts.database.connect() as db:
            db.execute("INSERT INTO users(id,handle,name,password,created,is_admin) VALUES('admin','owner','Owner','secret-hash',0,1)")
            db.execute("INSERT INTO users(id,handle,name,password,created,is_admin) VALUES('member','member','리도','secret-hash',0,0)")

    def tearDown(self):
        self.patch.stop();self.directory.cleanup()

    def handler(self,uid=None,data=None,origin='https://admin.pastellive.co.kr'):
        cookie=accounts.session(uid).split(';')[0].replace('pastellive_session=','luminous_admin_session=') if uid else ''
        handler=Handler(data or {},cookie,origin)
        handler.headers['Host']='admin.pastellive.co.kr'
        return handler

    def test_guest_and_non_admin_cannot_read_dashboard_data(self):
        h=self.handler();admin.get(h,'/api/admin/summary');self.assertEqual(h.status,401)
        h=self.handler('member');admin.get(h,'/api/admin/summary');self.assertEqual(h.status,403)

    def test_admin_summary_does_not_expose_hashes_or_session_tokens(self):
        h=self.handler('admin');admin.get(h,'/api/admin/summary');self.assertEqual(h.status,200)
        self.assertNotIn(b'secret-hash',h.wfile.getvalue())
        self.assertEqual(json.loads(h.wfile.getvalue())['home']['titleSize'],20)

    def test_home_configuration_requires_origin_and_valid_values(self):
        data=dict(showShorts=False,titleSize=22,announcement='Test notice')
        h=self.handler('admin',data,'https://evil.example');admin.post(h,'/api/admin/home');self.assertEqual(h.status,403)
        h=self.handler('admin',dict(data,titleSize=100));admin.post(h,'/api/admin/home');self.assertEqual(h.status,400)
        h=self.handler('admin',data);admin.post(h,'/api/admin/home');self.assertEqual(h.status,200)
        self.assertEqual(admin.home_settings(),data)

    def test_existing_main_session_is_shared_only_after_server_validation(self):
        token=accounts.session('admin').split(';')[0]
        h=Handler({},token);h.headers['Host']='pastellive.co.kr'
        cookies=accounts.admin_session_cookie(h,accounts.current(h))
        self.assertIn('Domain=.pastellive.co.kr',cookies[0])
        self.assertIn('HttpOnly',cookies[0])
        guest=Handler({});guest.headers['Host']='pastellive.co.kr'
        self.assertEqual(accounts.admin_session_cookie(guest,accounts.current(guest)),[])

    def test_admin_account_cannot_be_suspended_or_deleted(self):
        for action in ('suspend','delete','staff','sessions'):
            h=self.handler('admin',dict(id='admin',action=action,confirm='owner'))
            admin.post(h,'/api/admin/users/action');self.assertEqual(h.status,403)

    def test_suspension_invalidates_sessions_and_can_be_reversed(self):
        member=self.handler('member')
        h=self.handler('admin',dict(id='member',action='suspend',hours=24));admin.post(h,'/api/admin/users/action')
        self.assertEqual(h.status,200);self.assertIsNone(accounts.current(member))
        h=self.handler('admin',dict(id='member',action='unsuspend'));admin.post(h,'/api/admin/users/action');self.assertEqual(h.status,200)
        self.assertIsNotNone(accounts.current(self.handler('member')))

    def test_invalid_large_settings_do_not_partially_enable_maintenance(self):
        words=[str(i)+'x'*38 for i in range(30)]
        h=self.handler('admin',dict(maintenance=dict(enabled=True,message='Test'),bannedWords=words))
        admin.post(h,'/api/admin/site-settings');self.assertEqual(h.status,400)
        self.assertFalse(admin_operations.setting('maintenance',dict(enabled=False))['enabled'])


if __name__=='__main__':unittest.main()
