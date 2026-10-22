"""Account libraries and community galleries adapted from preserved profile/highlights views."""
import base64
import io
import html
from urllib.parse import urlencode
import json
import re
import secrets
import time
import accounts
import database
from PIL import Image

VIEWS={'history':'시청 기록','bookmarks':'북마크','playlists':'재생목록','gallery':'팬 갤러리','highlights':'하이라이트','mypage':'마이페이지'}

def init():
    with database.connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS library_items(user_id TEXT NOT NULL,video_id TEXT NOT NULL,kind TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(user_id,video_id,kind));
        CREATE TABLE IF NOT EXISTS library_playlists(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,title TEXT NOT NULL,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS library_playlist_items(source_id TEXT NOT NULL,video_id TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(source_id,video_id));
        CREATE TABLE IF NOT EXISTS library_highlights(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,video_id TEXT NOT NULL,title TEXT NOT NULL,starts_at INTEGER NOT NULL,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS library_gallery(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,title TEXT NOT NULL,image BLOB NOT NULL,content_type TEXT NOT NULL,created REAL NOT NULL);
        """)

def page(view,user=None,params=None):
    hero='<div class="mypage-hero"></div><nav class="mypage-tabs" aria-label="내 활동"><button data-view="history">시청 기록</button><button data-view="bookmarks">북마크</button><button data-view="playlists">재생목록</button><button data-view="gallery">내 업로드</button><button data-view="highlights">내 하이라이트</button></nav>' if view=='mypage' else ''
    params=dict(params or {});params['view']=['history' if view=='mypage' else view]
    try:initial=dataset(params,user)
    except LibraryError as error:initial=dict(items=[],hasMore=False,nextOffset=0,error=error.message)
    cards=''.join(server_card(item,params['view'][0],bool(params.get('playlist',[''])[0])) for item in initial['items'])
    bootstrap=json.dumps(initial,ensure_ascii=False).replace('<','\\u003c')
    return VIEWS[view],'<section class="library-page" data-library="'+view+'"><h1>'+VIEWS[view]+'</h1>'+hero+'<div class="library-tools"></div><p class="library-status" role="status">'+html.escape(initial.get('error','') or ('' if cards else '아직 등록된 항목이 없습니다.'))+'</p><div class="library-grid">'+cards+'</div><script type="application/json" class="library-bootstrap">'+bootstrap+'</script><button class="library-more" type="button" hidden>더 보기</button></section>'

def server_card(item,view,selected):
    esc=lambda value:html.escape(str(value or ''),quote=True)
    if view=='playlists' and not selected:
        body='<a href="/playlists?'+urlencode({'playlist':item['id']})+'"><h2>'+esc(item['title'])+'</h2><p>'+str(item['count'])+'개 영상</p></a>'
    elif view=='gallery':
        body='<a href="'+esc(item['image'])+'" target="_blank" rel="noopener"><img src="'+esc(item['image'])+'" alt="'+esc(item['title'])+'" loading="lazy"></a><h2>'+esc(item['title'])+'</h2><p>'+esc(item.get('author') or '탈퇴한 회원')+'</p>'
    else:
        query={'v':item.get('video_id') or item['id']}
        if view=='highlights':query['t']=item['starts_at']
        body='<a href="/watch?'+esc(urlencode(query))+'"><img src="'+esc(item.get('thumbnail'))+'" alt="" loading="lazy"><h2>'+esc(item['title'])+'</h2></a><p>'+esc(item.get('channelName'))+'</p>'
    return '<article class="library-card">'+body+'</article>'

def watch_tools(video):
    return '<div class="library-watch-tools" data-video="'+video+'"><button type="button" data-save="bookmark">북마크</button><button type="button" data-save="playlist">재생목록에 추가</button><button type="button" data-save="highlight">하이라이트 저장</button></div>'

class LibraryError(Exception):
    def __init__(self,message,status=400):self.message=message;self.status=status

def dataset(params,user):
    view=params.get('view',['history'])[0]
    if view not in VIEWS:raise LibraryError('잘못된 메뉴입니다.')
    if view not in ('gallery','highlights') and not user:raise LibraryError('로그인 후 이용해 주세요.',401)
    try:offset=max(0,min(100000,int(params.get('offset',['0'])[0])))
    except ValueError:raise LibraryError('페이지를 확인해 주세요.')
    uid=user['id'] if user else '';selected=params.get('playlist',[''])[0][:100]
    with database.connect() as db:
        if view=='mypage':
            counts={}
            for key,table in (('playlists','library_playlists'),('gallery','library_gallery'),('highlights','library_highlights')):counts[key]=db.execute('SELECT COUNT(*) FROM '+table+' WHERE user_id=?',(uid,)).fetchone()[0]
            for key,kind in (('history','history'),('bookmarks','bookmark')):counts[key]=db.execute('SELECT COUNT(*) FROM library_items WHERE user_id=? AND kind=?',(uid,kind)).fetchone()[0]
            return dict(user=accounts.public(user),counts=counts)
        if view=='playlists' and not selected:
            rows=db.execute('SELECT p.*, (SELECT COUNT(*) FROM library_playlist_items i WHERE i.source_id=p.id) AS count FROM library_playlists p WHERE p.user_id=? ORDER BY p.created DESC LIMIT 31 OFFSET ?',(uid,offset)).fetchall()
        elif view=='playlists':
            if not db.execute('SELECT 1 FROM library_playlists WHERE id=? AND user_id=?',(selected,uid)).fetchone():raise LibraryError('재생목록이 없습니다.',404)
            rows=db.execute('SELECT v.*,i.created FROM library_playlist_items i JOIN videos v ON v.id=i.video_id WHERE i.source_id=? ORDER BY i.created DESC LIMIT 31 OFFSET ?',(selected,offset)).fetchall()
        elif view in ('gallery','highlights'):
            table='library_gallery' if view=='gallery' else 'library_highlights'
            fields='c.id,c.user_id,c.title,c.created,u.name AS author'+(',c.video_id,c.starts_at,v.thumbnail,v.channelName' if view=='highlights' else '')
            join=' LEFT JOIN videos v ON v.id=c.video_id' if view=='highlights' else ''
            mine=params.get('mine',['0'])[0]=='1'
            if mine and not user:raise LibraryError('로그인 후 이용해 주세요.',401)
            rows=db.execute('SELECT '+fields+' FROM '+table+' c LEFT JOIN users u ON u.id=c.user_id'+join+(' WHERE c.user_id=?' if mine else '')+' ORDER BY c.created DESC,c.id DESC LIMIT 31 OFFSET ?',((uid,offset) if mine else (offset,))).fetchall()
        else:
            rows=db.execute('SELECT v.*,i.created FROM library_items i JOIN videos v ON v.id=i.video_id WHERE i.user_id=? AND i.kind=? ORDER BY i.created DESC LIMIT 31 OFFSET ?',(uid,'history' if view=='history' else 'bookmark',offset)).fetchall()
    items=[dict(row) for row in rows[:30]]
    for item in items:
        item['canDelete']=bool(user and (item.get('user_id',uid)==uid or user.get('is_admin')))
        if view=='gallery':item['image']='/api/library/image?id='+item['id']
    return dict(items=items,nextOffset=offset+len(items),hasMore=len(rows)>30)

def get(handler,params):
    try:return accounts.reply(handler,dataset(params,accounts.current(handler)))
    except LibraryError as error:return accounts.reply(handler,{'error':error.message},error.status)

def image(handler,params):
    with database.connect() as db:row=db.execute('SELECT image,content_type FROM library_gallery WHERE id=?',(params.get('id',[''])[0][:100],)).fetchone()
    if not row:return handler.send_error(404)
    raw=bytes(row['image']);handler.send_response(200);handler.send_header('Content-Type',row['content_type']);handler.send_header('Content-Length',str(len(raw)));handler.send_header('Cache-Control','public, max-age=3600');handler.send_header('X-Content-Type-Options','nosniff');handler.end_headers();handler.wfile.write(raw)

def post(handler):
    expected='https://pastellive.co.kr' if handler.headers.get('Host')=='pastellive.co.kr' else 'http://'+handler.headers.get('Host','')
    if handler.headers.get('Origin')!=expected:return accounts.reply(handler,{'error':'허용되지 않은 요청입니다.'},403)
    user=accounts.current(handler)
    if not user:return accounts.reply(handler,{'error':'로그인 후 이용해 주세요.'},401)
    try:
        length=int(handler.headers.get('Content-Length','0'))
        if not 0<length<=7100000:raise ValueError()
        data=json.loads(handler.rfile.read(length))
        if not isinstance(data,dict):raise ValueError()
        action=str(data.get('action',''));vid=str(data.get('video',''));title=str(data.get('title','')).strip();uid=user['id'];now=time.time()
        with database.connect() as db:
            if action in ('history','bookmark','playlist-add','highlight'):
                if not re.fullmatch(r'[A-Za-z0-9_-]{11}',vid) or not db.execute('SELECT 1 FROM videos WHERE id=?',(vid,)).fetchone():raise ValueError('영상을 확인해 주세요.')
            if action in ('playlist-create','highlight','gallery'):
                if not 1<=len(title)<=100 or accounts.banned_name(title):raise ValueError('제목을 1~100자로 입력해 주세요.')
            if action in ('history','bookmark'):
                db.execute('INSERT OR REPLACE INTO library_items VALUES(?,?,?,?)',(uid,vid,action,now))
            elif action=='item-delete':
                kind=str(data.get('kind',''))
                if kind not in ('history','bookmark'):raise ValueError()
                db.execute('DELETE FROM library_items WHERE user_id=? AND video_id=? AND kind=?',(uid,vid,kind))
            elif action=='playlist-create':
                pid=secrets.token_hex(16);db.execute('INSERT INTO library_playlists VALUES(?,?,?,?)',(pid,uid,title,now))
                return accounts.reply(handler,{'ok':True,'id':pid})
            elif action in ('playlist-add','playlist-remove','playlist-delete'):
                pid=str(data.get('playlist',''))[:100]
                if not db.execute('SELECT 1 FROM library_playlists WHERE id=? AND user_id=?',(pid,uid)).fetchone():return accounts.reply(handler,{'error':'본인의 재생목록만 수정할 수 있습니다.'},403)
                if action=='playlist-add':db.execute('INSERT OR IGNORE INTO library_playlist_items VALUES(?,?,?)',(pid,vid,now))
                elif action=='playlist-remove':db.execute('DELETE FROM library_playlist_items WHERE source_id=? AND video_id=?',(pid,vid))
                else:
                    db.execute('DELETE FROM library_playlist_items WHERE source_id=?',(pid,));db.execute('DELETE FROM library_playlists WHERE id=?',(pid,))
            elif action=='highlight':
                start=int(data.get('start',0))
                if not 0<=start<=86400:raise ValueError()
                db.execute('INSERT INTO library_highlights VALUES(?,?,?,?,?,?)',(secrets.token_hex(16),uid,vid,title,start,now))
            elif action=='gallery':
                raw=base64.b64decode(str(data.get('image','')).split(',')[-1],validate=True)
                if not 0<len(raw)<=5000000:raise ValueError('사진은 5MB 이하로 올려 주세요.')
                with Image.open(io.BytesIO(raw)) as picture:
                    if picture.format not in ('JPEG','PNG','WEBP','GIF') or picture.width*picture.height>20000000:raise ValueError('지원하지 않는 이미지입니다.')
                    picture.seek(0);picture.thumbnail((2000,2000));output=io.BytesIO();picture.convert('RGB').save(output,format='JPEG',quality=90)
                db.execute('INSERT INTO library_gallery VALUES(?,?,?,?,?,?)',(secrets.token_hex(16),uid,title,output.getvalue(),'image/jpeg',now))
            elif action in ('gallery-delete','highlight-delete'):
                table='library_gallery' if action=='gallery-delete' else 'library_highlights';item=str(data.get('id',''))[:100]
                row=db.execute('SELECT user_id FROM '+table+' WHERE id=?',(item,)).fetchone()
                if not row or (row['user_id']!=uid and not user.get('is_admin')):return accounts.reply(handler,{'error':'삭제 권한이 없습니다.'},403)
                db.execute('DELETE FROM '+table+' WHERE id=?',(item,))
            else:raise ValueError()
        return accounts.reply(handler,{'ok':True})
    except (ValueError,TypeError,OSError,Image.DecompressionBombError) as error:
        return accounts.reply(handler,{'error':str(error) if str(error) in ('영상을 확인해 주세요.','제목을 1~100자로 입력해 주세요.','사진은 5MB 이하로 올려 주세요.','지원하지 않는 이미지입니다.') else '입력을 확인해 주세요.'},400)

init()
