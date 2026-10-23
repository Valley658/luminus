"""Administrator operations for the imported dashboard, authorized by DB flags."""
import datetime as dt
import html
import ipaddress
import json
import re
import secrets
import time
import accounts
import database
import admin
import admin_operations as ops
import luminous_port as p


def api(h,path,user,data,q):
    p.require(user,True)
    if path=='/api/admin/stats':
        with database.connect() as db:
            users=db.execute('SELECT COUNT(*) FROM users').fetchone()[0]
            new=db.execute('SELECT COUNT(*) FROM users WHERE created>?',(time.time()-14*86400,)).fetchone()[0]
            art=db.execute('SELECT COUNT(*) FROM library_gallery').fetchone()[0]
            comments=db.execute('SELECT COUNT(*) FROM site_comments').fetchone()[0]
        searches=p.records('search',limit=1000);keywords={}
        for r in searches:keywords[r['keyword']]=keywords.get(r['keyword'],0)+1
        def daily(table):
            with database.connect() as db:rows=db.execute('SELECT created FROM '+table+' WHERE created>?',(time.time()-14*86400,)).fetchall()
            days={}
            for r in rows:
                day=p.iso(r[0])[:10];days[day]=days.get(day,0)+1
            return [dict(date=k,count=v) for k,v in sorted(days.items())]
        reports=p.records('report',limit=10000)
        p.reply(h,totals=dict(total_users=users,new_users_14d=new,total_fanart=art,total_comments=comments),
                top_keywords=[dict(keyword=k,search_count=v) for k,v in sorted(keywords.items(),key=lambda x:-x[1])[:20]],
                fanart_daily=daily('library_gallery'),comments_daily=daily('site_comments'),
                reports_by_status=[dict(status='resolved',c=sum(bool(r.get('resolved')) for r in reports)),dict(status='pending',c=sum(not r.get('resolved') for r in reports))],cheers_by_member=[]);return
    if path=='/api/admin/users':
        page=max(1,p.number(q.get('page'),1));per=30
        with database.connect() as db:
            rows=[dict(r) for r in db.execute('SELECT id,handle,name,avatar,is_admin,created,suspended_until FROM users ORDER BY created DESC LIMIT ? OFFSET ?',(per,(page-1)*per))]
            total=db.execute('SELECT COUNT(*) FROM users').fetchone()[0]
            roles={r[0]:r[1] for r in db.execute('SELECT * FROM user_staff')}
        result=[dict(r,nickname=html.escape(r['name']),login_id=r['handle'],picture=r['avatar'],created_at=p.iso(r['created']),is_staff=r['id'] in roles,staff_role=roles.get(r['id'],''),suspended=r['suspended_until']>time.time()) for r in rows]
        p.reply(h,users=result,total=total,page=page,per_page=per);return
    match=re.fullmatch(r'/api/admin/users/([\w-]+)(?:/(staff|suspend|unsuspend|summary|ip-history))?',path)
    if match:
        uid,action=match.groups()
        with database.connect() as db:target=db.execute('SELECT id,name,is_admin FROM users WHERE id=?',(uid,)).fetchone()
        if not target:raise p.RequestError('회원이 없습니다.',404)
        if action in ('summary','ip-history'):
            rows=p.records('login',owner=uid)
            if action=='ip-history':p.reply(h,items=rows,history=rows);return
            with database.connect() as db:
                counts={table:db.execute('SELECT COUNT(*) FROM '+table+' WHERE user_id=?',(uid,)).fetchone()[0] for table in ('site_comments','library_gallery','library_highlights')}
            p.reply(h,user=dict(target),summary=counts);return
        if h.command=='GET':raise p.RequestError('변경 요청 방식을 확인해 주세요.',405)
        if target['is_admin'] or uid==user['id']:raise p.RequestError('관리자 계정은 변경하거나 삭제할 수 없습니다.',403)
        with database.connect() as db:
            if h.command=='DELETE' and not action:
                db.execute('DELETE FROM sessions WHERE user_id=?',(uid,));db.execute('DELETE FROM user_avatars WHERE user_id=?',(uid,));db.execute('DELETE FROM users WHERE id=?',(uid,))
            elif action=='staff':
                if data.get('is_staff'):db.execute('INSERT OR REPLACE INTO user_staff VALUES(?,?)',(uid,p.text(data.get('staff_role','스태프'),40)))
                else:db.execute('DELETE FROM user_staff WHERE user_id=?',(uid,))
            elif action in ('suspend','unsuspend'):
                until=0 if action=='unsuspend' else time.time()+86400*max(1,p.number(data.get('days'),30,3650))
                db.execute('UPDATE users SET suspended_until=? WHERE id=?',(until,uid))
                if until:db.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
            else:raise p.RequestError('잘못된 변경 요청입니다.')
        ops.audit(user,action or 'delete-user',uid);p.reply(h);return
    if path=='/api/admin/audit-log':
        with database.connect() as db:rows=[dict(r) for r in db.execute('SELECT a.*,u.name AS nickname FROM admin_audit a LEFT JOIN users u ON u.id=a.actor ORDER BY a.created DESC LIMIT 200')]
        p.reply(h,logs=[dict(r,created_at=p.iso(r['created']),target_id=r['target'],target_type='site') for r in rows]);return
    if path=='/api/admin/server-log':
        lines=[];size=0
        for name in ('web-server.log','server-error.log'):
            f=p.ROOT/'logs'/name
            if f.is_file():
                size+=f.stat().st_size
                with f.open('rb') as stream:stream.seek(max(0,f.stat().st_size-150000));content=stream.read().decode(errors='replace')
                for value in accounts.config().values():
                    if len(value)>12:content=content.replace(value,'[REDACTED]')
                content=re.sub(r'(https?://\S*[?&](?:key|token|code)=)\S+',r'\1[REDACTED]',content,flags=re.I)
                lines+=content.splitlines()[-200:]
        p.reply(h,lines=lines[-400:],size=size);return
    if path=='/api/admin/logs/reset':
        for f in (p.ROOT/'logs').glob('*.log'):
            with f.open('w',encoding='utf-8'):pass
        ops.audit(user,'reset-logs');p.reply(h);return
    if path=='/api/admin/db-overview':
        tables=('users','videos','channels','sessions','site_comments','library_gallery','library_highlights','library_playlists','schedule_events','luminous_records','luminous_attendance')
        with database.connect() as db:rows=[dict(name=t,count=db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0]) for t in tables]
        p.reply(h,groups=[dict(label='Python/MySQL',tables=rows)],tables=rows);return
    if path in ('/api/admin/ip-ban','/api/admin/ip-unban'):
        try:ip=str(ipaddress.ip_address(data.get('ip','')))
        except ValueError:raise p.RequestError('IP 주소를 확인해 주세요.')
        if ipaddress.ip_address(ip).is_loopback:raise p.RequestError('서버 내부 주소는 차단할 수 없습니다.')
        with database.connect() as db:
            if path.endswith('unban'):db.execute('DELETE FROM ip_bans WHERE ip=?',(ip,))
            else:db.execute('INSERT OR REPLACE INTO ip_bans VALUES(?,?,?)',(ip,p.text(data.get('reason','관리자 차단'),200),time.time()))
        ops.rules_time=0;ops.audit(user,path.rsplit('/',1)[-1],ip);p.reply(h);return
    if path.startswith('/api/admin/traffic'):
        ip=path.split('/ip/',1)[-1] if '/ip/' in path else ''
        with database.connect() as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM request_traffic'+(' WHERE ip=?' if ip else '')+' ORDER BY last_seen DESC LIMIT 500',((ip,) if ip else ()))]
            bans=[dict(r) for r in db.execute('SELECT * FROM ip_bans')]
        p.reply(h,items=rows,traffic=rows,ips=rows,bans=bans,total=sum(r['hits'] for r in rows));return
    if path=='/api/admin/rate-limit':p.reply(h,limits=dict(auth_attempts=20,auth_window_seconds=300,write_attempts=60,write_window_seconds=60),active_clients=len(p.attempts));return
    if path.startswith('/api/admin/search-trends'):
        if path.endswith('/delete-all'):
            with database.connect() as db:db.execute("DELETE FROM luminous_records WHERE kind='search'")
            ops.audit(user,'search-trends-clear');p.reply(h);return
        if path.endswith('/delete'):
            rid=str(data.get('id',''));p.remove(rid,user,'search');ops.audit(user,'search-trend-delete',rid);p.reply(h);return
        p.reply(h,items=p.records('search',limit=500),trends=p.records('search',limit=500));return
    if path.startswith(('/api/admin/inquiries','/api/admin/report-flags')):
        kind='inquiry' if '/inquiries' in path else 'report'
        if path.endswith('/resolve'):
            rid=path.split('/')[-2];r=p.item(rid,kind);r['resolved']=True;p.record(kind,r['user_id'],r['parent'],r,rid=rid);ops.audit(user,kind+'-resolve',rid);p.reply(h);return
        rows=p.records(kind);p.reply(h,items=rows,inquiries=rows,flags=rows);return
    if path=='/api/admin/backfill-webp':
        # Python validates/re-encodes new uploads directly; legacy originals are
        # retained rather than destructively rewritten by a maintenance button.
        raise p.RequestError('기존 원본 사진은 보존됩니다. 새 사진은 Python에서 검증·변환합니다.',409)
    # Keep the existing administrator settings and schedule APIs operational.
    (admin.get if h.command=='GET' else admin.post)(h,path)
