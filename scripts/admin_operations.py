"""Operations restored from the legacy administrator dashboard."""
import datetime
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import parse_qs,urlsplit
import accounts
import database

started=time.time()
pending={}
mutex=threading.Lock()
stop=threading.Event()
rules_cache=None
rules_time=0


def init():
    with database.connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS admin_audit(id TEXT PRIMARY KEY,actor TEXT NOT NULL,action TEXT NOT NULL,target TEXT NOT NULL,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS ip_bans(ip TEXT PRIMARY KEY,reason TEXT NOT NULL,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS path_blocks(path TEXT PRIMARY KEY,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS user_staff(user_id TEXT PRIMARY KEY,label TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS request_traffic(id TEXT PRIMARY KEY,ip TEXT NOT NULL,path TEXT NOT NULL,day TEXT NOT NULL,hits INTEGER NOT NULL,last_seen REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS search_queries(name TEXT PRIMARY KEY,hits INTEGER NOT NULL,updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS schedule_events(id TEXT PRIMARY KEY,title TEXT NOT NULL,channel_id TEXT NOT NULL DEFAULT '',starts_at REAL NOT NULL,ends_at REAL NOT NULL DEFAULT 0,link TEXT NOT NULL DEFAULT '',note TEXT NOT NULL DEFAULT '');
        ''')


def audit(actor,action,target=''):
    with database.connect() as db:
        db.execute('INSERT INTO admin_audit VALUES(?,?,?,?,?)',(secrets.token_hex(16),actor['id'],action,str(target)[:190],time.time()))


def setting(name,default):
    with database.connect() as db:
        row=db.execute('SELECT value FROM site_settings WHERE name=?',(name,)).fetchone()
    return json.loads(row[0]) if row else default


def rules():
    global rules_cache,rules_time
    if rules_cache is None or time.time()-rules_time>10:
        with database.connect() as db:
            rules_cache=dict(ips={row[0] for row in db.execute('SELECT ip FROM ip_bans')},paths={row[0] for row in db.execute('SELECT path FROM path_blocks')})
        rules_time=time.time()
    return rules_cache


def observe(handler,path):
    if not handler.public_auth_request():return True
    address=accounts.visitor_ip(handler)
    if not address:return True
    policy=rules()
    if address in policy['ips'] or path in policy['paths']:
        accounts.reply(handler,{'error':'접근이 제한되었습니다.'},403);return False
    if path in ('/','/index.html','/index2.html','/api/videos','/api/shorts'):
        day=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y-%m-%d')
        key=(address,path,day)
        with mutex:
            if len(pending)<4096 or key in pending:pending[key]=pending.get(key,0)+1
    return True


def flush():
    with mutex:
        batch=dict(pending);pending.clear()
    if not batch:return
    try:
        with database.connect() as db:
            for (address,path,day),hits in batch.items():
                key=hashlib.sha256((address+'|'+path+'|'+day).encode()).hexdigest()
                db.execute('INSERT OR IGNORE INTO request_traffic VALUES(?,?,?,?,0,?)',(key,address,path,day,time.time()))
                db.execute('UPDATE request_traffic SET hits=hits+?,last_seen=? WHERE id=?',(hits,time.time(),key))
    except Exception:
        with mutex:
            for key,hits in batch.items():
                if len(pending)<4096 or key in pending:pending[key]=pending.get(key,0)+hits


def start():
    stop.clear()
    def worker():
        while not stop.wait(5):flush()
    threading.Thread(target=worker,daemon=True).start()


def close():
    stop.set();flush()


def redact(text):
    text=re.sub(r'(?i)(authorization[:=]\s*bearer\s+|(?:token|password|secret|code)=)[^\s&]+',r'\1[hidden]',text)
    for key,value in accounts.config().items():
        if len(value)>6 and any(part in key.upper() for part in ('SECRET','PASSWORD','TOKEN','API_KEY')):text=text.replace(value,'[hidden]')
    return text


def get(handler,path):
    params=parse_qs(urlsplit(handler.path).query)
    if path=='/api/admin/schedule':
        with database.connect() as db:
            items=[dict(row) for row in db.execute('SELECT * FROM schedule_events ORDER BY starts_at DESC LIMIT 300')]
        channels=[dict(id=profile['youtube_channel_id'],name=profile['name']) for profile in database.profiles()]
        return accounts.reply(handler,dict(items=items,channels=channels))
    if path=='/api/admin/users':
        query=params.get('q',[''])[0].strip()[:100]
        try:page=max(1,min(100000,int(params.get('page',['1'])[0])))
        except ValueError:page=1
        with database.connect() as db:
            condition=' WHERE name LIKE ? OR handle LIKE ?' if query else ''
            args=('%'+query+'%','%'+query+'%') if query else ()
            count=db.execute('SELECT COUNT(*) FROM users'+condition,args).fetchone()[0]
            rows=db.execute('SELECT id,name,handle,is_admin,discord_id,created,suspended_until FROM users'+condition+' ORDER BY created DESC LIMIT 30 OFFSET ?',args+((page-1)*30,)).fetchall()
            labels={row['user_id']:row['label'] for row in db.execute('SELECT * FROM user_staff')}
        return accounts.reply(handler,dict(items=[dict(id=r['id'],name=r['name'],handle=r['handle'],isAdmin=bool(r['is_admin']),discordLinked=bool(r['discord_id']),created=r['created'],suspendedUntil=r['suspended_until'],staffLabel=labels.get(r['id'],'')) for r in rows],page=page,total=count))
    if path=='/api/admin/system':
        import psutil
        disk=psutil.disk_usage(str(database.ROOT));ram=psutil.virtual_memory()
        return accounts.reply(handler,dict(uptime=time.time()-started,cpu=psutil.cpu_percent(interval=None),memory=ram.percent,diskUsed=disk.used,diskTotal=disk.total,processes=len(psutil.pids()),backend='MySQL' if (database.ROOT/'data/mysql-enabled').exists() else 'SQLite'))
    if path=='/api/admin/server-log':
        file=database.ROOT/'logs/web-server.log'
        text=''
        if file.exists():
            with file.open('rb') as source:
                source.seek(max(0,file.stat().st_size-64000));text=source.read().decode('utf-8',errors='replace')
        return accounts.reply(handler,dict(text=redact('\n'.join(text.splitlines()[-200:]))))
    if path=='/api/admin/audit-log':
        with database.connect() as db:
            rows=[dict(r) for r in db.execute('SELECT admin_audit.*,users.name AS actor_name FROM admin_audit LEFT JOIN users ON users.id=admin_audit.actor ORDER BY admin_audit.created DESC LIMIT 200')]
        return accounts.reply(handler,dict(items=rows))
    if path=='/api/admin/traffic':
        flush()
        with database.connect() as db:
            rows=[dict(r) for r in db.execute('SELECT ip,path,day,hits,last_seen FROM request_traffic ORDER BY last_seen DESC LIMIT 200')]
            bans=[dict(r) for r in db.execute('SELECT * FROM ip_bans ORDER BY created DESC')]
            paths=[r[0] for r in db.execute('SELECT path FROM path_blocks')]
        return accounts.reply(handler,dict(items=rows,bans=bans,paths=paths))
    if path=='/api/admin/site-settings':
        return accounts.reply(handler,dict(maintenance=setting('maintenance',dict(enabled=False,message='사이트 점검 중입니다.')),bannedWords=setting('bannedWords',[])))
    return handler.send_error(404)


def post(handler,path,data,actor):
    global rules_time
    if path in ('/api/admin/schedule','/api/admin/schedule/delete'):
        from urllib.parse import urlsplit
        event_id=str(data.get('id','')) or secrets.token_hex(16)
        if not re.fullmatch(r'[a-f0-9]{32}',event_id):return accounts.reply(handler,{'error':'일정 ID가 올바르지 않습니다.'},400)
        if path.endswith('/delete'):
            with database.connect() as db:db.execute('DELETE FROM schedule_events WHERE id=?',(event_id,))
            audit(actor,'schedule.delete',event_id)
            return accounts.reply(handler,{'ok':True})
        title=str(data.get('title','')).strip();note=str(data.get('note','')).strip();channel=str(data.get('channel_id',''))
        start=data.get('starts_at');end=data.get('ends_at',0);link=str(data.get('link','')).strip()
        if (not 1<=len(title)<=120 or len(note)>300 or len(link)>600 or type(start) not in (int,float)
                or not 946684800<=start<4102444800 or type(end) not in (int,float) or end and not start<=end<4102444800):
            return accounts.reply(handler,{'error':'제목·시작 시간·종료 시간을 확인해 주세요.'},400)
        if channel and channel not in {profile['youtube_channel_id'] for profile in database.profiles()}:
            return accounts.reply(handler,{'error':'등록된 채널을 선택해 주세요.'},400)
        if link:
            parsed=urlsplit(link)
            if parsed.path=='/watch' and not parsed.netloc and re.fullmatch(r'[\w-]{11}',parse_qs(parsed.query).get('v',[''])[0]):
                link='/watch?v='+parse_qs(parsed.query)['v'][0]
            elif parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or any(ord(c)<32 for c in link):
                return accounts.reply(handler,{'error':'HTTPS 시청 주소 또는 /watch?v=영상ID를 입력해 주세요.'},400)
            elif parsed.hostname.lower() in ('youtube.com','www.youtube.com','m.youtube.com','youtu.be'):
                video_id=parsed.path.strip('/').split('/')[-1] if parsed.hostname.lower()=='youtu.be' or parsed.path.startswith('/shorts/') else parse_qs(parsed.query).get('v',[''])[0]
                if not re.fullmatch(r'[\w-]{11}',video_id):return accounts.reply(handler,{'error':'YouTube 영상 주소를 확인해 주세요.'},400)
                link='/watch?v='+video_id
        with database.connect() as db:
            db.execute('INSERT OR REPLACE INTO schedule_events VALUES(?,?,?,?,?,?,?)',(event_id,title,channel,start,end,link,note))
        audit(actor,'schedule.save',event_id)
        return accounts.reply(handler,{'ok':True,'id':event_id})
    if path=='/api/admin/users/action':
        uid=str(data.get('id',''));action=data.get('action')
        with database.connect() as db:
            member=db.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
            if not member:return accounts.reply(handler,{'error':'회원을 찾을 수 없습니다.'},404)
            if member['is_admin']:return accounts.reply(handler,{'error':'관리자 계정은 변경하거나 삭제할 수 없습니다.'},403)
            if action=='suspend':
                hours=data.get('hours',24)
                if type(hours) is not int or not 1<=hours<=8760:return accounts.reply(handler,{'error':'정지 기간은 1~8760시간입니다.'},400)
                db.execute('UPDATE users SET suspended_until=? WHERE id=?',(time.time()+hours*3600,uid))
                db.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
            elif action=='unsuspend':db.execute('UPDATE users SET suspended_until=0 WHERE id=?',(uid,))
            elif action=='sessions':db.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
            elif action=='staff':
                label=str(data.get('label','')).strip()[:60]
                if label:db.execute('INSERT OR REPLACE INTO user_staff VALUES(?,?)',(uid,label))
                else:db.execute('DELETE FROM user_staff WHERE user_id=?',(uid,))
            elif action=='delete':
                if data.get('confirm')!=member['handle']:return accounts.reply(handler,{'error':'삭제할 회원 아이디를 정확히 입력해 주세요.'},400)
                for table in ('sessions','user_avatars','user_staff'):db.execute('DELETE FROM '+table+' WHERE user_id=?',(uid,))
                db.execute('DELETE FROM oauth_states WHERE user_id=?',(uid,))
                db.execute('DELETE FROM users WHERE id=?',(uid,))
            else:return accounts.reply(handler,{'error':'지원하지 않는 작업입니다.'},400)
        audit(actor,'member.'+action,uid)
        return accounts.reply(handler,{'ok':True})
    if path in ('/api/admin/ip-ban','/api/admin/ip-unban'):
        try:
            address=ipaddress.ip_address(str(data.get('ip','')))
            if address.is_loopback or address.is_unspecified or address.is_multicast:raise ValueError()
            address=str(address)
        except ValueError:return accounts.reply(handler,{'error':'올바른 외부 IP를 입력해 주세요.'},400)
        if path.endswith('/ip-ban') and address==accounts.visitor_ip(handler):return accounts.reply(handler,{'error':'현재 관리자의 접속 IP는 차단할 수 없습니다.'},400)
        with database.connect() as db:
            if path.endswith('/ip-ban'):db.execute('INSERT OR REPLACE INTO ip_bans VALUES(?,?,?)',(address,str(data.get('reason',''))[:300],time.time()))
            else:db.execute('DELETE FROM ip_bans WHERE ip=?',(address,))
        rules_time=0;audit(actor,path,address)
        return accounts.reply(handler,{'ok':True})
    if path in ('/api/admin/blocked-paths','/api/admin/blocked-paths/delete'):
        value=str(data.get('path','')).strip()
        if not value.startswith('/') or len(value)>180 or value in ('/','/index.html') or value.startswith(('/api/auth/','/auth/discord/')) or '?' in value or '#' in value:
            return accounts.reply(handler,{'error':'홈·로그인 경로를 제외한 정확한 경로를 입력해 주세요.'},400)
        with database.connect() as db:
            if path.endswith('/delete'):db.execute('DELETE FROM path_blocks WHERE path=?',(value,))
            else:db.execute('INSERT OR REPLACE INTO path_blocks VALUES(?,?)',(value,time.time()))
        rules_time=0;audit(actor,path,value)
        return accounts.reply(handler,{'ok':True})
    if path=='/api/admin/site-settings':
        maintenance=data.get('maintenance');words=data.get('bannedWords')
        if (not isinstance(maintenance,dict) or type(maintenance.get('enabled')) is not bool
                or not isinstance(maintenance.get('message'),str) or len(maintenance['message'])>300
                or not isinstance(words,list) or len(words)>50 or any(not isinstance(w,str) or not 1<=len(w.strip())<=40 for w in words)):
            return accounts.reply(handler,{'error':'점검 문구는 300자, 금지어는 40자 이내 최대 50개입니다.'},400)
        payloads=[(key,json.dumps(value,ensure_ascii=False)) for key,value in [('maintenance',maintenance),('bannedWords',list(dict.fromkeys(w.strip() for w in words)))]]
        if any(len(payload)>1000 for _,payload in payloads):return accounts.reply(handler,{'error':'설정 내용이 너무 깁니다.'},400)
        with database.connect() as db:
            for key,payload in payloads:
                db.execute('INSERT OR REPLACE INTO site_settings VALUES(?,?,?)',(key,payload,time.time()))
        audit(actor,'site-settings')
        return accounts.reply(handler,{'ok':True})
    return handler.send_error(404)


init()
