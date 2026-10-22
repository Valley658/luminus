"""Administrator-only dashboard; all authorization is checked against the DB."""
import json
import time
from pathlib import Path
import accounts
import database
import mysql_backend
import youtube_shorts

ROOT = database.ROOT


def init():
    with database.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS site_settings(name TEXT PRIMARY KEY,value TEXT NOT NULL,updated REAL NOT NULL)')


def home_settings():
    result = dict(showShorts=True, titleSize=20, announcement='')
    with database.connect() as db:
        for row in db.execute('SELECT name,value FROM site_settings'):
            if row['name']=='home':
                value=json.loads(row['value'])
                result.update({key:value[key] for key in result if key in value})
    return result


def authorized(handler):
    user = accounts.current(handler)
    if not user:
        accounts.reply(handler, {'error': '메인 사이트에서 로그인해 주세요.'}, 401)
        return None
    if not user.get('is_admin'):
        accounts.reply(handler, {'error': '관리자만 접근할 수 있습니다.'}, 403)
        return None
    return user


def send(handler, path, content_type):
    body = path.read_bytes()
    handler.send_response(200)
    handler.send_header('Content-Type', content_type)
    handler.send_header('Content-Length', str(len(body)))
    handler.send_header('Cache-Control', 'private, no-store')
    handler.send_header('X-Content-Type-Options', 'nosniff')
    handler.send_header('X-Frame-Options', 'DENY')
    handler.end_headers()
    handler.wfile.write(body)


def get(handler, path):
    if path in ('/', '/index.html'):
        return send(handler, ROOT / 'templates/luminus/admin.html', 'text/html; charset=utf-8')
    if path == '/favicon.ico':
        return send(handler, ROOT / 'public/favicon.ico', 'image/x-icon')
    if path in ('/admin-assets/admin.js', '/admin-assets/admin.css'):
        return send(handler, ROOT / 'static/luminus/assets' / Path(path).name,
                    'text/javascript; charset=utf-8' if path.endswith('.js') else 'text/css; charset=utf-8')
    if path == '/api/auth/me':
        return accounts.reply(handler, dict(user=accounts.public(accounts.current(handler))))
    if not path.startswith('/api/admin/'):
        return handler.send_error(404)
    user = authorized(handler)
    if not user:
        return
    if path!='/api/admin/summary':return admin_operations.get(handler,path)
    with database.connect() as db:
        count = lambda table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
        stats = dict(users=count('users'), videos=count('videos'), channels=count('channels'),
                     shorts=db.execute("SELECT COUNT(*) FROM videos WHERE kind='shorts'").fetchone()[0],
                     songs=count('music_memberships'),
                     sessions=db.execute('SELECT COUNT(*) FROM sessions WHERE expires>?', (time.time(),)).fetchone()[0])
        updated = db.execute("SELECT value FROM metadata WHERE name='updatedAt'").fetchone()
        members = [dict(name=row['name'], handle='@'+row['handle'], isAdmin=bool(row['is_admin']),
                        discordLinked=bool(row['discord_id']), created=row['created'])
                   for row in db.execute('SELECT name,handle,is_admin,discord_id,created FROM users ORDER BY created DESC LIMIT 100')]
    config = accounts.config()
    roles = []
    for item in config.get('STAFF_ROLE_LABELS', '').split(','):
        if ':' in item:
            name, label = item.split(':', 1)
            roles.append(dict(name=name, label=label))
    return accounts.reply(handler, dict(user=accounts.public(user), stats=stats, members=members, home=home_settings(),
                         roles=roles, backend='MySQL' if mysql_backend.enabled() else 'SQLite',
                         cacheUpdatedAt=updated[0] if updated else 0, refreshing=youtube_shorts.running))


def post(handler, path):
    if path == '/api/auth/logout':
        return accounts.post(handler, path)
    if not path.startswith('/api/admin/'):
        return handler.send_error(404)
    if handler.headers.get('Origin')!='https://admin.pastellive.co.kr':
        return accounts.reply(handler, {'error':'허용되지 않은 요청입니다.'}, 403)
    actor=authorized(handler)
    if not actor:return
    try:
        size=int(handler.headers.get('Content-Length','0'))
        if not 0<=size<=4096:raise ValueError()
        data=json.loads(handler.rfile.read(size) or b'{}')
        if not isinstance(data,dict):raise ValueError()
    except (ValueError,TypeError):
        return accounts.reply(handler, {'error':'입력을 확인해 주세요.'},400)
    if path not in ('/api/admin/home','/api/admin/cache/refresh'):
        return admin_operations.post(handler,path,data,actor)
    if path=='/api/admin/cache/refresh':
        key=youtube_shorts.api_key()
        if not key:return accounts.reply(handler,{'error':'YouTube API 설정을 확인해 주세요.'},503)
        with youtube_shorts.lock:
            if youtube_shorts.running or time.time()-youtube_shorts.attempted<60:
                return accounts.reply(handler,{'error':'갱신 중이거나 최근에 요청했습니다. 잠시 후 확인해 주세요.'},429)
            youtube_shorts.running=True;youtube_shorts.attempted=time.time()
            youtube_shorts.threading.Thread(target=youtube_shorts.refresh,args=(key,),daemon=True).start()
        admin_operations.audit(actor,'cache.refresh')
        return accounts.reply(handler,{'ok':True},202)
    if (type(data.get('showShorts')) is not bool or type(data.get('titleSize')) is not int
            or not 14<=data['titleSize']<=28 or not isinstance(data.get('announcement'),str)
            or len(data['announcement'])>300):
        return accounts.reply(handler,{'error':'제목은 14~28px, 공지는 300자 이내로 입력해 주세요.'},400)
    settings=dict(showShorts=data['showShorts'],titleSize=data['titleSize'],announcement=data['announcement'].strip())
    with database.connect() as db:
        db.execute('INSERT OR REPLACE INTO site_settings VALUES(?,?,?)',('home',json.dumps(settings,ensure_ascii=False),time.time()))
    admin_operations.audit(actor,'home.update')
    return accounts.reply(handler,{'ok':True,'home':settings})


init()
import admin_operations
