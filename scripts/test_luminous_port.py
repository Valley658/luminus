import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import accounts
import admin_operations
import database
import library
import comments
import pages
import luminous_port as p


class Request:
    def __init__(self,path='/',method='GET',data=None,cookie='',origin='http://127.0.0.1',host='127.0.0.1'):
        self.path=path;self.command=method;self.client_address=('127.0.0.1',1234)
        raw=json.dumps(data or {}).encode();self.rfile=io.BytesIO(raw);self.wfile=io.BytesIO();self.status=0;self.sent=[]
        self.headers={'Host':host,'Origin':origin,'Cookie':cookie,'Content-Type':'application/json','Content-Length':str(len(raw))}
    def send_response(self,status):self.status=status
    def send_header(self,k,v):self.sent.append((k,v))
    def end_headers(self):pass
    def send_error(self,status):self.send_response(status)
    def local_request(self):return self.headers['Host']=='127.0.0.1'
    def public_auth_request(self):return self.headers['Host']=='pastellive.co.kr'
    def admin_request(self):return self.headers['Host']=='admin.pastellive.co.kr'
    def json(self):return json.loads(self.wfile.getvalue())


class ImportedUITests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.dbpatch=patch.object(database,'DB',Path(self.folder.name)/'test.sqlite3');self.dbpatch.start()
        accounts.init();admin_operations.init();library.init();comments.init();pages.init();p.init();p.attempts.clear();accounts.attempts.clear()
        with database.connect() as db:
            for uid,name,adm in (('owner','관리자',1),('member','일반회원',0),('other','다른회원',0)):
                db.execute('INSERT INTO users(id,handle,name,password,created,is_admin) VALUES(?,?,?,?,?,?)',(uid,uid,name,accounts.password_hash('long-password-123'),0,adm))
            db.execute('INSERT INTO channels VALUES(?,?,?,?,?,?)',(15,'시즈키 토아','15.webp','','','UCtest'))
            db.execute('INSERT INTO videos VALUES(?,?,?,?,?,?,?,?)',('abcdefghijk','video','Test video','UCtest','시즈키 토아','https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg','2026-10-23',0))
        self.cookies={uid:accounts.session(uid).split(';')[0] for uid in ('owner','member','other')}

    def tearDown(self):self.dbpatch.stop();self.folder.cleanup()

    def request(self,path,method='GET',data=None,uid=None,**kw):
        h=Request(path,method,data,self.cookies.get(uid,''),**kw)
        self.assertTrue(p.handle(h),path);return h

    def test_home_and_every_copied_template_compile_with_assets(self):
        with patch.object(p,'videos',return_value=([],{})):
            h=self.request('/');self.assertEqual(h.status,200)
        html=h.wfile.getvalue().decode();self.assertIn('/static/css/style.css',html);self.assertIn('4기생',html);self.assertNotIn('{{',html)
        for name in ('profile.html','watch.html','watch_mobile.html','watch_shorts.html','admin_stats.html','m.html','channel.html','privacy.html','terms.html','credits.html','updates.html','code.html','reset_password.html','highlights.html','share.html'):
            h=Request('/')
            p.render(h,name,video_id='abcdefghijk',is_drive=False,page_title='Video',video_description='',init_videos=[],init_mobile_video_cards=[],channel_id='member',credits=[],github_url='',title='',description='',image='',share_url='',redirect_target='/')
            self.assertEqual(h.status,200,name)
        self.assertEqual(self.request('/static/css/style.css').status,200)
        self.assertEqual(self.request('/static/%2e%2e/%2e%2e/.env').status,404)
        self.assertEqual(self.request('/static/css/style.css',host='unknown.example').status,404)

    def test_watch_recommendations_include_real_short_classification(self):
        def feed(*args,**kw):
            short=kw.get('shorts',False)
            return [dict(id='short123456' if short else 'abcdefghijk',videoId='short123456' if short else 'abcdefghijk',is_short=short)],dict(hasMore=False,nextOffset=0)
        with patch.object(p,'videos',side_effect=feed):
            h=self.request('/api/videos/recommend_random')
        self.assertEqual(h.status,200)
        self.assertTrue(any(v['is_short'] for v in h.json()['videos']))
        self.assertTrue(any(not v['is_short'] for v in h.json()['videos']))
        h=self.request('/api/community/like','POST',dict(target_type='video',target_id='abcdefghijk',reaction='like'),uid='member')
        self.assertEqual(h.status,200);self.assertEqual(h.json()['like_count'],1)

    def test_live_status_uses_cached_name_map(self):
        with patch('luminous_live.status',return_value={'토아':True}):
            h=self.request('/api/live_status')
        self.assertIs(h.json()['토아'],True)

    def test_login_alias_uses_existing_hashed_sessions(self):
        h=self.request('/api/login','POST',{'login_id':'member','password':'long-password-123'})
        self.assertEqual(h.status,200);self.assertTrue(h.json()['success'])
        cookie=next(v.split(';')[0] for k,v in h.sent if k=='Set-Cookie')
        h=Request('/api/me',cookie=cookie);p.handle(h);self.assertTrue(h.json()['logged_in']);self.assertEqual(h.json()['nickname'],'일반회원')

    def test_write_csrf_and_get_method_do_not_delete_data(self):
        self.request('/api/history/add','POST',{'videoId':'abcdefghijk'},'member')
        self.assertEqual(self.request('/api/history/clear',uid='member').status,405)
        self.assertEqual(self.request('/api/history/clear','POST',{},'member',origin='https://evil.example').status,403)
        self.assertEqual(len(self.request('/api/history',uid='member').json()['videos']),1)

    def test_bookmarks_and_playlists_keep_user_ownership(self):
        saved=self.request('/api/bookmarks','POST',{'videoId':'abcdefghijk'},'member');self.assertTrue(saved.json()['bookmarked'])
        self.assertEqual(self.request('/api/bookmarks',uid='other').json()['bookmarks'],[])
        pid=self.request('/api/playlists','POST',{'title':'My list'},'member').json()['playlist_id']
        self.assertEqual(self.request('/api/playlists/'+pid+'/videos','POST',{'videoId':'abcdefghijk'},'other').status,403)
        self.assertEqual(self.request('/api/playlists/'+pid+'/videos','POST',{'videoId':'abcdefghijk'},'member').status,200)
        self.assertEqual(len(self.request('/api/playlists/'+pid+'/videos',uid='member').json()['videos']),1)

    def test_attendance_can_only_award_once_per_korean_day(self):
        h=self.request('/api/attendance/mark','POST',{},'member');self.assertEqual(h.status,200);self.assertEqual(h.json()['points_earned'],5)
        self.assertEqual(self.request('/api/attendance/mark','POST',{},'member').status,409)
        self.assertEqual(self.request('/api/points/my',uid='member').json()['points'],5)

    def test_community_and_comment_reactions_survive_requests(self):
        rid=self.request('/api/community/post','POST',{'member_name':'토아','content':'Hello'},'member').json()['id']
        self.assertEqual(self.request('/api/community/like','POST',{'post_id':rid},'other').status,200)
        rows=self.request('/api/community/토아/posts').json()['posts'];self.assertEqual(rows[0]['likes'],1)
        cid=self.request('/api/community/comments/'+rid,'POST',{'content':'Reply'},'other').json()['id']
        self.assertEqual(self.request('/api/community/comments/'+cid+'/delete','POST',{},'member').status,403)
        self.assertEqual(self.request('/api/community/comments/'+cid+'/delete','POST',{},'other').status,200)
        self.assertEqual(len(self.request('/api/notifications',uid='member').json()['notifications']),1)

    def test_schedule_and_admin_operations_require_db_admin_flag(self):
        payload={'member_name':'토아','event_date':'2026-10-23','event_time':'21:00','title':'Live'}
        self.assertEqual(self.request('/api/schedules','POST',payload,'member').status,403)
        self.assertEqual(self.request('/api/schedules','POST',payload,'owner').status,200)
        self.assertEqual(len(self.request('/api/schedules?start=2026-10-23').json()['schedules']),1)
        self.assertEqual(self.request('/api/admin/stats',uid='member').status,403)
        self.assertEqual(self.request('/api/admin/stats',uid='owner').json()['totals']['total_users'],3)
        self.assertEqual(self.request('/api/admin/users/owner','DELETE',{},'owner').status,403)
        self.assertEqual(self.request('/api/admin/logs/reset',uid='owner').status,405)

    def test_admin_name_and_reserved_names_remain_protected(self):
        self.assertEqual(self.request('/api/me/nickname','POST',{'nickname':'changed'},'owner').status,403)
        self.assertEqual(self.request('/api/me/nickname','POST',{'nickname':'리도'},'member').status,400)

    def test_gif_avatar_preserves_animation_and_multipart_korean(self):
        a=Image.new('RGB',(8,8),'red');b=Image.new('RGB',(8,8),'blue');out=io.BytesIO();a.save(out,format='GIF',save_all=True,append_images=[b],duration=100,loop=0)
        raw=out.getvalue();boundary='test-boundary';body=(f'--{boundary}\r\nContent-Disposition: form-data; name="nickname"\r\n\r\n일반회원\r\n--{boundary}\r\nContent-Disposition: form-data; name="picture"; filename="avatar.gif"\r\nContent-Type: image/gif\r\n\r\n').encode()+raw+f'\r\n--{boundary}--\r\n'.encode()
        h=Request('/api/me/profile','POST',cookie=self.cookies['member']);h.rfile=io.BytesIO(body);h.headers.update({'Content-Type':'multipart/form-data; boundary='+boundary,'Content-Length':str(len(body))});p.handle(h);self.assertEqual(h.status,200)
        h=self.request('/api/member-avatar?user=member');self.assertEqual(h.wfile.getvalue(),raw)
        with Image.open(io.BytesIO(h.wfile.getvalue())) as image:self.assertEqual(image.n_frames,2)

    def test_removed_email_and_password_recovery_endpoints(self):
        for path in ('/reset-password','/account/verify-email?token=old','/api/me/email','/api/account/forgot-password','/api/account/reset-password'):
            for method in ('GET','POST'):
                self.assertEqual(self.request(path,method,{'email':'test@example.com'},uid='member').status,410)
        draft=(p.ROOT/'drafts/index2.html').read_text(encoding='utf8')
        self.assertNotIn('db_reset_notice_v1',draft)
        js=(p.ROOT/'frontend/luminus/imported/js/script.js').read_text(encoding='utf8')
        self.assertNotIn('/reset-password',js)
        self.assertNotIn('/api/me/email',js)

    def test_discord_login_and_explicit_link_have_separate_states(self):
        with patch.object(accounts,'discord_ready',return_value=True),patch.object(accounts,'config',return_value={'DISCORD_CLIENT_ID':'test'}):
            self.request('/login/discord',uid='member')
            with database.connect() as db:row=db.execute('SELECT user_id FROM oauth_states').fetchone()
            self.assertIsNone(row['user_id'])
            with database.connect() as db:db.execute('DELETE FROM oauth_states')
            self.request('/login/discord?intent=link',uid='member')
            with database.connect() as db:row=db.execute('SELECT user_id FROM oauth_states').fetchone()
            self.assertEqual(row['user_id'],'member')

    def test_source_endpoint_denies_env_and_private_ops(self):
        for name in ('.env','AGENTS.md','scripts/sync-vm.py','../.env'):
            self.assertEqual(self.request('/api/code/file?path='+name).status,404)


if __name__=='__main__':unittest.main()
