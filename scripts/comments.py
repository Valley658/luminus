"""Authenticated site comments and shared, persistent YouTube comment cache."""
import hashlib
import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import accounts
import database
import youtube_shorts

lock=threading.Lock()
pending=set()
attempts={}

def init():
    with database.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS site_comments(id TEXT PRIMARY KEY,video_id TEXT NOT NULL,user_id TEXT NOT NULL,body TEXT NOT NULL,created REAL NOT NULL)')
        db.executescript('CREATE INDEX IF NOT EXISTS site_comments_video ON site_comments(video_id,created);')
        db.execute('CREATE TABLE IF NOT EXISTS youtube_comment_cache(id TEXT PRIMARY KEY,content LONGTEXT NOT NULL,updated REAL NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS youtube_comment_authors(id TEXT PRIMARY KEY,name TEXT NOT NULL,updated REAL NOT NULL)')

def masked_name(value):
    value=re.sub(r'[\x00-\x1f\x7f]','',str(value)).strip().lstrip('@')[:100]
    if not value:return '익명 ▧▧'
    if len(value)==1:return '▧'
    return value[0]+'▧'*min(6,max(1,len(value)-2))+(value[-1] if len(value)>=4 else '')

def author_names(snippets):
    """Resolve channel titles in one server request; persist only masked names."""
    ids=list(dict.fromkeys(s.get('authorChannelId',{}).get('value','') for s in snippets))
    ids=[cid for cid in ids if re.fullmatch(r'[A-Za-z0-9_-]{24}',cid)]
    names={};missing=[]
    with database.connect() as db:
        for cid in ids:
            row=db.execute('SELECT * FROM youtube_comment_authors WHERE id=?',(cid,)).fetchone()
            if row:names[cid]=row['name']
            if not row or time.time()-row['updated']>86400:missing.append(cid)
    if missing:
        try:
            data=youtube_shorts.api('channels',youtube_shorts.api_key(),part='snippet',id=','.join(missing))
            with database.connect() as db:
                for item in data.get('items',[]):
                    name=masked_name(item['snippet']['title']);names[item['id']]=name
                    db.execute('INSERT OR REPLACE INTO youtube_comment_authors VALUES(?,?,?)',(item['id'],name,time.time()))
        except Exception:pass
    return names

def refresh(video,cursor,key):
    result=dict(items=[],nextCursor='',message='')
    try:
        params=dict(part='snippet',videoId=video,maxResults=30,textFormat='plainText',order='relevance',key=youtube_shorts.api_key())
        if cursor:params['pageToken']=cursor
        url='https://www.googleapis.com/youtube/v3/commentThreads?'+urllib.parse.urlencode(params)
        with urllib.request.urlopen(url,timeout=15) as response:payload=json.load(response)
        snippets=[item['snippet']['topLevelComment']['snippet'] for item in payload.get('items',[])]
        names=author_names(snippets)
        for item in payload.get('items',[]):
            comment=item['snippet']['topLevelComment'];s=comment['snippet']
            name=names.get(s.get('authorChannelId',{}).get('value',''))
            if not name:name=masked_name(s.get('authorDisplayName','')) if not s.get('authorDisplayName','').startswith('@') else '익명 ▧▧'
            result['items'].append(dict(id=comment['id'],author=name,masked=True,avatar=s.get('authorProfileImageUrl',''),body=s.get('textDisplay',''),created=s.get('publishedAt',''),likes=s.get('likeCount',0),canDelete=False))
        result['nextCursor']=payload.get('nextPageToken','')
    except urllib.error.HTTPError as error:
        try:reason=json.loads(error.read(65536))['error']['errors'][0]['reason']
        except (ValueError,KeyError,IndexError,TypeError):reason=''
        result['message']='이 영상은 YouTube 댓글이 비활성화되어 있습니다.' if reason=='commentsDisabled' else 'YouTube 댓글을 가져오지 못했습니다. 잠시 후 다시 시도해 주세요.'
    except Exception:
        result['message']='YouTube 댓글 연결이 지연되고 있습니다. 잠시 후 다시 시도해 주세요.'
    try:
        with database.connect() as db:db.execute('INSERT OR REPLACE INTO youtube_comment_cache VALUES(?,?,?)',(key,json.dumps(result,ensure_ascii=False),time.time()))
    finally:
        with lock:pending.discard(key)

def youtube(video,cursor):
    key=hashlib.sha256(('masked-v2:'+video+':'+cursor).encode()).hexdigest()
    with database.connect() as db:row=db.execute('SELECT * FROM youtube_comment_cache WHERE id=?',(key,)).fetchone()
    stale=not row or time.time()-row['updated']>(120 if json.loads(row['content']).get('message') else 3600)
    if stale:
        with lock:
            if key not in pending:
                pending.add(key);threading.Thread(target=refresh,args=(video,cursor,key),daemon=True).start()
    return dict(json.loads(row['content']),refreshing=False) if row else dict(items=[],nextCursor='',refreshing=True,message='YouTube 댓글을 가져오고 있습니다.')

def get(handler,params):
    video=params.get('video',[''])[0];cursor=params.get('cursor',[''])[0]
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}',video) or len(cursor)>600:return accounts.reply(handler,{'error':'영상을 확인해 주세요.'},400)
    if params.get('source',['site'])[0]=='youtube':return accounts.reply(handler,youtube(video,cursor))
    try:offset=max(0,min(int(cursor or 0),100000))
    except ValueError:return accounts.reply(handler,{'error':'페이지를 확인해 주세요.'},400)
    user=accounts.current(handler)
    with database.connect() as db:
        rows=db.execute('SELECT c.*,u.name,u.avatar,u.is_admin FROM site_comments c LEFT JOIN users u ON u.id=c.user_id WHERE c.video_id=? ORDER BY c.created DESC,c.id DESC LIMIT 31 OFFSET ?',(video,offset)).fetchall()
        total=db.execute('SELECT COUNT(*) FROM site_comments WHERE video_id=?',(video,)).fetchone()[0]
    items=[]
    for row in rows[:30]:
        avatar=row['avatar'] or ''
        if avatar.startswith('/api/auth/avatar'):avatar='/api/comments/avatar?user='+urllib.parse.quote(row['user_id'])
        items.append(dict(id=row['id'],author=row['name'] or '탈퇴한 회원',avatar=avatar,body=row['body'],created=row['created'],isAdmin=bool(row['is_admin']),canDelete=bool(user and (user['id']==row['user_id'] or user.get('is_admin')))))
    return accounts.reply(handler,dict(items=items,total=total,nextCursor=str(offset+30) if len(rows)>30 else '',signedIn=bool(user)))

def avatar(handler,params):
    uid=params.get('user',[''])[0]
    if len(uid)>100:return handler.send_error(404)
    with database.connect() as db:
        row=db.execute('SELECT a.image,a.content_type FROM user_avatars a WHERE a.user_id=? AND EXISTS(SELECT 1 FROM site_comments c WHERE c.user_id=a.user_id)',(uid,)).fetchone()
    if not row:return handler.send_error(404)
    data=bytes(row['image']);handler.send_response(200);handler.send_header('Content-Type',row['content_type']);handler.send_header('Content-Length',str(len(data)));handler.send_header('Cache-Control','public, max-age=60');handler.send_header('X-Content-Type-Options','nosniff');handler.end_headers();handler.wfile.write(data)

def post(handler,path):
    host=handler.headers.get('Host','').lower()
    expected='https://pastellive.co.kr' if host=='pastellive.co.kr' else 'http://'+host
    if handler.headers.get('Origin','')!=expected:return accounts.reply(handler,{'error':'허용되지 않은 요청입니다.'},403)
    user=accounts.current(handler)
    if not user:return accounts.reply(handler,{'error':'로그인 후 댓글을 작성해 주세요.'},401)
    try:
        length=int(handler.headers.get('Content-Length','0'))
        if not 0<length<=8192:raise ValueError()
        data=json.loads(handler.rfile.read(length))
        if not isinstance(data,dict):raise ValueError()
    except (ValueError,TypeError):return accounts.reply(handler,{'error':'입력을 확인해 주세요.'},400)
    with database.connect() as db:
        if path=='/api/comments/delete':
            row=db.execute('SELECT * FROM site_comments WHERE id=?',(str(data.get('id',''))[:100],)).fetchone()
            if not row:return accounts.reply(handler,{'error':'댓글이 없습니다.'},404)
            if row['user_id']!=user['id'] and not user.get('is_admin'):return accounts.reply(handler,{'error':'본인의 댓글만 삭제할 수 있습니다.'},403)
            db.execute('DELETE FROM site_comments WHERE id=?',(row['id'],))
        else:
            video=str(data.get('video',''));body=str(data.get('body','')).strip()
            if not re.fullmatch(r'[A-Za-z0-9_-]{11}',video) or not 1<=len(body)<=500:return accounts.reply(handler,{'error':'댓글은 1~500자로 입력해 주세요.'},400)
            if accounts.banned_name(body):return accounts.reply(handler,{'error':'사용할 수 없는 단어가 포함되어 있습니다.'},400)
            with lock:
                now=time.time()
                for uid in list(attempts):
                    if now-attempts[uid]>10:del attempts[uid]
                if user['id'] in attempts:return accounts.reply(handler,{'error':'잠시 후 다시 작성해 주세요.'},429)
                attempts[user['id']]=now
            db.execute('INSERT INTO site_comments VALUES(?,?,?,?,?)',(secrets.token_hex(16),video,user['id'],body,time.time()))
    return accounts.reply(handler,{'ok':True})

def render(video):
    # A cache miss starts the shared server worker before the viewer selects the tab.
    cached=youtube(video,'')
    bootstrap=json.dumps(cached,ensure_ascii=False).replace('<','\\u003c')
    return '<section class="watch-comments" data-video="'+video+'"><h2>댓글</h2><div class="comment-tabs" role="group" aria-label="댓글 종류"><button type="button" data-source="site" aria-pressed="true">사이트 댓글</button><button type="button" data-source="youtube" aria-pressed="false">YouTube 댓글</button></div><form class="site-comment-form"><textarea maxlength="500" required aria-label="댓글" placeholder="로그인 후 댓글을 남겨 주세요. (최대 500자)"></textarea><button type="submit">댓글 등록</button></form><p class="comment-status" role="status"></p><div class="comment-list"></div><button type="button" class="comment-more" hidden>댓글 더 보기</button><script type="application/json" class="youtube-comments-bootstrap">'+bootstrap+'</script></section>'

init()
