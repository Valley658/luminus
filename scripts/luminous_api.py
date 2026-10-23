"""Database-backed Python ports of luminous-master's account/library routes."""
import base64
import datetime as dt
import hashlib
import html
import re
import secrets
import sqlite3
import time
from urllib.parse import quote
import luminous_port as p
import accounts
import database
import library
import comments


def lookup_video(vid):
    if not re.fullmatch(r'[\w-]{11}',str(vid),re.ASCII):raise p.RequestError('잘못된 영상 ID입니다.')
    with database.connect() as db:r=db.execute('SELECT * FROM videos WHERE id=?',(vid,)).fetchone()
    if not r:raise p.RequestError('등록된 영상이 없습니다.',404)
    return p.video(dict(r))


def reaction(target,user,action):
    uid=p.require(user)
    if action not in ('like','dislike','report'):raise p.RequestError('반응을 확인해 주세요.')
    rid=hashlib.sha256((target+':'+uid).encode()).hexdigest()
    with database.connect() as db:
        old=db.execute('SELECT action FROM luminous_reactions WHERE id=?',(rid,)).fetchone()
        if old and old[0]==action:db.execute('DELETE FROM luminous_reactions WHERE id=?',(rid,))
        else:db.execute('INSERT OR REPLACE INTO luminous_reactions VALUES(?,?,?,?,?)',(rid,target,uid,action,time.time()))
    return counts(target,user)


def counts(target,user):
    with database.connect() as db:
        values={r['action']:r['n'] for r in db.execute('SELECT action,COUNT(*) AS n FROM luminous_reactions WHERE target=? GROUP BY action',(target,))}
        mine=db.execute('SELECT action FROM luminous_reactions WHERE target=? AND user_id=?',(target,user['id'] if user else '')).fetchone()
    return dict(likes=values.get('like',0),likes_count=values.get('like',0),like_count=values.get('like',0),dislikes=values.get('dislike',0),dislike_count=values.get('dislike',0),reports=values.get('report',0),user_reaction=mine[0] if mine else None,my_reaction=mine[0] if mine else None)


def account(h,path,user,data):
    if path=='/api/me':return p.reply(h,p.user_info(user))
    if path=='/api/staff-roles':
        roles={}
        for pair in accounts.config().get('STAFF_ROLE_LABELS','').split(','):
            if ':' in pair:k,v=pair.split(':',1);roles[k]=v
        return p.reply(h,roles=roles)
    if path=='/api/account/set-password':
        uid=p.require(user);password=str(data.get('password',''));old=str(data.get('current_password',''))
        if user['password'] and not secrets.compare_digest(accounts.password_hash(old,user['password'].split(':')[0]),user['password']):raise p.RequestError('현재 비밀번호를 확인해 주세요.',403)
        if not 10<=len(password)<=128:raise p.RequestError('새 비밀번호는 10~128자로 입력해 주세요.')
        name=p.text(data.get('nickname') or user['name'],30);handle=str(data.get('login_id') or user['handle']).strip().lower()
        if not re.fullmatch(r'[a-z0-9_]{3,24}',handle):raise p.RequestError('아이디를 확인해 주세요.')
        if name!=user['name']:
            if user.get('is_admin'):raise p.RequestError('관리자 이름은 변경할 수 없습니다.',403)
            if accounts.protected_name(name):raise p.RequestError(accounts.RESERVED_NAME_MESSAGE)
        hashed=accounts.password_hash(password)
        try:
            with database.connect() as db:
                db.execute('UPDATE users SET handle=?,name=?,password=? WHERE id=?',(handle,name,hashed,uid));db.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
        except sqlite3.IntegrityError:raise p.RequestError('이미 사용 중인 아이디입니다.',409)
        return accounts.reply(h,dict(success=True,message='로그인 정보가 변경되었습니다.'),cookies=[accounts.session(uid)])
    if path in ('/api/login','/api/register'):
        route={'/api/login':'login','/api/register':'signup'}[path]
        body=dict(handle=data.get('login_id',data.get('handle','')),password=data.get('password',''),
                  name=data.get('nickname',''),confirm=data.get('password',''))
        if body['name']:p.text(body['name'],30)
        return p.forward(h,lambda c:accounts.post(c,'/api/auth/'+route),body)
    if path=='/api/me/nickname':
        return p.forward(h,lambda c:accounts.post(c,'/api/auth/profile'),{'name':p.text(data.get('nickname'),30)})
    if path=='/api/me/profile':
        uid=p.require(user)
        upload=data.get('picture') or data.get('file') or data.get('image')
        name=p.text(data.get('nickname',user['name']),30)
        if name!=user['name']:
            if user.get('is_admin'):raise p.RequestError('관리자 이름은 변경할 수 없습니다.',403)
            if accounts.protected_name(name):raise p.RequestError(accounts.RESERVED_NAME_MESSAGE)
        if upload:
            from luminous_social import validate_image
            raw,ctype=validate_image(upload)
        with database.connect() as db:
            if name!=user['name']:db.execute('UPDATE users SET name=? WHERE id=?',(name,uid))
            if upload:
                db.execute('INSERT OR REPLACE INTO user_avatars VALUES(?,?,?)',(uid,raw,ctype))
                db.execute('UPDATE users SET avatar=? WHERE id=?',('/api/member-avatar?user='+uid+'&v='+secrets.token_hex(5),uid))
        return p.reply(h,message='프로필이 저장되었습니다.')
    if path=='/api/me/email':
        raise p.RequestError('제공하지 않는 기능입니다.',410)
    if path=='/api/me/withdraw':
        uid=p.require(user)
        if user.get('is_admin'):raise p.RequestError('관리자 계정은 삭제할 수 없습니다.',403)
        password=str(data.get('password',data.get('current_password','')))
        if not user['password'] or not secrets.compare_digest(accounts.password_hash(password,user['password'].split(':')[0]),user['password']):raise p.RequestError('비밀번호를 확인해 주세요.',403)
        with database.connect() as db:
            db.execute('DELETE FROM sessions WHERE user_id=?',(uid,));db.execute('DELETE FROM user_avatars WHERE user_id=?',(uid,));db.execute('DELETE FROM users WHERE id=?',(uid,))
        return p.reply(h,message='탈퇴되었습니다.')
    if path in ('/api/account/forgot-password','/api/account/reset-password'):
        raise p.RequestError('제공하지 않는 기능입니다.',410)
    raise p.RequestError('없는 계정 요청입니다.',404)


def library_api(h,path,user,data,q):
    uid=p.require(user);post=h.command!='GET'
    if path.startswith('/api/history'):
        with database.connect() as db:
            if path.endswith('/add'):
                v=lookup_video(data.get('videoId'))
                db.execute('INSERT OR REPLACE INTO library_items VALUES(?,?,?,?)',(uid,v['id'],'history',time.time()))
            elif path.endswith('/clear'):db.execute("DELETE FROM library_items WHERE user_id=? AND kind='history'",(uid,))
            elif path.endswith('/delete'):
                ids=data.get('videoIds',[])
                if not isinstance(ids,list) or len(ids)>200:raise p.RequestError('삭제할 항목을 확인해 주세요.')
                for vid in ids:db.execute("DELETE FROM library_items WHERE user_id=? AND video_id=? AND kind='history'",(uid,str(vid)))
            elif path=='/api/history':
                rows=db.execute("SELECT v.*,i.created AS saved FROM library_items i JOIN videos v ON v.id=i.video_id WHERE i.user_id=? AND i.kind='history' ORDER BY i.created DESC LIMIT 500",(uid,)).fetchall()
                return p.reply(h,videos=[p.video(dict(r)) for r in rows])
        return p.reply(h)
    if path.startswith('/api/bookmarks'):
        vid=data.get('videoId') if post else q.get('videoId','')
        with database.connect() as db:
            if path.endswith('/check'):
                return p.reply(h,bookmarked=bool(db.execute("SELECT 1 FROM library_items WHERE user_id=? AND video_id=? AND kind='bookmark'",(uid,vid)).fetchone()))
            if post:
                v=lookup_video(vid)
                old=db.execute("SELECT 1 FROM library_items WHERE user_id=? AND video_id=? AND kind='bookmark'",(uid,vid)).fetchone()
                if old:db.execute("DELETE FROM library_items WHERE user_id=? AND video_id=? AND kind='bookmark'",(uid,vid))
                else:db.execute('INSERT INTO library_items VALUES(?,?,?,?)',(uid,vid,'bookmark',time.time()))
                return p.reply(h,bookmarked=not bool(old))
            rows=db.execute("SELECT v.*,i.created AS saved FROM library_items i JOIN videos v ON v.id=i.video_id WHERE i.user_id=? AND i.kind='bookmark' ORDER BY i.created DESC LIMIT 500",(uid,)).fetchall()
            return p.reply(h,bookmarks=[dict(p.video(dict(r)),date=p.iso(r['saved'])) for r in rows])
    if path=='/api/playlists':
        if post:
            title=p.text(data.get('title'),100);pid=secrets.token_hex(16);privacy=data.get('privacy','private')
            if privacy not in ('private','public'):raise p.RequestError('공개 범위를 확인해 주세요.')
            with database.connect() as db:db.execute('INSERT INTO library_playlists VALUES(?,?,?,?)',(pid,uid,title,time.time()))
            p.record('playlist-settings',uid,pid,dict(privacy=privacy),rid='playlist-'+pid)
            return p.reply(h,playlist_id=pid,privacy=privacy)
        result=library.dataset({'view':['playlists']},user)['items']
        return p.reply(h,playlists=[dict(r,playlist_id=r['id'],video_count=r['count'],created_at=p.iso(r['created']),privacy=(p.records('playlist-settings',parent=r['id']) or [{}])[0].get('privacy','private')) for r in result])
    match=re.fullmatch(r'/api/playlists/([\w-]+)(/videos)?',path)
    if match:
        pid=match[1]
        with database.connect() as db:
            owned=db.execute('SELECT * FROM library_playlists WHERE id=? AND user_id=?',(pid,uid)).fetchone()
            if not owned:raise p.RequestError('본인의 재생목록만 사용할 수 있습니다.',403)
            if h.command=='DELETE':db.execute('DELETE FROM library_playlist_items WHERE source_id=?',(pid,));db.execute('DELETE FROM library_playlists WHERE id=?',(pid,));return p.reply(h)
            if post:
                lookup_video(data.get('videoId'));db.execute('INSERT OR IGNORE INTO library_playlist_items VALUES(?,?,?)',(pid,data['videoId'],time.time()));return p.reply(h,added=True)
        rows=library.dataset({'view':['playlists'],'playlist':[pid]},user)['items']
        return p.reply(h,videos=[p.video(r) for r in rows],title=owned['title'])
    raise p.RequestError('없는 라이브러리 요청입니다.',404)


def attendance(h,path,user):
    now=dt.datetime.now(p.KST);today=now.date().isoformat();uid=user['id'] if user else ''
    if path=='/api/attendance/status':
        with database.connect() as db:done=db.execute('SELECT 1 FROM luminous_attendance WHERE id=?',(uid+':'+today,)).fetchone()
        return p.reply(h,logged_in=bool(user),attended_today=bool(done))
    p.require(user)
    if path=='/api/attendance/mark':
        with p.mutex,database.connect() as db:
            if db.execute('SELECT 1 FROM luminous_attendance WHERE id=?',(uid+':'+today,)).fetchone():raise p.RequestError('이미 오늘 출석을 완료했습니다.',409)
            yesterday=(now.date()-dt.timedelta(days=1)).isoformat()
            old=db.execute('SELECT consecutive FROM luminous_attendance WHERE id=?',(uid+':'+yesterday,)).fetchone()
            consecutive=old[0]+1 if old else 1;points=5+(20 if consecutive%7==0 else 0)
            db.execute('INSERT INTO luminous_attendance VALUES(?,?,?,?,?,?)',(uid+':'+today,uid,today,points,consecutive,time.time()))
            total=db.execute('SELECT COUNT(*) FROM luminous_attendance WHERE user_id=?',(uid,)).fetchone()[0]
        return p.reply(h,total=total,consecutive=consecutive,points_earned=points)
    if path=='/api/attendance/calendar':
        from urllib.parse import parse_qs,urlsplit
        q=parse_qs(urlsplit(h.path).query);year=p.number(q.get('year',[now.year])[0],now.year,9999);month=p.number(q.get('month',[now.month])[0],now.month,12)
        try:start=dt.date(year,month,1)
        except ValueError:raise p.RequestError('날짜를 확인해 주세요.')
        with database.connect() as db:dates=[r[0] for r in db.execute('SELECT day FROM luminous_attendance WHERE user_id=? AND day LIKE ? ORDER BY day',(uid,start.strftime('%Y-%m-')+'%'))]
        return p.reply(h,year=year,month=month,dates=dates)


def points(h,path,user):
    with database.connect() as db:
        totals={r[0]:int(r[1]) for r in db.execute('SELECT user_id,SUM(points) FROM luminous_attendance GROUP BY user_id')}
        for r in db.execute('SELECT user_id,SUM(points) FROM luminous_points GROUP BY user_id'):totals[r[0]]=totals.get(r[0],0)+int(r[1])
        people={r['id']:dict(r) for r in db.execute('SELECT id,name,avatar FROM users')}
    scores=[dict(people[uid],user_id=uid,points=n) for uid,n in sorted(totals.items(),key=lambda x:-x[1])[:100] if uid in people]
    board=[dict(r,rank=i+1,nickname=html.escape(r['name']),picture=r['avatar']) for i,r in enumerate(scores)]
    if path=='/api/leaderboard':return p.reply(h,leaderboard=board,entries=board)
    p.require(user);mine=next((r for r in board if r['user_id']==user['id']),{})
    return p.reply(h,points=mine.get('points',0),rank=mine.get('rank',0),badges=[])


def award(uid,action,source,points):
    rid=hashlib.sha256((action+':'+source).encode()).hexdigest()
    with database.connect() as db:db.execute('INSERT OR IGNORE INTO luminous_points VALUES(?,?,?,?,?)',(rid,uid,action,points,time.time()))


def schedule(h,path,user,data,q):
    if h.command=='GET':
        now=dt.datetime.now(p.KST).date()
        try:start=dt.date.fromisoformat(q['start']) if q.get('start') else now-dt.timedelta(days=(now.weekday()+1)%7)
        except ValueError:raise p.RequestError('날짜 형식을 확인해 주세요.')
        begin=dt.datetime.combine(start,dt.time(),p.KST).timestamp();end=begin+7*86400
        with database.connect() as db:rows=[dict(r) for r in db.execute('SELECT * FROM schedule_events WHERE starts_at>=? AND starts_at<? ORDER BY starts_at',(begin,end))]
        names={str(r['position']):r['name'] for r in database.profiles()}
        result=[]
        for r in rows:
            t=dt.datetime.fromtimestamp(r['starts_at'],p.KST)
            result.append(dict(r,member_name=names.get(r['channel_id'],r['channel_id']),event_date=t.date().isoformat(),event_time=t.strftime('%H:%M'),is_day_off=r['title']=='휴방'))
        return p.reply(h,start=start.isoformat(),end=(start+dt.timedelta(days=6)).isoformat(),schedules=result)
    p.require(user,True);rid=path.rsplit('/',1)[-1]
    with database.connect() as db:
        if h.command=='DELETE':db.execute('DELETE FROM schedule_events WHERE id=?',(rid,));return p.reply(h)
        member=p.text(data.get('member_name'),50);title=p.text(data.get('title') or ('휴방' if data.get('is_day_off') else ''),255)
        try:at=dt.datetime.fromisoformat(str(data.get('event_date'))+'T'+str(data.get('event_time') or '00:00')).replace(tzinfo=p.KST).timestamp()
        except ValueError:raise p.RequestError('날짜와 시간을 확인해 주세요.')
        channel=next((str(r['position']) for r in database.profiles() if member in r['name']),member)
        if h.command=='POST':rid=secrets.token_hex(16)
        elif not db.execute('SELECT 1 FROM schedule_events WHERE id=?',(rid,)).fetchone():raise p.RequestError('일정이 없습니다.',404)
        db.execute('INSERT OR REPLACE INTO schedule_events VALUES(?,?,?,?,?,?,?)',(rid,title,channel,at,0,'',''))
    return p.reply(h,id=rid)
