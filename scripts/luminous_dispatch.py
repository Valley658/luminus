"""HTTP contract dispatch for the copied luminous-master frontend."""
import datetime as dt
import hashlib
import html
import json
import re
import secrets
import time
from urllib.parse import quote
import accounts
import database
import library
import luminous_port as p
import luminous_api as core
import luminous_social as social


def api(h,path,user,data,q):
    post=h.command!='GET'
    if path=='/api/image-cache':
        import image_cache
        image_cache.serve(h,q.get('id',''));return
    if h.command=='GET' and (path in ('/api/register','/api/login','/api/me/profile','/api/me/email','/api/me/nickname','/api/me/withdraw','/api/channel/subscribe','/api/delete_comments')
                            or re.search(r'/(?:add|clear|delete|delete-all|reset|ip-ban|ip-unban|staff|subscribe|backfill-webp|upload|update|mark|read|resolve|suspend|unsuspend|withdraw|reset-password|forgot-password|set-password|post|like|react|tag)$',path)):
        raise p.RequestError('POST 요청이 필요합니다.',405)
    if path in ('/api/me','/api/staff-roles','/api/register','/api/login') or path.startswith(('/api/me/','/api/account/')):
        core.account(h,path,user,data);return
    if path=='/api/member-avatar':
        uid=q.get('user','')[:100]
        with database.connect() as db:row=db.execute('SELECT image,content_type FROM user_avatars WHERE user_id=?',(uid,)).fetchone()
        if not row:raise p.RequestError('사진이 없습니다.',404)
        raw=bytes(row['image']);h.send_response(200);h.send_header('Content-Type',row['content_type']);h.send_header('Content-Length',str(len(raw)));h.send_header('X-Content-Type-Options','nosniff');h.send_header('Cache-Control','private, max-age=60');h.end_headers();h.wfile.write(raw);return
    if path=='/api/content-image':
        with database.connect() as db:row=db.execute('SELECT image,content_type FROM luminous_images WHERE id=?',(q.get('id','')[:100],)).fetchone()
        if not row:raise p.RequestError('사진이 없습니다.',404)
        raw=bytes(row['image']);h.send_response(200);h.send_header('Content-Type',row['content_type']);h.send_header('Content-Length',str(len(raw)));h.send_header('X-Content-Type-Options','nosniff');h.send_header('Cache-Control','public, max-age=3600');h.end_headers();h.wfile.write(raw);return
    if path.startswith(('/api/history','/api/bookmarks','/api/playlists')):
        # The bookmark indicator is useful before login as well.
        if path=='/api/bookmarks/check' and not user:p.reply(h,bookmarked=False);return
        core.library_api(h,path,user,data,q);return
    if path.startswith('/api/attendance/'):
        core.attendance(h,path,user);return
    if path in ('/api/points/my','/api/leaderboard'):core.points(h,path,user);return
    if path.startswith('/api/schedules'):core.schedule(h,path,user,data,q);return
    if path.startswith('/api/notifications'):social.notifications(h,path,user,data);return
    if path in ('/api/fanart','/api/fanart/my','/api/fanart/upload','/api/fanart/update'):social.fanart(h,path,user,data,q);return
    if path.startswith('/api/highlights'):social.highlights(h,path,user,data,q);return
    if path.startswith(('/api/community/','/api/fanart/','/api/comments/','/api/youtube_comments/','/api/comment_preview','/api/video/')) or path in ('/api/all_comments','/api/delete_comments'):
        social.discussion(h,path,user,data,q);return
    if path.startswith('/api/profile/'):
        uid=p.require(user)
        if path.endswith('my_posts'):p.reply(h,posts=social.enriched(p.records('community',owner=uid),user));return
        if path.endswith('my_videos'):
            rows=library.dataset({'view':['gallery'],'mine':['1']},user)['items'];p.reply(h,videos=[dict(r,video_url=r['image'],thumbnail=r['image'],date=p.iso(r['created'])) for r in rows]);return
        if path.endswith('liked_videos'):
            with database.connect() as db:
                rows=[dict(r) for r in db.execute("SELECT v.* FROM videos v JOIN luminous_reactions r ON r.target=v.id WHERE r.user_id=? AND r.action='like' LIMIT 100",(uid,))]
            p.reply(h,videos=[p.video(r) for r in rows]);return
    if path.startswith('/api/cheers/'):
        member=p.text(path.rsplit('/',1)[-1],50)
        if post:
            uid=p.require(user);day=dt.datetime.now(p.KST).date().isoformat()
            rid='cheer-'+hashlib.sha256((uid+':'+member+':'+day).encode()).hexdigest()
            with p.mutex:
                if p.records('cheer',parent=member,owner=uid) and any(r['id']==rid for r in p.records('cheer',parent=member,owner=uid)):raise p.RequestError('오늘은 이미 응원했습니다.',409)
                p.record('cheer',uid,member,dict(member_name=member),rid=rid)
        with database.connect() as db:n=db.execute("SELECT COUNT(*) FROM luminous_records WHERE kind='cheer' AND parent=?",(member,)).fetchone()[0]
        p.reply(h,count=n,cheers=n);return
    if path.startswith('/api/search'):
        search(h,path,user,data,q);return
    if path in ('/api/videos/random_scroll','/api/videos/recommend_random','/api/videos/home_shorts','/api/m/shorts') or re.fullmatch('/api/videos/[^/]+',path):
        video_feed(h,path,user,data,q);return
    if path=='/api/live_status':
        from luminous_live import status
        p.reply(h,status());return
    if path.startswith('/api/channel/'):
        channel(h,path,user,data,q);return
    if path.startswith('/api/admin/'):
        from luminous_admin import api as admin_api
        admin_api(h,path,user,data,q);return
    if path.startswith(('/api/inquiries','/api/reports')):
        reports(h,path,user,data);return
    if path.startswith('/api/code/'):
        from luminous_code import api as code_api
        code_api(h,path,user,data,q);return
    if path.startswith('/api/drive_'):
        from luminous_drive import api as drive_api
        drive_api(h,path,user,q);return
    if path.startswith('/api/video_storyboard/'):
        # Official thumbnail rather than an invalid YouTube storyboard response.
        v=core.lookup_video(path.rsplit('/',1)[-1]);p.reply(h,thumbnail=v['thumbnail'],storyboard=None);return
    if path.startswith('/api/kirinuki/'):
        rows,_=p.videos(query=q.get('q',''),offset=p.number(q.get('offset')));p.reply(h,videos=rows,nextPageToken=None);return
    if path=='/api/csp-report':p.reply(h);return
    raise p.RequestError('없는 요청입니다.',404)


def video_feed(h,path,user,data,q):
    shorts=path in ('/api/videos/home_shorts','/api/m/shorts') or q.get('type')=='shorts'
    channel='';member=path.rsplit('/',1)[-1]
    if member not in ('home_shorts','shorts','random_scroll','recommend_random'):
        profile=next((r for r in database.profiles() if member in (r['name'],r['name'].split(' ')[-1],str(r['position']))),None)
        if not profile:raise p.RequestError('채널이 없습니다.',404)
        channel=str(profile['position'])
    offset=p.number(q.get('pageToken',q.get('offset',0)))
    tab={'music':'songs','song':'songs','replay':'live','live':'live','video':'videos'}.get(q.get('type'),'all')
    rows,state=p.videos(channel,q.get('q',''),shorts,offset,q.get('seed',''),tab,q.get('sort','latest') if channel else 'random')
    if path=='/api/videos/recommend_random':
        short_rows,_=p.videos(shorts=True,seed=q.get('seed',''))
        rows=short_rows[:15]+rows
    excluded=data.get('exclude',[]) if h.command=='POST' else q.get('exclude','').split(',')
    if not isinstance(excluded,list):raise p.RequestError('영상 목록을 확인해 주세요.')
    rows=[v for v in rows if v['id'] not in excluded[:10000]]
    p.reply(h,videos=rows,nextPageToken=str(state['nextOffset']) if state['hasMore'] else None,
            remaining=len(rows),has_music=True,has_replay=True,refreshing=state.get('refreshing',False))


def search(h,path,user,data,q):
    if path.endswith('/trending/delete'):
        p.require(user,True);rid=str(data.get('id',''));p.remove(rid,user,'search');p.reply(h);return
    if path.endswith('/trending'):
        rows=p.records('search',limit=500);counts={}
        for r in rows:counts[r['keyword']]=counts.get(r['keyword'],0)+1
        ranked=[dict(keyword=k,count=v,search_count=v) for k,v in sorted(counts.items(),key=lambda x:-x[1])[:10]]
        p.reply(h,trending=ranked,keywords=ranked);return
    query=q.get('q','').strip()[:100]
    with database.connect() as db:
        if path.endswith('local_index'):rows=db.execute('SELECT * FROM videos ORDER BY publishedAt DESC LIMIT 300').fetchall()
        else:
            terms=query.split()[:8];sql=' AND '.join('(LOWER(title) LIKE ? OR LOWER(channelName) LIKE ?)' for _ in terms) or '1=0'
            args=[val for term in terms for val in ('%'+term.lower()+'%','%'+term.lower()+'%')]
            rows=db.execute('SELECT * FROM videos WHERE '+sql+' ORDER BY publishedAt DESC LIMIT 100 OFFSET ?',(*args,p.number(q.get('offset')))).fetchall()
    result=[p.video(dict(r)) for r in rows]
    if path.endswith(('suggest','local_index')):
        items=[dict(type='video',text=r['title'],videoId=r['id'],thumbnail=r['thumbnail']) for r in result]
        items += [dict(type='member',text=r['name']) for r in database.profiles() if query in r['name']]
        p.reply(h,suggestions=items[:12],items=items);return
    if query and len(query)<=60 and not re.search(r"[<>;'`]|--|\b(?:select|union|drop|sleep)\b",query,re.I):
        day=dt.datetime.now(p.KST).date().isoformat();viewer=(user or {}).get('id') or (accounts.visitor_ip(h) or h.client_address[0])
        rid=hashlib.sha256((viewer+':'+query+':'+day).encode()).hexdigest()
        p.record('search',(user or {}).get('id',''),data=dict(keyword=query),rid=rid)
    p.reply(h,videos=result,results=[dict(r,type='video') for r in result],total=len(result),has_more=len(result)==100)


def channel(h,path,user,data,q):
    cid=str(data.get('channel_id')) if path.endswith('/subscribe') else path.rsplit('/',1)[-1]
    with database.connect() as db:
        owner=db.execute('SELECT id,name,avatar,created FROM users WHERE id=? OR handle=?',(cid,cid)).fetchone()
    if not owner:raise p.RequestError('채널이 없습니다.',404)
    rid='subscription-'+hashlib.sha256((str((user or {}).get('id'))+':'+owner['id']).encode()).hexdigest()
    if path.endswith('/subscribe'):
        uid=p.require(user)
        if uid==owner['id']:raise p.RequestError('본인 채널은 구독할 수 없습니다.')
        with p.mutex,database.connect() as db:
            old=db.execute('SELECT 1 FROM luminous_records WHERE id=?',(rid,)).fetchone()
            if old:db.execute('DELETE FROM luminous_records WHERE id=?',(rid,))
            else:db.execute('INSERT INTO luminous_records VALUES(?,?,?,?,?,?)',(rid,'subscription',uid,owner['id'],'{}',time.time()))
    subscriptions=p.records('subscription',parent=owner['id'],limit=10000);subscribed=any(r['id']==rid for r in subscriptions)
    result=dict(channel_id=owner['id'],is_owner=bool(user and user['id']==owner['id']),is_subscribed=subscribed,subscribed=subscribed,subscriber_count=len(subscriptions),
                channel=dict(nickname=html.escape(owner['name']),picture=owner['avatar'],joined_at=p.iso(owner['created'])[:10],description='',link='',country='',subscriber_count=len(subscriptions),video_count=0,view_count=0),
                videos=[],posts=social.enriched(p.records('community',owner=owner['id']),user))
    p.reply(h,result)


def reports(h,path,user,data):
    uid=p.require(user)
    if path=='/api/inquiries':
        title=p.text(data.get('title',data.get('subject')),200);content=p.text(data.get('content'),5000)
        rid=p.record('inquiry',uid,data=dict(title=title,content=content,resolved=False))
        p.reply(h,id=rid,message='문의가 접수되었습니다.');return
    p.require(user,True)
    if path.endswith('/resolve'):
        rid=path.split('/')[-2];row=p.item(rid,'report');row['resolved']=True;p.record('report',row['user_id'],row['parent'],row,rid=rid);p.reply(h);return
    p.reply(h,reports=p.records('report'))
