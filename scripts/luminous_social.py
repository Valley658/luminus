"""Python community, comments, fan art, highlights and notifications services."""
import datetime as dt
import html
import io
import json
import secrets
import time
import re
from PIL import Image
import accounts
import comments
import database
import luminous_port as p
from luminous_api import lookup_video, counts, reaction, award


def notify(owner,actor,kind,target,content=''):
    if owner and actor and owner!=actor['id']:
        p.record('notification',owner,target,dict(actor_nickname=html.escape(actor['name']),type=kind,content=content[:200],is_read=False))


def enriched(rows,user):
    result=[]
    with database.connect() as db:
        people={r['id']:dict(r) for r in db.execute('SELECT id,name,avatar,is_admin FROM users')}
    for row in rows:
        author=people.get(row.get('user_id'),{})
        avatar=author.get('avatar','')
        if avatar.startswith('/api/auth/avatar'):avatar='/api/member-avatar?user='+row['user_id']
        with database.connect() as db:
            comment_count=db.execute('SELECT COUNT(*) FROM luminous_records WHERE kind IN (?,?) AND parent=?',('community-comment','fanart-comment',row['id'])).fetchone()[0]
        result.append(dict(row,nickname=html.escape(author.get('name','탈퇴한 회원')),picture=avatar,images=row.get('images',[]),video_url=row.get('video_url',''),comments_count=comment_count,
                           is_owner=bool(user and user['id']==row.get('user_id')),is_admin=bool(author.get('is_admin')),**counts(row['id'],user)))
    return result


def discussion(h,path,user,data,q):
    post=h.command!='GET'
    match=re.fullmatch(r'/api/(fanart|community)/([^/]+)/comments',path)
    # Community's original route is /api/community/comments/{postID}.
    alternate=re.fullmatch(r'/api/community/comments/([^/]+)',path)
    if match or alternate:
        kind,target=(match[1],match[2]) if match else ('community',alternate[1])
        parent=p.item(target,kind)
        if post:
            p.require(user);body=p.text(data.get('content'),2000)
            rid=p.record(kind+'-comment',user['id'],target,dict(content=body))
            award(user['id'],'comment',rid,1)
            notify(parent['user_id'],user,'comment_on_'+kind,target,body)
            return p.reply(h,id=rid,message='댓글이 등록되었습니다.')
        return p.reply(h,comments=enriched(p.records(kind+'-comment',parent=target),user))
    match=re.fullmatch(r'/api/(fanart|community)/comments/([^/]+)/delete',path)
    if match:p.remove(match[2],user,match[1]+'-comment');return p.reply(h)
    match=re.fullmatch(r'/api/(fanart|comments)/([^/]+)/react',path)
    if match:
        target=match[2]
        if match[1]=='fanart':p.item(target,'fanart')
        else:
            with database.connect() as db:exists=db.execute('SELECT 1 FROM site_comments WHERE id=?',(target,)).fetchone()
            if not exists:raise p.RequestError('댓글이 없습니다.',404)
        action=str(data.get('reaction',''))
        if action=='delete':
            if match[1]=='fanart':
                parent=p.item(target,'fanart');p.remove(target,user,'fanart')
                with database.connect() as db:db.execute('DELETE FROM library_gallery WHERE id=?',(parent.get('gallery_id',target),))
            else:
                with database.connect() as db:
                    owner=db.execute('SELECT user_id FROM site_comments WHERE id=?',(target,)).fetchone();p.require(user)
                    if owner[0]!=user['id'] and not user.get('is_admin'):raise p.RequestError('권한이 없습니다.',403)
                    db.execute('DELETE FROM site_comments WHERE id=?',(target,))
            return p.reply(h)
        with p.mutex:result=reaction(target,user,action)
        if action=='report':p.record('report',p.require(user),target,dict(reason=p.text(data.get('reason','신고'),500),content_type=match[1],resolved=False))
        return p.reply(h,result)
    if path=='/api/community/post':
        p.require(user);body=p.text(data.get('content'),10000);member=p.text(data.get('member_name',data.get('member','전체')),50)
        images=data.get('images',data.get('image',[]));images=images if isinstance(images,list) else [images]
        if len(images)>5:raise p.RequestError('사진은 최대 5장입니다.')
        image_urls=[save_image(raw) for raw in images if raw]
        video_url=str(data.get('video_url','')).strip()
        if video_url and not re.fullmatch(r'https://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)[\w-]{11}',video_url):raise p.RequestError('YouTube 영상 주소를 확인해 주세요.')
        rid=p.record('community',user['id'],member,dict(content=body,title=p.text(data.get('title',''),200,False),member_name=member,images=image_urls,video_url=video_url))
        award(user['id'],'community',rid,3)
        return p.reply(h,id=rid,message='게시글이 등록되었습니다.')
    match=re.fullmatch(r'/api/community/([^/]+)/posts',path)
    if match:return p.reply(h,posts=enriched(p.records('community',parent=match[1],offset=p.number(q.get('offset'))),user))
    if path=='/api/community/like':
        target=str(data.get('target_id',data.get('post_id',data.get('postId',''))))
        if data.get('target_type')=='video':lookup_video(target)
        else:
            kind='community-comment' if data.get('target_type')=='comment' else 'community';p.item(target,kind)
        with p.mutex:r=reaction(target,user,data.get('reaction','like'))
        return p.reply(h,r)
    match=re.fullmatch(r'/api/comments/([\w-]{11})',path)
    if match:
        vid=match[1]
        if post:
            p.require(user);lookup_video(vid);upload=data.get('image');body=p.text(data.get('content'),2000,not bool(upload));rid=secrets.token_hex(16)
            image_url=save_image(upload) if upload else ''
            with database.connect() as db:db.execute('INSERT INTO site_comments VALUES(?,?,?,?,?)',(rid,vid,user['id'],body,time.time()))
            if image_url:p.record('comment-image',user['id'],rid,dict(image_url=image_url),rid='image-'+rid)
            award(user['id'],'comment',rid,1)
            return p.reply(h,id=rid,message='댓글이 등록되었습니다.')
        with database.connect() as db:
            rows=[dict(r) for r in db.execute('SELECT id,user_id,body AS content,created FROM site_comments WHERE video_id=? ORDER BY created DESC LIMIT 100 OFFSET ?',(vid,p.number(q.get('offset'))))]
        result=enriched([dict(r,date=p.iso(r['created']),image_url=(p.records('comment-image',parent=r['id']) or [{}])[0].get('image_url','')) for r in rows],user)
        return p.reply(h,comments=result,has_more=len(result)==100)
    match=re.fullmatch(r'/api/youtube_comments/([\w-]{11})',path)
    if match:
        result=comments.youtube(match[1],q.get('pageToken',''))
        rows=[dict(r,nickname=r['author'],content=r['body'],picture=r['avatar'],date=r['created'],is_youtube=True,author_hidden=True) for r in result['items']]
        return p.reply(h,comments=rows,nextPageToken=result['nextCursor'],refreshing=result['refreshing'],message=result.get('message',''))
    if path in ('/api/comment_previews',) or path.startswith('/api/comment_preview/'):
        ids=q.get('ids','').split(',')[:50] if path.endswith('previews') else [path.rsplit('/',1)[-1]]
        result={}
        with database.connect() as db:
            for vid in ids:
                row=db.execute('SELECT c.body,u.name FROM site_comments c JOIN users u ON u.id=c.user_id WHERE c.video_id=? ORDER BY c.created DESC LIMIT 1',(vid,)).fetchone()
                if row:result[vid]=dict(nickname=html.escape(row['name']),content=html.escape(row['body']),source='site')
        return p.reply(h,previews=result,preview=result.get(ids[0]) if ids else None)
    if path=='/api/all_comments':
        p.require(user,True)
        with database.connect() as db:rows=[dict(r) for r in db.execute('SELECT *,body AS content FROM site_comments ORDER BY created DESC LIMIT 100')]
        return p.reply(h,comments=enriched(rows,user))
    if path=='/api/delete_comments':
        p.require(user,True);ids=data.get('ids',data.get('comment_ids',[]))
        if not isinstance(ids,list) or len(ids)>200:raise p.RequestError('댓글 목록을 확인해 주세요.')
        with database.connect() as db:
            for rid in ids:db.execute('DELETE FROM site_comments WHERE id=?',(str(rid),))
        return p.reply(h)
    match=re.fullmatch(r'/api/video/([^/]+)/reaction',path)
    if match:
        lookup_video(match[1])
        return p.reply(h,reaction(match[1],user,data.get('reaction')) if post else counts(match[1],user))
    raise p.RequestError('없는 댓글 요청입니다.',404)


def validate_image(raw):
    if not isinstance(raw,bytes) or not raw or len(raw)>5*1024*1024:raise p.RequestError('최대 5MB의 사진을 선택해 주세요.')
    try:
        with Image.open(io.BytesIO(raw)) as image:
            fmt=image.format
            if fmt not in ('JPEG','PNG','WEBP','GIF'):raise ValueError()
            if min(image.size)<1 or max(image.size)>4096:raise ValueError()
            total=0
            for i in range(301):
                try:image.seek(i)
                except EOFError:break
                total+=image.width*image.height
                if i>=300 or total>100000000:raise ValueError()
                image.load()
        if fmt!='GIF':
            with Image.open(io.BytesIO(raw)) as image:
                out=io.BytesIO();image.convert('RGBA').save(out,format='PNG');raw=out.getvalue();fmt='PNG'
    except (ValueError,OSError,Image.DecompressionBombError):raise p.RequestError('정상적인 JPEG·PNG·WebP·GIF 파일을 선택해 주세요.')
    return raw,{'JPEG':'image/jpeg','PNG':'image/png','WEBP':'image/webp','GIF':'image/gif'}[fmt]


def save_image(raw):
    raw,ctype=validate_image(raw);rid=secrets.token_hex(16)
    with database.connect() as db:db.execute('INSERT INTO luminous_images VALUES(?,?,?)',(rid,raw,ctype))
    return '/api/content-image?id='+rid


def fanart(h,path,user,data,q):
    if path=='/api/fanart/upload':
        uid=p.require(user);title=p.text(data.get('title'),100);description=p.text(data.get('description',''),2000,False)
        raw,ctype=validate_image(data.get('image') or data.get('file') or data.get('fanart'))
        rid=secrets.token_hex(16)
        with database.connect() as db:db.execute('INSERT INTO library_gallery VALUES(?,?,?,?,?,?)',(rid,uid,title,raw,ctype,time.time()))
        import image_cache
        try:image_cache.store('gallery:'+rid,raw)
        except (ValueError,OSError):image_cache.enqueue('gallery',rid)
        p.record('fanart',uid,data=dict(title=title,description=description,image_url='/api/library/image?id='+rid,gallery_id=rid),rid=rid)
        award(uid,'fanart',rid,10)
        return p.reply(h,id=rid,message='업로드되었습니다.')
    if path=='/api/fanart/update':
        p.require(user);rid=str(data.get('id',''));row=p.item(rid,'fanart')
        if row['user_id']!=user['id'] and not user.get('is_admin'):raise p.RequestError('본인의 게시물만 수정할 수 있습니다.',403)
        title=p.text(data.get('title'),100);desc=p.text(data.get('description',''),2000,False)
        p.record('fanart',row['user_id'],data=dict(title=title,description=desc,image_url=row['image_url'],gallery_id=row['gallery_id']),rid=rid)
        with database.connect() as db:db.execute('UPDATE library_gallery SET title=? WHERE id=?',(title,row['gallery_id']))
        return p.reply(h)
    mine=path=='/api/fanart/my'
    if mine:p.require(user)
    rows=p.records('fanart',owner=user['id'] if mine else None,offset=p.number(q.get('offset')))
    # Include gallery uploads saved through the previous Python UI as well.
    known={r['id'] for r in rows}
    existing=library_dataset_gallery(user,mine)
    for r in existing:
        if r['id'] not in known:rows.append(dict(r,image_url=r['image'],description='',user_id=r.get('user_id',''),date=p.iso(r['created'])))
    import image_cache
    for row in rows:
        row['image_url']=image_cache.gallery(row.get('gallery_id',row['id']))
        row['thumbnail_url']=row['image_url']
    return p.reply(h,fanarts=enriched(rows,user),has_more=len(rows)>=100)


def library_dataset_gallery(user,mine):
    import library
    return library.dataset({'view':['gallery'],'mine':['1' if mine else '0']},user)['items']


def highlights(h,path,user,data,q):
    if path=='/api/highlights/tag':
        uid=p.require(user);v=lookup_video(data.get('videoId'));tag=p.text(data.get('tag'),200)
        start=p.number(data.get('startSeconds'),limit=86400);duration=max(5,p.number(data.get('durationSeconds'),15,30))
        rid=secrets.token_hex(16)
        with database.connect() as db:db.execute('INSERT INTO library_highlights VALUES(?,?,?,?,?,?)',(rid,uid,v['id'],tag,start,time.time()))
        p.record('highlight-meta',uid,rid,dict(duration_seconds=duration),rid='highlight-'+rid)
        return p.reply(h,clip_id=rid,id=rid,status='ready',message='하이라이트가 저장되었습니다.')
    rid=path.removeprefix('/api/highlights/') if path.startswith('/api/highlights/') else ''
    with database.connect() as db:
        rows=[dict(r) for r in db.execute('SELECT h.*,v.thumbnail,v.title AS source_title,v.channelName FROM library_highlights h LEFT JOIN videos v ON v.id=h.video_id'+(' WHERE h.id=?' if rid else '')+' ORDER BY h.created DESC LIMIT 100',((rid,) if rid else ()))]
    result=[dict(r,videoId=r['video_id'],source_video_id=r['video_id'],tag=r['title'],start_seconds=r['starts_at'],duration_seconds=(p.records('highlight-meta',parent=r['id']) or [{}])[0].get('duration_seconds',15),status='ready',created_at=p.iso(r['created'])) for r in rows]
    return p.reply(h,clips=result,highlights=result,clip=result[0] if rid and result else None)


def notifications(h,path,user,data):
    uid=p.require(user)
    rows=p.records('notification',owner=uid)
    if path.endswith('/read'):
        selected=data.get('ids',[])
        if not isinstance(selected,list):raise p.RequestError('알림 목록을 확인해 주세요.')
        for row in rows:
            if not selected or row['id'] in selected:
                row['is_read']=True;p.record('notification',uid,row['parent'],row,rid=row['id'])
        return p.reply(h)
    unread=sum(not r.get('is_read') for r in rows)
    if path.endswith('/stream'):
        body=('retry: 10000\nevent: notifications\ndata: '+json.dumps(dict(unread_count=unread,notifications=rows),ensure_ascii=False)+'\n\n').encode()
        h.send_response(200);h.send_header('Content-Type','text/event-stream');h.send_header('Cache-Control','no-store');h.send_header('Content-Length',str(len(body)));h.end_headers();h.wfile.write(body);return
    return p.reply(h,notifications=rows,unread_count=unread)
