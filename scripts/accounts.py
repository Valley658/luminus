"""SQLite accounts, password hashes, sessions and Discord authorization-code flow."""
import hashlib
import io
import ipaddress
import unicodedata
import base64
import hmac
import json
import re
import secrets
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from http.cookies import SimpleCookie
import database

lock = threading.Lock()
attempts = {}


RESERVED_NAME_MESSAGE='리도는 관리자 전용 닉네임입니다. 회원가입 및 이름 변경에 사용할 수 없습니다.'


def reserved_name(name):
    return unicodedata.normalize('NFKC',name).strip()=='리도'


def init():
    with database.connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS site_ip_states(ip TEXT PRIMARY KEY,after_sites INTEGER NOT NULL DEFAULT 0 CHECK(after_sites IN (0,1)),created REAL NOT NULL,last_seen REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS site_visitors(token_hash TEXT PRIMARY KEY,after_sites INTEGER NOT NULL DEFAULT 0 CHECK(after_sites IN (0,1)),created REAL NOT NULL,last_seen REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, handle TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
          password TEXT, avatar TEXT NOT NULL DEFAULT '', discord_id TEXT UNIQUE, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id TEXT NOT NULL,expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS user_avatars(user_id TEXT PRIMARY KEY,image BLOB NOT NULL);
        CREATE TABLE IF NOT EXISTS oauth_states(token TEXT PRIMARY KEY,binding TEXT NOT NULL,user_id TEXT,expires REAL NOT NULL);
        ''')
        if 'is_admin' not in [r[1] for r in db.execute('PRAGMA table_info(users)')]:
            db.execute('ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0')
        db.execute('CREATE TABLE IF NOT EXISTS account_migrations(name TEXT PRIMARY KEY)')
        if not db.execute("SELECT 1 FROM account_migrations WHERE name='reserve-rido'").fetchone():
            for row in db.execute('SELECT id,name FROM users').fetchall():
                if reserved_name(row['name']):db.execute('UPDATE users SET is_admin=1 WHERE id=?',(row['id'],))
            db.execute("INSERT INTO account_migrations VALUES('reserve-rido')")
        for column in ('discord_username','discord_name'):
            if column not in [r[1] for r in db.execute('PRAGMA table_info(users)')]:
                db.execute(f"ALTER TABLE users ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        if 'ip' not in [r[1] for r in db.execute('PRAGMA table_info(site_visitors)')]:
            db.execute("ALTER TABLE site_visitors ADD COLUMN ip TEXT NOT NULL DEFAULT ''")
        if 'redirect_uri' not in [r[1] for r in db.execute('PRAGMA table_info(oauth_states)')]:
            db.execute("ALTER TABLE oauth_states ADD COLUMN redirect_uri TEXT NOT NULL DEFAULT ''")
        if 'content_type' not in [r[1] for r in db.execute('PRAGMA table_info(user_avatars)')]:
            db.execute("ALTER TABLE user_avatars ADD COLUMN content_type TEXT NOT NULL DEFAULT 'image/jpeg'")
        if 'suspended_until' not in [r[1] for r in db.execute('PRAGMA table_info(users)')]:
            db.execute('ALTER TABLE users ADD COLUMN suspended_until REAL NOT NULL DEFAULT 0')


def config():
    values={}
    try:
        for line in (database.ROOT/'.env').read_text(encoding='utf-8-sig').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k,v=line.split('=',1);values[k.strip()]=v.strip().strip('"\'')
    except OSError:pass
    return values


def public_origin():
    return 'https://pastellive.co.kr'


def redirect_uri(handler=None):
    c=config()
    if handler and handler.headers.get('Host','').lower()=='pastellive.co.kr':
        return c.get('DISCORD_PUBLIC_REDIRECT_URI') or public_origin()+'/auth/discord/callback'
    return c.get('DISCORD_LOCAL_REDIRECT_URI') or 'http://127.0.0.1/auth/discord/callback'


def landing(handler):
    return '/auth/discord/complete' if handler.headers.get('Host','').lower()=='pastellive.co.kr' else '/index2.html'


def discord_ready():
    c=config()
    return all(c.get(k) for k in ('DISCORD_CLIENT_ID','DISCORD_CLIENT_SECRET'))


def cookie(handler,name):
    try:
        jar=SimpleCookie();jar.load(handler.headers.get('Cookie',''));return jar[name].value if name in jar else ''
    except Exception:return ''


def digest(value):return hashlib.sha256(value.encode()).hexdigest()


def current(handler):
    token=session_token(handler)
    if not token:return None
    with database.connect() as db:
        row=db.execute('SELECT users.* FROM users JOIN sessions ON users.id=sessions.user_id WHERE token=? AND expires>?',(digest(token),time.time())).fetchone()
    return dict(row) if row and row['suspended_until']<=time.time() else None


def protected_name(name):
    if reserved_name(name):return True
    normalized=unicodedata.normalize('NFKC',name).strip()
    with database.connect() as db:
        return any(unicodedata.normalize('NFKC',row['name']).strip()==normalized for row in db.execute('SELECT name FROM users WHERE is_admin=1'))


def banned_name(name):
    try:
        with database.connect() as db:row=db.execute("SELECT value FROM site_settings WHERE name='bannedWords'").fetchone()
        words=json.loads(row[0]) if row else []
        return any(word.casefold() in name.casefold() for word in words)
    except Exception:
        return False


def session_token(handler):
    name='luminous_admin_session' if handler.headers.get('Host','').lower()=='admin.pastellive.co.kr' else 'pastellive_session'
    return cookie(handler,name)


def admin_session_cookie(handler, user):
    if not user or handler.headers.get('Host','').lower()!='pastellive.co.kr':return []
    token=session_token(handler)
    with database.connect() as db:
        row=db.execute('SELECT expires FROM sessions WHERE token=?',(digest(token),)).fetchone()
    if not row:return []
    lifetime=max(0,int(row[0]-time.time()))
    return ['luminous_admin_session='+token+'; Domain=.pastellive.co.kr; HttpOnly; SameSite=Lax; Path=/; Max-Age='+str(lifetime)]


def visitor_ip(handler):
    host=handler.headers.get('Host','').lower().split(':')[0]
    if host in ('localhost','127.0.0.1') or handler.headers.get('Host','').lower().startswith('[::1]'):
        return None
    try:
        peer=ipaddress.ip_address(handler.client_address[0])
        address=peer
        if peer.is_loopback:
            # Only accept the client address supplied through the site's Cloudflare tunnel.
            if host not in ('pastellive.co.kr','admin.pastellive.co.kr') or not handler.headers.get('CF-Ray'):return None
            address=ipaddress.ip_address(handler.headers.get('CF-Connecting-IP',''))
        if isinstance(address,ipaddress.IPv6Address) and address.ipv4_mapped:address=address.ipv4_mapped
        if address.is_loopback or address.is_unspecified or address.is_multicast:return None
        return str(address)
    except ValueError:return None


def visit(handler):
    """Persist the visitor-selected public release state, with an opaque browser ID."""
    address=visitor_ip(handler)
    secure='; Secure' if handler.headers.get('Host','').lower()=='pastellive.co.kr' else ''
    if address is None:
        flag=handler.local_request() and cookie(handler,'after-sites')=='True' if hasattr(handler,'local_request') else False
        value='True' if flag else 'False'
        return flag,[] if cookie(handler,'after-sites')==value else ['after-sites='+value+'; SameSite=Lax; Path=/; Max-Age=31536000'+secure]
    token=cookie(handler,'site_visitor');valid=bool(re.fullmatch(r'[A-Za-z0-9_-]{43}',token))
    now=time.time();fresh=False
    with database.connect() as db:
        db.execute('INSERT OR IGNORE INTO site_ip_states VALUES(?,0,?,?)',(address,now,now))
        flag=bool(db.execute('SELECT after_sites FROM site_ip_states WHERE ip=?',(address,)).fetchone()[0])
        db.execute('UPDATE site_ip_states SET last_seen=? WHERE ip=?',(now,address))
        row=db.execute('SELECT token_hash FROM site_visitors WHERE token_hash=?',(digest(token),)).fetchone() if valid else None
        if row is None:
            token=secrets.token_urlsafe(32);fresh=True
            db.execute('INSERT INTO site_visitors(token_hash,after_sites,created,last_seen,ip) VALUES(?,?,?,?,?)',(digest(token),int(flag),now,now,address))
        else:
            db.execute('UPDATE site_visitors SET after_sites=?,last_seen=?,ip=? WHERE token_hash=?',(int(flag),now,address,digest(token)))
    cookies=[]
    if fresh:cookies.append('site_visitor='+token+'; HttpOnly; SameSite=Lax; Path=/; Max-Age=31536000'+secure)
    value='True' if flag else 'False'
    for name in ('after-sites','after_sites'):
        if fresh or cookie(handler,name)!=value:cookies.append(name+'='+value+'; SameSite=Lax; Path=/; Max-Age=31536000'+secure)
    return flag,cookies


def site_enabled(handler):
    if hasattr(handler,'local_request') and handler.local_request():return True
    # Public homepage release explicitly requested on 2026-10-22.
    return bool(hasattr(handler,'public_auth_request') and handler.public_auth_request())


def revive(handler):
    origin=handler.headers.get('Origin','')
    if origin not in ('https://pastellive.co.kr','http://127.0.0.1','http://localhost','http://127.0.0.1:80','http://localhost:80','http://127.0.0.1:8081','http://localhost:8081'):
        return reply(handler,{'error':'허용되지 않은 요청입니다.'},403)
    try:
        size=int(handler.headers.get('Content-Length','0'))
        if not 0<=size<=1024:raise ValueError()
        handler.rfile.read(size)
    except ValueError:return reply(handler,{'error':'잘못된 요청입니다.'},400)
    address=visitor_ip(handler)
    if address:
        now=time.time()
        with database.connect() as db:
            db.execute('INSERT INTO site_ip_states VALUES(?,1,?,?) ON CONFLICT(ip) DO UPDATE SET after_sites=1,last_seen=excluded.last_seen',(address,now,now))
        flag,cookies=visit(handler)
    elif handler.local_request():
        flag=True;cookies=['after-sites=True; SameSite=Lax; Path=/; Max-Age=31536000','after_sites=True; SameSite=Lax; Path=/; Max-Age=31536000']
    else:return reply(handler,{'error':'접속 IP를 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.'},503)
    return reply(handler,{'ok':True,'after_sites':flag,'url':'/'},cookies=cookies)


def public(user):
    return dict(name=user['name'],handle='@'+user['handle'],avatar=user['avatar'],discordLinked=bool(user['discord_id']),discordAccount=(dict(id=user['discord_id'],username=user.get('discord_username',''),name=user.get('discord_name','')) if user['discord_id'] else None),needsPassword=not bool(user['password']),isAdmin=bool(user.get('is_admin',0))) if user else None


def session(user_id):
    token=secrets.token_urlsafe(32)
    with database.connect() as db:
        db.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
        db.execute('INSERT INTO sessions VALUES(?,?,?)',(digest(token),user_id,time.time()+86400*14))
    return 'pastellive_session='+token+'; HttpOnly; SameSite=Lax; Path=/; Max-Age=1209600'


def reply(handler,data,status=200,cookies=()):
    body=json.dumps(data,ensure_ascii=False).encode()
    handler.send_response(status)
    handler.send_header('Content-Type','application/json; charset=utf-8')
    handler.send_header('Cache-Control','no-store')
    handler.send_header('Content-Length',str(len(body)))
    for value in cookies:
        if handler.headers.get('Host','').lower() in ('pastellive.co.kr','admin.pastellive.co.kr'):value+='; Secure'
        handler.send_header('Set-Cookie',value)
    handler.end_headers();handler.wfile.write(body)


def password_hash(password,salt=None):
    salt=salt or secrets.token_hex(16)
    value=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),600000).hex()
    return salt+':'+value


def post(handler,path):
    origin=handler.headers.get('Origin','')
    if origin not in ('http://127.0.0.1','http://127.0.0.1:80','http://localhost','http://localhost:80','http://127.0.0.1:8081','http://localhost:8081',public_origin(),'https://admin.pastellive.co.kr'):
        return reply(handler,{'error':'허용되지 않은 요청입니다.'},403)
    try:
        length=int(handler.headers.get('Content-Length','0'))
        if length<0 or length>(7500000 if path=='/api/auth/avatar' else 8192):raise ValueError()
        data=json.loads(handler.rfile.read(length) or b'{}')
        if not isinstance(data,dict):raise ValueError()
    except (ValueError,TypeError):return reply(handler,{'error':'입력을 확인해 주세요.'},400)
    if path=='/api/auth/profile':
        user=current(handler)
        if not user:return reply(handler,{'error':'로그인이 필요합니다.'},401)
        if user.get('is_admin'):return reply(handler,{'error':'관리자 이름은 변경할 수 없습니다. 프로필 사진은 변경할 수 있습니다.'},403)
        name=str(data.get('name','')).strip()
        if not 1<=len(name)<=30:return reply(handler,{'error':'이름은 1~30자로 입력해 주세요.'},400)
        if protected_name(name):return reply(handler,{'error':RESERVED_NAME_MESSAGE},400)
        if banned_name(name):return reply(handler,{'error':'닉네임에 사용할 수 없는 단어가 포함되어 있습니다.'},400)
        with database.connect() as db:db.execute('UPDATE users SET name=? WHERE id=?',(name,user['id']))
        return reply(handler,{'ok':True})
    if path=='/api/auth/avatar':
        user=current(handler)
        if not user:return reply(handler,{'error':'로그인이 필요합니다.'},401)
        try:
            from PIL import Image
            image=base64.b64decode(data.get('image',''),validate=True)
            if not image or len(image)>5*1024*1024:raise ValueError()
            with Image.open(io.BytesIO(image)) as picture:
                content_type={'JPEG':'image/jpeg','PNG':'image/png','WEBP':'image/webp','GIF':'image/gif'}.get(picture.format)
                if not content_type or max(picture.size)>4096 or min(picture.size)<1:raise ValueError()
                pixels=0
                for frame in range(301):
                    try:picture.seek(frame)
                    except EOFError:break
                    pixels+=picture.width*picture.height
                    if frame>=300 or pixels>100000000:raise ValueError()
                    picture.load()
        except (ValueError,TypeError,OSError,Image.DecompressionBombError):return reply(handler,{'error':'JPG·PNG·WebP·GIF 사진을 선택해 주세요. 최대 5MB, 300프레임입니다.'},400)
        with database.connect() as db:
            db.execute('INSERT OR REPLACE INTO user_avatars(user_id,image,content_type) VALUES(?,?,?)',(user['id'],image,content_type))
            db.execute('UPDATE users SET avatar=? WHERE id=?',('/api/auth/avatar?v='+secrets.token_hex(5),user['id']))
        return reply(handler,{'ok':True})
    if path=='/api/auth/logout':
        with database.connect() as db:db.execute('DELETE FROM sessions WHERE token=?',(digest(session_token(handler)),))
        return reply(handler,{'ok':True},cookies=['pastellive_session=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax','luminous_admin_session=; Domain=.pastellive.co.kr; Max-Age=0; Path=/; HttpOnly; SameSite=Lax'])
    if path not in ('/api/auth/signup','/api/auth/login','/api/auth/password'):return reply(handler,{'error':'없는 요청입니다.'},404)
    handle=str(data.get('handle','')).strip().lower().lstrip('@');password=str(data.get('password',''))
    with lock:
        now=time.time();key=handler.client_address[0];recent=[t for t in attempts.get(key,[]) if now-t<300]
        if len(recent)>=20:return reply(handler,{'error':'잠시 후 다시 시도해 주세요.'},429)
        attempts[key]=recent+[now]
    if not re.fullmatch('[a-z0-9_]{3,24}',handle) or not 10<=len(password)<=128:
        return reply(handler,{'error':'아이디는 영문·숫자·밑줄 3~24자, 비밀번호는 10~128자로 입력해 주세요.'},400)
    with database.connect() as db:
        if path.endswith('password'):
            user=current(handler)
            if not user:return reply(handler,{'error':'로그인이 필요합니다.'},401)
            if user['password']:return reply(handler,{'error':'이미 비밀번호가 설정되어 있습니다.'},409)
            if password!=data.get('confirm'):return reply(handler,{'error':'비밀번호가 일치하지 않습니다.'},400)
            try:
                changed=db.execute('UPDATE users SET handle=?,password=? WHERE id=? AND password IS NULL',(handle,password_hash(password),user['id'])).rowcount
            except sqlite3.IntegrityError:return reply(handler,{'error':'이미 사용 중인 아이디입니다.'},409)
            if not changed:return reply(handler,{'error':'이미 비밀번호가 설정되어 있습니다.'},409)
            uid=user['id'];db.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
        elif path.endswith('signup'):
            name=str(data.get('name','')).strip()
            if not 1<=len(name)<=30:return reply(handler,{'error':'이름은 1~30자로 입력해 주세요.'},400)
            if protected_name(name):return reply(handler,{'error':RESERVED_NAME_MESSAGE},400)
            if banned_name(name):return reply(handler,{'error':'닉네임에 사용할 수 없는 단어가 포함되어 있습니다.'},400)
            uid=secrets.token_hex(16)
            try:db.execute('INSERT INTO users(id,handle,name,password,created) VALUES(?,?,?,?,?)',(uid,handle,name,password_hash(password),time.time()))
            except sqlite3.IntegrityError:return reply(handler,{'error':'이미 사용 중인 아이디입니다.'},409)
        else:
            row=db.execute('SELECT * FROM users WHERE handle=?',(handle,)).fetchone()
            stored=row['password'] if row and row['password'] else '0'*32+':'+'0'*64
            actual=password_hash(password,stored.split(':')[0])
            if not row or not row['password'] or not hmac.compare_digest(actual,stored):
                return reply(handler,{'error':'아이디 또는 비밀번호를 확인해 주세요.'},401)
            if row['suspended_until']>time.time():return reply(handler,{'error':'이용이 제한된 계정입니다.'},403)
            uid=row['id']
    return reply(handler,{'ok':True},cookies=[session(uid)])


def redirect(handler,url,cookies=()):
    handler.send_response(303);handler.send_header('Location',url);handler.send_header('Cache-Control','no-store')
    for value in cookies:
        if handler.headers.get('Host','').lower() in ('pastellive.co.kr','admin.pastellive.co.kr'):value+='; Secure'
        handler.send_header('Set-Cookie',value)
    handler.send_header('Content-Length','0');handler.end_headers()


def avatar_get(handler):
    user=current(handler)
    if not user:return handler.send_error(404)
    with database.connect() as db:row=db.execute('SELECT image,content_type FROM user_avatars WHERE user_id=?',(user['id'],)).fetchone()
    if not row:return handler.send_error(404)
    handler.send_response(200);handler.send_header('Content-Type',row['content_type']);handler.send_header('X-Content-Type-Options','nosniff')
    handler.send_header('Content-Length',str(len(row['image'])));handler.send_header('Cache-Control','no-store');handler.end_headers();handler.wfile.write(row['image'])


def oauth_start(handler):
    if not discord_ready():return redirect(handler,landing(handler)+'?auth_error=discord_setup')
    c=config();state=secrets.token_urlsafe(32);binding=secrets.token_urlsafe(32)
    intent=urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query).get('intent',['login'])[0]
    user=current(handler) if intent=='link' else None
    if intent=='link' and not user:return redirect(handler,landing(handler)+'?auth_error=login_required')
    with database.connect() as db:
        db.execute('DELETE FROM oauth_states WHERE expires<?',(time.time(),))
        db.execute('INSERT INTO oauth_states(token,binding,user_id,expires,redirect_uri) VALUES(?,?,?,?,?)',(digest(state),digest(binding),user['id'] if user else None,time.time()+600,redirect_uri(handler)))
    url='https://discord.com/oauth2/authorize?'+urllib.parse.urlencode(dict(client_id=c['DISCORD_CLIENT_ID'],redirect_uri=redirect_uri(handler),response_type='code',scope='identify',state=state))
    redirect(handler,url,['pastellive_oauth='+binding+'; HttpOnly; SameSite=Lax; Path=/; Max-Age=600'])


def oauth_callback(handler):
    p=urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query);state=p.get('state',[''])[0]
    with database.connect() as db:
        row=db.execute('SELECT * FROM oauth_states WHERE token=?',(digest(state),)).fetchone()
        db.execute('DELETE FROM oauth_states WHERE token=?',(digest(state),))
    if not row or row['redirect_uri']!=redirect_uri(handler) or row['expires']<time.time() or not hmac.compare_digest(row['binding'],digest(cookie(handler,'pastellive_oauth'))) or p.get('iss',['https://discord.com'])[0]!='https://discord.com':
        return redirect(handler,landing(handler)+'?auth_error=discord_failed')
    if not p.get('code'):return redirect(handler,landing(handler)+'?auth_error=discord_cancelled')
    try:
        c=config();payload=urllib.parse.urlencode(dict(client_id=c['DISCORD_CLIENT_ID'],client_secret=c['DISCORD_CLIENT_SECRET'],grant_type='authorization_code',code=p['code'][0],redirect_uri=row['redirect_uri'])).encode()
        req=urllib.request.Request('https://discord.com/api/oauth2/token',data=payload,headers={'Content-Type':'application/x-www-form-urlencoded','User-Agent':'Luminous/1.0 (https://pastellive.co.kr)','Accept':'application/json'})
        with urllib.request.urlopen(req,timeout=15) as response:token=json.load(response)['access_token']
        with urllib.request.urlopen(urllib.request.Request('https://discord.com/api/v10/users/@me',headers={'Authorization':'Bearer '+token,'User-Agent':'Luminous/1.0 (https://pastellive.co.kr)','Accept':'application/json'}),timeout=15) as response:identity=json.load(response)
        did=identity['id'];avatar=identity.get('avatar')
        if not re.fullmatch(r'\d+',did) or (avatar and not re.fullmatch(r'[a-zA-Z0-9_]+',avatar)):raise ValueError()
        extension='gif' if avatar and avatar.startswith('a_') else 'png'
        avatar_url=f'https://cdn.discordapp.com/avatars/{did}/{avatar}.{extension}?size=128' if avatar else ''
        with database.connect() as db:
            existing=db.execute('SELECT id,avatar,suspended_until FROM users WHERE discord_id=?',(did,)).fetchone()
            if existing and existing['suspended_until']>time.time():raise ValueError()
            uid=row['user_id']
            if uid:
                active=current(handler)
                if not active or active['id']!=uid or (existing and existing['id']!=uid):raise ValueError()
                if active['avatar'].startswith('/api/auth/avatar'):avatar_url=active['avatar']
                db.execute('UPDATE users SET discord_id=?,avatar=? WHERE id=?',(did,avatar_url,uid))
            elif existing:
                uid=existing['id']
                if existing['avatar'].startswith('/api/auth/avatar'):avatar_url=existing['avatar']
                db.execute('UPDATE users SET avatar=? WHERE id=?',(avatar_url,uid))
            else:
                uid=secrets.token_hex(16);name=str(identity.get('global_name') or identity['username'])[:30]
                if reserved_name(name):name='디스코드 회원'
                db.execute('INSERT INTO users(id,handle,name,avatar,discord_id,created) VALUES(?,?,?,?,?,?)',(uid,'discord_'+did,name,avatar_url,did,time.time()))
            db.execute('UPDATE users SET discord_username=?,discord_name=? WHERE id=?',(str(identity.get('username',''))[:100],str(identity.get('global_name') or identity.get('username',''))[:100],uid))
        redirect(handler,landing(handler),cookies=[session(uid),'pastellive_oauth=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax'])
    except Exception:
        redirect(handler,landing(handler)+'?auth_error=discord_failed')


init()
