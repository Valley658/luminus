import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import accounts


class Handler:
    def __init__(self,data,cookie='',origin='http://127.0.0.1'):
        raw=json.dumps(data).encode();self.headers={'Content-Length':str(len(raw)),'Cookie':cookie,'Origin':origin}
        self.rfile=io.BytesIO(raw);self.wfile=io.BytesIO();self.client_address=('127.0.0.1',1);self.sent=[]
    def send_response(self,status):self.status=status
    def send_header(self,k,v):self.sent.append((k,v))
    def end_headers(self):pass


class AccountsTests(unittest.TestCase):
    def test_discord_link_saves_identity_and_persistent_session(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(accounts.database,'DB',Path(directory)/'test.sqlite3'):
            accounts.init()
            with accounts.database.connect() as db:
                db.execute("INSERT INTO users(id,handle,name,password,created) VALUES('u','member','Member','hash',0)")
            session=accounts.session('u').split(';')[0]
            h=Handler({},session+'; pastellive_oauth=binding');h.path='/auth/discord/callback?state=state&code=code'
            with accounts.database.connect() as db:
                db.execute('INSERT INTO oauth_states VALUES(?,?,?,?,?)',(accounts.digest('state'),accounts.digest('binding'),'u',accounts.time.time()+600,accounts.redirect_uri(h)))
            replies=[io.BytesIO(b'{"access_token":"test"}'),io.BytesIO(b'{"id":"123456","username":"linked_user","global_name":"Linked Name"}')]
            with patch.object(accounts,'config',return_value={'DISCORD_CLIENT_ID':'id','DISCORD_CLIENT_SECRET':'secret'}),patch.object(accounts.urllib.request,'urlopen',side_effect=replies) as request:
                accounts.oauth_callback(h)
            self.assertEqual(h.status,303)
            self.assertIn('Luminous',request.call_args.args[0].get_header('User-agent'))
            identity=accounts.public(accounts.current(Handler({},session)))
            self.assertTrue(identity['discordLinked'])
            self.assertEqual(identity['discordAccount']['username'],'linked_user')
            self.assertEqual(identity['discordAccount']['name'],'Linked Name')
            self.assertTrue(any(k=='Set-Cookie' and 'Max-Age=1209600' in v for k,v in h.sent))

    def test_reserved_admin_name_signup_rename_and_existing_account(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(accounts.database,'DB',Path(directory)/'test.sqlite3'):
            accounts.init();accounts.attempts.clear()
            with accounts.database.connect() as db:
                db.execute("INSERT INTO users(id,handle,name,created) VALUES('admin','rido','리도',0)")
                db.execute("DELETE FROM account_migrations WHERE name='reserve-rido'")
            accounts.init()
            admin_cookie=accounts.session('admin').split(';')[0]
            self.assertTrue(accounts.public(accounts.current(Handler({},admin_cookie)))['isAdmin'])
            signup=Handler(dict(name=' 리도 ',handle='new_member',password='long-password-123'))
            accounts.post(signup,'/api/auth/signup');self.assertEqual(signup.status,400)
            signup=Handler(dict(name='일반회원',handle='new_member',password='long-password-123'))
            accounts.post(signup,'/api/auth/signup');self.assertEqual(signup.status,200)
            member_cookie=next(v.split(';')[0] for k,v in signup.sent if k=='Set-Cookie')
            blocked=Handler({'name':'리도'},member_cookie);accounts.post(blocked,'/api/auth/profile');self.assertEqual(blocked.status,400)
            allowed=Handler({'name':'새이름'},member_cookie);accounts.post(allowed,'/api/auth/profile');self.assertEqual(allowed.status,200)
            self.assertFalse(accounts.current(Handler({},member_cookie))['is_admin'])
            anonymous=Handler({'name':'새이름'});accounts.post(anonymous,'/api/auth/profile');self.assertEqual(anonymous.status,401)

    def test_revival_survives_cookie_clear_and_stays_per_ip(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(accounts.database,'DB',Path(directory)/'test.sqlite3'):
            accounts.init()
            def request(ip='203.0.113.32',cookie='',origin='https://pastellive.co.kr'):
                h=Handler({},cookie,origin);h.headers.update({'Host':'pastellive.co.kr','CF-Ray':'test','CF-Connecting-IP':ip})
                h.local_request=lambda:False;h.public_auth_request=lambda:True
                return h
            self.assertTrue(accounts.site_enabled(request(cookie='after-sites=True')))
            self.assertFalse(accounts.visit(request(cookie='after-sites=True'))[0])
            bad=request(origin='https://evil.example');accounts.revive(bad);self.assertEqual(bad.status,403)
            h=request();accounts.revive(h);self.assertEqual(h.status,200)
            self.assertTrue(any(k=='Set-Cookie' and v.startswith('after-sites=True;') for k,v in h.sent))
            self.assertTrue(accounts.site_enabled(request()))
            flag,cookies=accounts.visit(request())
            self.assertTrue(flag)
            self.assertTrue(any(c.startswith('after-sites=True;') for c in cookies))
            self.assertTrue(accounts.site_enabled(request('203.0.113.33',cookie='after-sites=True')))
            self.assertFalse(accounts.visit(request('203.0.113.33',cookie='after-sites=True'))[0])

    def test_cloudflare_ip_and_local_visits(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(accounts.database,'DB',Path(directory)/'test.sqlite3'):
            accounts.init()
            for host in ['localhost','127.0.0.1:8081','[::1]:8081','pastellive.co.kr']:
                handler=Handler({});handler.headers['Host']=host
                self.assertFalse(accounts.visit(handler)[0])
            handler=Handler({});handler.headers.update({'Host':'pastellive.co.kr','CF-Ray':'test','CF-Connecting-IP':'2001:db8::12'})
            accounts.visit(handler)
            self.assertEqual(accounts.visitor_ip(handler),'2001:db8::12')
            with accounts.database.connect() as db:
                self.assertEqual(db.execute('SELECT ip FROM site_ip_states').fetchall()[0][0],'2001:db8::12')
                self.assertEqual(db.execute('SELECT COUNT(*) FROM site_visitors').fetchone()[0],1)
            for ip in ['127.0.0.1','::1','::ffff:127.0.0.1','invalid']:
                handler.headers['CF-Connecting-IP']=ip
                self.assertIsNone(accounts.visitor_ip(handler))

    def test_visitor_flag_is_persisted_and_not_trusted_from_client(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(accounts.database,'DB',Path(directory)/'test.sqlite3'):
            accounts.init()
            def visitor_handler(cookie):
                handler=Handler({},cookie);handler.client_address=('203.0.113.10',1);handler.headers['Host']='pastellive.co.kr';return handler
            flag,cookies=accounts.visit(visitor_handler('after_sites=True'))
            self.assertFalse(flag)
            self.assertTrue(any(value.startswith('after_sites=False;') for value in cookies))
            visitor=next(value.split(';')[0] for value in cookies if value.startswith('site_visitor='))
            flag,cookies=accounts.visit(visitor_handler(visitor+'; after_sites=True'))
            self.assertFalse(flag)
            with accounts.database.connect() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM site_visitors').fetchone()[0],1)
                db.execute('UPDATE site_ip_states SET after_sites=1')
            flag,cookies=accounts.visit(visitor_handler(visitor+'; after_sites=False'))
            self.assertTrue(flag)
            self.assertTrue(any(value.startswith('after_sites=True;') for value in cookies))

    def test_signup_login_logout_and_password_hash(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(accounts.database,'DB',Path(directory)/'test.sqlite3'):
            accounts.init();accounts.attempts.clear()
            data=dict(handle='test_member',name='테스트',password='a-long-test-password')
            signup=Handler(data);accounts.post(signup,'/api/auth/signup');self.assertEqual(signup.status,200)
            cookie=next(v for k,v in signup.sent if k=='Set-Cookie').split(';')[0]
            user=accounts.current(Handler({},cookie));self.assertEqual(user['name'],'테스트')
            self.assertNotEqual(user['password'],data['password']);self.assertNotIn('password',accounts.public(user))
            duplicate=Handler(data);accounts.post(duplicate,'/api/auth/signup');self.assertEqual(duplicate.status,409)
            bad=Handler(dict(handle='test_member',password='incorrect-password'));accounts.post(bad,'/api/auth/login');self.assertEqual(bad.status,401)
            login=Handler(data);accounts.post(login,'/api/auth/login');self.assertEqual(login.status,200)
            logout=Handler({},cookie);accounts.post(logout,'/api/auth/logout');self.assertIsNone(accounts.current(Handler({},cookie)))

    def test_csrf_rejection(self):
        handler=Handler({},origin='https://evil.example');accounts.post(handler,'/api/auth/signup');self.assertEqual(handler.status,403)

    def test_discord_first_password_setup(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(accounts.database,'DB',Path(directory)/'test.sqlite3'):
            accounts.init();accounts.attempts.clear()
            with accounts.database.connect() as db:
                db.execute("INSERT INTO users(id,handle,name,discord_id,created) VALUES('discord-user','discord_123456789012345678','회원','123456789012345678',0)")
            cookie=accounts.session('discord-user').split(';')[0]
            self.assertTrue(accounts.public(accounts.current(Handler({},cookie)))['needsPassword'])
            data=dict(handle='new_member',password='long-password-123',confirm='different')
            mismatch=Handler(data,cookie);accounts.post(mismatch,'/api/auth/password');self.assertEqual(mismatch.status,400)
            data['confirm']=data['password']
            anonymous=Handler(data);accounts.post(anonymous,'/api/auth/password');self.assertEqual(anonymous.status,401)
            setup=Handler(data,cookie);accounts.post(setup,'/api/auth/password');self.assertEqual(setup.status,200)
            self.assertIsNone(accounts.current(Handler({},cookie)))
            newcookie=next(v for k,v in setup.sent if k=='Set-Cookie').split(';')[0]
            self.assertFalse(accounts.public(accounts.current(Handler({},newcookie)))['needsPassword'])
            again=Handler(data,newcookie);accounts.post(again,'/api/auth/password');self.assertEqual(again.status,409)
            login=Handler(data);accounts.post(login,'/api/auth/login');self.assertEqual(login.status,200)

    def test_discord_disabled_without_configuration(self):
        with patch.object(accounts,'config',return_value={}):self.assertFalse(accounts.discord_ready())

    def test_local_and_public_discord_redirects(self):
        local=Handler({});local.headers['Host']='127.0.0.1'
        public=Handler({});public.headers['Host']='pastellive.co.kr'
        with patch.object(accounts,'config',return_value={}):
            self.assertEqual(accounts.redirect_uri(local),'http://127.0.0.1/auth/discord/callback')
            self.assertEqual(accounts.redirect_uri(public),'https://pastellive.co.kr/auth/discord/callback')
            self.assertEqual(accounts.landing(public),'/auth/discord/complete')


if __name__=='__main__':unittest.main()
