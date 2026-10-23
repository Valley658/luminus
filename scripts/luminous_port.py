"""Python implementation of the copied luminous-master UI and HTTP contracts.

The Go tree is reference material only. Accounts, sessions, video caches and
library data continue to use the existing Python/MySQL implementation.
"""
import base64
import datetime as dt
import hashlib
import html
import io
import json
import mimetypes
import re
import secrets
import threading
import time
from email import policy
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlsplit
from jinja2 import Environment, FileSystemLoader, ChainableUndefined, select_autoescape
import accounts
import database
import library
import comments
import pages
import channel_details
import youtube_shorts
from channel_catalog import GROUPS

ROOT = database.ROOT
KST = dt.timezone(dt.timedelta(hours=9))
mutex = threading.RLock()
ENV = Environment(loader=FileSystemLoader([str(ROOT/'templates/luminus/imported'), str(ROOT/'templates/luminus')]),
                  autoescape=select_autoescape(['html']), undefined=ChainableUndefined)


class RequestError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def init():
    with database.connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS luminous_records(id TEXT PRIMARY KEY,kind TEXT NOT NULL,owner TEXT NOT NULL,parent TEXT NOT NULL,payload LONGTEXT NOT NULL,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS luminous_reactions(id TEXT PRIMARY KEY,target TEXT NOT NULL,user_id TEXT NOT NULL,action TEXT NOT NULL,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS luminous_attendance(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,day TEXT NOT NULL,points INTEGER NOT NULL,consecutive INTEGER NOT NULL,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS luminous_settings(user_id TEXT PRIMARY KEY,email TEXT NOT NULL DEFAULT '',email_verified INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS luminous_points(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,action TEXT NOT NULL,points INTEGER NOT NULL,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS luminous_images(id TEXT PRIMARY KEY,image BLOB NOT NULL,content_type TEXT NOT NULL);
        ''')


def reply(h, data=None, status=200, **extra):
    return accounts.reply(h, dict(success=True, **(data or {}), **extra), status)


def fail(h, error):
    return accounts.reply(h, dict(success=False, error=error.message, message=error.message), error.status)


def require(user, admin=False):
    if not user: raise RequestError('로그인 후 이용해 주세요.', 401)
    if admin and not user.get('is_admin'): raise RequestError('관리자 권한이 필요합니다.', 403)
    return user['id']


def text(value, maximum=2000, required=True):
    value=str(value or '').strip()
    if (required and not value) or len(value)>maximum or re.search(r'[<>\x00-\x08\x0b\x0c\x0e-\x1f]',value):
        raise RequestError('내용의 길이와 사용할 수 없는 문자를 확인해 주세요.')
    if accounts.banned_name(value): raise RequestError('사용할 수 없는 단어가 포함되어 있습니다.')
    return value


def number(value, default=0, limit=100000):
    try: return max(0,min(limit,int(value)))
    except (TypeError,ValueError): return default


def iso(value):
    return dt.datetime.fromtimestamp(float(value),KST).strftime('%Y-%m-%d %H:%M:%S')


def record(kind, owner='', parent='', data=None, rid=None):
    rid=rid or secrets.token_hex(16)
    with database.connect() as db:
        db.execute('INSERT OR REPLACE INTO luminous_records VALUES(?,?,?,?,?,?)',
                   (rid,kind,owner,parent,json.dumps(data or {},ensure_ascii=False),time.time()))
    return rid


def records(kind, parent=None, owner=None, limit=100, offset=0):
    where=['kind=?'];args=[kind]
    if parent is not None:where.append('parent=?');args.append(parent)
    if owner is not None:where.append('owner=?');args.append(owner)
    with database.connect() as db:
        rows=db.execute('SELECT * FROM luminous_records WHERE '+' AND '.join(where)+' ORDER BY created DESC,id DESC LIMIT ? OFFSET ?',(*args,limit,offset)).fetchall()
    return [dict(json.loads(r['payload']),id=r['id'],user_id=r['owner'],parent=r['parent'],date=iso(r['created']),created_at=iso(r['created'])) for r in rows]


def item(rid, kind=None):
    with database.connect() as db:
        r=db.execute('SELECT * FROM luminous_records WHERE id=?',(rid,)).fetchone()
    if not r or (kind and r['kind']!=kind):raise RequestError('항목이 없습니다.',404)
    return dict(json.loads(r['payload']),id=r['id'],user_id=r['owner'],parent=r['parent'],kind=r['kind'])


def remove(rid,user,kind=None):
    require(user);r=item(rid,kind)
    if r['user_id']!=user['id'] and not user.get('is_admin'):raise RequestError('본인의 항목만 삭제할 수 있습니다.',403)
    with database.connect() as db:
        db.execute('DELETE FROM luminous_records WHERE id=?',(rid,))
        db.execute('DELETE FROM luminous_records WHERE parent=?',(rid,))
        db.execute('DELETE FROM luminous_reactions WHERE target=?',(rid,))


def user_info(user):
    if not user:return dict(logged_in=False,discord_link_success=False,discord_flash_error=None)
    with database.connect() as db:
        s=db.execute('SELECT * FROM luminous_settings WHERE user_id=?',(user['id'],)).fetchone()
        staff=db.execute('SELECT 1 FROM user_staff WHERE user_id=?',(user['id'],)).fetchone()
    avatar=user['avatar']
    if avatar.startswith('/api/auth/avatar'):avatar='/api/member-avatar?user='+user['id']
    return dict(logged_in=True,id=user['id'],user_id=user['id'],login_id=user['handle'],nickname=html.escape(user['name']),picture=avatar,
                email=s['email'] if s else None,needs_setup=False,needs_migration=not bool(user['password']),
                discord_linked=bool(user['discord_id']),discord_username=user.get('discord_username',''),
                is_admin=bool(user.get('is_admin')),is_staff=bool(staff or user.get('is_admin')),discord_link_success=False,discord_flash_error=None)


class Capture:
    """Call existing handlers without changing their security checks or session format."""
    def __init__(self,h,data=None):
        self.headers=dict(h.headers);self.client_address=h.client_address;self.path=h.path;self.command=h.command
        raw=json.dumps(data or {}).encode();self.headers['Content-Length']=str(len(raw));self.rfile=io.BytesIO(raw)
        self.wfile=io.BytesIO();self.sent=[];self.status=200
    def send_response(self,status):self.status=status
    def send_header(self,k,v):self.sent.append((k,v))
    def end_headers(self):pass


def forward(h,function,data=None):
    c=Capture(h,data);function(c)
    try:result=json.loads(c.wfile.getvalue())
    except ValueError:result={}
    result.update(success=c.status<400)
    if 'error' in result:result['message']=result['error']
    return accounts.reply(h,result,c.status,cookies=[v for k,v in c.sent if k=='Set-Cookie'])


def read_body(h):
    try:length=int(h.headers.get('Content-Length','0'))
    except ValueError:raise RequestError('잘못된 요청입니다.')
    if not 0<=length<=7500000:raise RequestError('최대 업로드 용량을 초과했습니다.',413)
    raw=h.rfile.read(length);ctype=h.headers.get('Content-Type','')
    if ctype.startswith('multipart/form-data'):
        message=BytesParser(policy=policy.default).parsebytes(('Content-Type: '+ctype+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+raw)
        if not message.is_multipart():raise RequestError('업로드 형식을 확인해 주세요.')
        result={}
        for i,part in enumerate(message.iter_parts()):
            if i>=20:raise RequestError('업로드 항목이 너무 많습니다.')
            name=part.get_param('name',header='content-disposition')
            if name:
                value=part.get_payload(decode=True)
                try:
                    value=value if part.get_filename() else value.decode('utf-8')
                    if name in result:result[name]=([*result[name],value] if isinstance(result[name],list) else [result[name],value])
                    else:result[name]=value
                except UnicodeError:raise RequestError('입력 문자의 인코딩을 확인해 주세요.')
        return result
    if 'application/x-www-form-urlencoded' in ctype or (ctype.startswith('text/plain') and not raw.startswith(b'{')):
        return {k:v[0] for k,v in parse_qs(raw.decode()).items()}
    try:result=json.loads(raw or b'{}')
    except (ValueError,UnicodeError):raise RequestError('입력 형식을 확인해 주세요.')
    if not isinstance(result,dict):raise RequestError('입력 형식을 확인해 주세요.')
    return result


def videos(channel='',query='',shorts=False,offset=0,seed='',tab='all',sort='random'):
    if shorts:data=youtube_shorts.shorts_page(channel,offset,seed)
    else:data=youtube_shorts.video_feed(seed or secrets.token_hex(8),offset,channel,query,tab,tab in ('all','songs'),sort)
    return [video(v) for v in data['items']],data


def video(v):
    import image_cache
    return dict(v,thumbnail=image_cache.thumbnail(v.get('thumbnail','')),videoId=v['id'],is_short=v.get('kind')=='shorts',is_drive=False,
                member_name=v.get('channelName','').split(' ')[-1],viewCount=v.get('viewCount',0),comment_preview=None)


def context(h):
    from luminous_live import CHANNELS
    profiles={p['position']:p for p in database.profiles()};members=[];groups=[];names={};images={}
    with database.connect() as db:
        channel_photos={r['channel_id']:r['photo'] for r in db.execute('SELECT channel_id,photo FROM channel_details') if r['photo'].startswith('https://')}
    for group,rows in GROUPS:
        entries=[]
        for pos,name in rows:
            p=profiles.get(pos)
            if not p:continue
            short=name.split(' ')[-1];m=dict(name=short,img=channel_photos.get(p['youtube_channel_id']) or '/profiles/'+p['photo'],chzzkId=CHANNELS.get(short,''),position=pos)
            names[short]=name;images[short]=m['img'];entries.append(m);members.append(m)
        if group and '기생' in group and entries:
            parts=group.split(' · ');groups.append(dict(key=parts[0],unit_label=parts[-1],members=entries,img=entries[0]['img']))
    top=[m for pos in (13,1) for m in members if m['position']==pos];bottom=[]
    return dict(MEMBER_FULL_NAMES=names,MEMBER_ACCENT_COLORS={},MEMBER_IMAGES=images,MEMBER_MUSIC={},
                GENERATION_REP_IMAGE_OVERRIDES={},sidebar_members=members,sidebar_generations=groups,
                sidebar_ungrouped_top=top,sidebar_ungrouped_bottom=bottom,request=dict(path=urlsplit(h.path).path),
                user=user_info(accounts.current(h)),attendance_infinite=False)


def versioned_static(name):
    p=ROOT/'static/luminus/imported'/name
    return '/static/'+name+('?v='+str(p.stat().st_mtime_ns) if p.is_file() else '')


def render(h,name,**data):
    ctx=context(h);ctx.update(data)
    ctx.update(versioned_static=versioned_static,url_for=lambda endpoint,filename='',**kw:('/static/'+filename) if endpoint=='static' else '')
    template=ENV.get_template(name)
    members=ctx['sidebar_members']+[dict(name=g['key'],img=g['img'],chzzkId='') for g in ctx['sidebar_generations']]
    bootstrap=json.dumps(members,ensure_ascii=False).replace('<','\\u003c')
    body=template.render(**ctx).replace('</head>','<script>window.LUMINOUS_SIDEBAR_MEMBERS='+bootstrap+';</script></head>')
    body=body.replace('</body>', '<script src="'+versioned_static('js/python-port.js')+'" defer></script></body>').encode()
    h.send_response(200);h.send_header('Content-Type','text/html; charset=utf-8');h.send_header('Content-Length',str(len(body)))
    h.send_header('Cache-Control','private, no-store');h.send_header('X-Content-Type-Options','nosniff');h.end_headers();h.wfile.write(body)


def static(h,path):
    root=(ROOT/'static/luminus/imported').resolve();rel=unquote(path.removeprefix('/static/'))
    p=(root/rel).resolve()
    if not p.is_relative_to(root) or not p.is_file() or any(part.startswith('.') for part in Path(rel).parts):raise RequestError('파일이 없습니다.',404)
    if p.suffix.lower() not in ('.css','.js','.woff','.woff2','.ttf','.otf','.png','.webp','.jpg','.jpeg','.gif','.svg','.ico','.json','.wasm','.mp3','.ogg','.wav','.m4a'):raise RequestError('파일이 없습니다.',404)
    data=p.read_bytes();etag='"'+hashlib.sha256(data).hexdigest()+'"'
    h.send_response(304 if h.headers.get('If-None-Match')==etag else 200)
    h.send_header('Content-Type',mimetypes.guess_type(p.name)[0] or 'application/octet-stream');h.send_header('ETag',etag)
    h.send_header('X-Content-Type-Options','nosniff');h.send_header('Cache-Control','public, max-age=3600')
    if h.headers.get('If-None-Match')!=etag:h.send_header('Content-Length',str(len(data)))
    h.end_headers()
    if h.headers.get('If-None-Match')!=etag:h.wfile.write(data)


PAGE_PATHS={'/','/index.html','/index2.html','/profile','/mypage','/gallery','/highlights','/history','/bookmarks','/playlists',
            '/schedule','/comments','/privacy','/terms','/credits','/updates','/code','/reports','/reset-password','/admin/stats'}
API_PREFIXES=('/api/community/','/api/fanart','/api/highlights','/api/history','/api/bookmarks','/api/playlists',
              '/api/attendance/','/api/points/','/api/profile/','/api/notifications','/api/schedules','/api/cheers/',
              '/api/channel/','/api/search','/api/inquiries','/api/reports','/api/code/','/api/account/',
              '/api/me/','/api/video/','/api/youtube_comments/','/api/video_storyboard/','/api/comment_preview',
              '/api/drive_','/api/kirinuki/')
API_NAMES={'/api/me','/api/login','/api/register','/api/staff-roles','/api/leaderboard','/api/all_comments','/api/delete_comments',
           '/api/live_status','/api/m/shorts','/api/videos/home_shorts','/api/videos/random_scroll','/api/videos/recommend_random',
           '/api/member-avatar','/api/content-image','/api/image-cache','/api/csp-report'}


def handles(path):
    return (path in PAGE_PATHS or path in API_NAMES or path.startswith(API_PREFIXES) or path.startswith('/static/')
            or path.startswith(('/watch/','/channel/','/share/')) or path in ('/watch','/sw.js','/robots.txt','/sitemap.xml','/logout','/login/discord','/login/discord/callback','/account/verify-email')
            or re.fullmatch(r'/api/videos/[^/]+',path) or re.fullmatch(r'/api/comments/[^/]+(?:/react)?',path)
            or path.startswith('/api/admin/') or re.fullmatch(r'/[^/]+/community',path))


attempts={}
def handle(h):
    """Return False only for routes still implemented by the original Python server."""
    path=unquote(urlsplit(h.path).path)
    if not handles(path):return False
    if not (h.local_request() or h.public_auth_request() or h.admin_request()):h.send_error(404);return True
    if not __import__('admin_operations').observe(h,path):return True
    if hasattr(h,'maintenance_response') and h.maintenance_response(path):return True
    try:
        if path in ('/reset-password','/account/verify-email','/api/me/email','/api/account/forgot-password','/api/account/reset-password'):
            raise RequestError('제공하지 않는 기능입니다.',410)
        if path=='/static/sw.js':raise RequestError('서비스 워커는 /sw.js에서 제공됩니다.',404)
        if path.startswith('/static/'):static(h,path);return True
        q={k:v[0] for k,v in parse_qs(urlsplit(h.path).query).items()};user=accounts.current(h)
        if h.command not in ('GET','HEAD'):
            host=h.headers.get('Host','').lower()
            expected='https://'+host if host in ('pastellive.co.kr','admin.pastellive.co.kr') else 'http://'+host
            if h.headers.get('Origin')!=expected:raise RequestError('허용되지 않은 요청입니다.',403)
            who=accounts.visitor_ip(h) or h.client_address[0];now=time.monotonic()
            with mutex:
                recent=[t for t in attempts.get(who,[]) if now-t<60]
                if len(recent)>=60:raise RequestError('요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.',429)
                if len(attempts)>4096:attempts.clear()
                attempts[who]=recent+[now]
            data=read_body(h)
        else:data={}
        if path.startswith('/api/'):
            from luminous_dispatch import api
            api(h,path,user,data,q);return True
        if path in ('/login/discord','/login/discord/callback'):
            (accounts.oauth_start if path=='/login/discord' else accounts.oauth_callback)(h);return True
        if path=='/account/verify-email':
            from luminous_mail import verify_email
            verify_email(h,q.get('token',''));return True
        if path=='/logout':
            # Logout still validates Origin through the existing POST endpoint;
            # the copied UI is patched to use that endpoint rather than a GET.
            raise RequestError('프로필 메뉴의 로그아웃을 이용해 주세요.',405)
        if path=='/robots.txt':
            body=b'User-agent: *\nDisallow: /api/\nDisallow: /admin/\nDisallow: /profile\n';send_bytes(h,body,'text/plain');return True
        if path=='/sitemap.xml':send_bytes(h,b'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://pastellive.co.kr/</loc></url></urlset>','application/xml');return True
        if path=='/sw.js':
            # Do not let an imported service worker cache sessions or stale APIs.
            send_bytes(h,b"self.addEventListener('install',()=>self.skipWaiting());self.addEventListener('activate',e=>e.waitUntil(Promise.all([caches.keys().then(k=>Promise.all(k.map(n=>caches.delete(n)))),self.registration.unregister()])));",'text/javascript');return True
        if path.startswith('/share/'):
            parts=path.split('/');vid=parts[-1]
            with database.connect() as db:r=db.execute('SELECT * FROM videos WHERE id=?',(vid,)).fetchone()
            if not r:raise RequestError('영상이 없습니다.',404)
            render(h,'share.html',title=r['title'],description=r['channelName'],image=r['thumbnail'],share_url=h.path,redirect_target='/watch?v='+vid);return True
        if path in ('/admin/stats',) or (h.admin_request() and path in ('/','/index.html')):
            require(user,True);render(h,'admin_stats.html');return True
        if path.startswith('/watch/') or path=='/watch':
            vid=path.split('/')[2] if path.startswith('/watch/') else q.get('v','')
            drive=bool(re.fullmatch(r'[A-Za-z0-9_-]{20,200}',vid))
            if not drive and not re.fullmatch(r'[A-Za-z0-9_-]{11}',vid):raise RequestError('잘못된 영상입니다.',404)
            with database.connect() as db:v=db.execute('SELECT * FROM videos WHERE id=?',(vid,)).fetchone()
            short=q.get('type')=='shorts' or bool(v and v['kind']=='shorts')
            mobile=any(k in h.headers.get('User-Agent','').lower() for k in ('mobile','android','iphone'))
            name='watch_shorts.html' if short else 'watch_mobile.html' if mobile else 'watch.html'
            channel=channel_details.get(v['channelId']) if v else {}
            count=channel.get('subscribers')
            label=('구독자 '+(str(round(count/10000,1)).removesuffix('.0')+'만' if count>=10000 else f'{count:,}')+'명') if count is not None else '구독자 수 비공개'
            render(h,name,video_id=vid,is_drive=drive,video_title=v['title'] if v else '영상',channel_info=channel,subscriber_label=label,
                   page_title=(v['title'] if v else '영상')+' - 루미너스',video_description=v['title'] if v else '')
            if not drive:comments.youtube(vid,'')
            return True
        if path in ('/profile','/mypage'):
            if not user:accounts.redirect(h,'/');return True
            render(h,'profile.html');return True
        if path.startswith('/channel/'):
            render(h,'channel.html',channel_id=path.rsplit('/',1)[-1]);return True
        templates={'/privacy':'privacy.html','/terms':'terms.html','/credits':'credits.html','/updates':'updates.html',
                   '/code':'code.html','/reset-password':'reset_password.html','/highlights':'highlights.html'}
        if path in templates:
            render(h,templates[path],github_url='https://github.com/Valley658/luminus',credits=[]);return True
        # The original home template already includes the history, playlist,
        # fan-art and schedule panels; its JavaScript opens them in place.
        initial,_=videos(seed=secrets.token_hex(10));shorts,_=videos(shorts=True,seed=secrets.token_hex(10))
        render(h,'index2.html',init_videos=initial,init_home_shorts=shorts[:15],init_mobile_video_cards=initial[:10])
        return True
    except RequestError as error:fail(h,error);return True
    except library.LibraryError as error:fail(h,RequestError(error.message,error.status));return True


def send_bytes(h,data,ctype):
    h.send_response(200);h.send_header('Content-Type',ctype+'; charset=utf-8');h.send_header('Content-Length',str(len(data)));h.send_header('Cache-Control','no-store');h.send_header('X-Content-Type-Options','nosniff');h.end_headers();h.wfile.write(data)
