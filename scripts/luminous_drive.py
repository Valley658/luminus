"""Restricted Google Drive media proxy, backed by the existing SQL database."""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import accounts
import luminous_port as p


def metadata(fid):
    if not re.fullmatch(r'[A-Za-z0-9_-]{10,200}',fid):raise p.RequestError('잘못된 Drive 파일입니다.')
    cached=p.records('drive-cache',parent=fid,limit=1)
    if cached and cached[0].get('expires',0)>time.time():return cached[0]
    key=accounts.config().get('GOOGLE_DRIVE_API_KEY','')
    if not key:raise p.RequestError('Drive API가 설정되지 않았습니다.',503)
    query=urllib.parse.urlencode(dict(key=key,fields='id,name,mimeType,size,thumbnailLink,videoMediaMetadata'))
    try:
        with urllib.request.urlopen('https://www.googleapis.com/drive/v3/files/'+fid+'?'+query,timeout=15) as response:
            d=json.loads(response.read(256000))
    except Exception:raise p.RequestError('Drive 파일을 가져올 수 없습니다.',502) from None
    d['expires']=time.time()+600;p.record('drive-cache',parent=fid,data=d,rid='drive-'+fid)
    return d


def api(h,path,user,q):
    fid=path.rsplit('/',1)[-1];d=metadata(fid)
    if path.startswith('/api/drive_video_title/'):
        p.reply(h,title=d.get('name',''),name=d.get('name',''));return
    if path.startswith('/api/drive_thumb/'):
        url=d.get('thumbnailLink','');host=urllib.parse.urlsplit(url).hostname or ''
        if not host.endswith('.googleusercontent.com') and host!='lh3.googleusercontent.com':raise p.RequestError('썸네일이 없습니다.',404)
        try:
            with urllib.request.urlopen(url,timeout=15) as r:
                body=r.read(5*1024*1024+1);ctype=r.headers.get('Content-Type','')
            if len(body)>5*1024*1024 or not ctype.startswith('image/'):raise ValueError()
        except Exception:raise p.RequestError('썸네일을 가져오지 못했습니다.',502) from None
        h.send_response(200);h.send_header('Content-Type',ctype);h.send_header('Content-Length',str(len(body)));h.send_header('Cache-Control','public, max-age=600');h.send_header('X-Content-Type-Options','nosniff');h.end_headers();h.wfile.write(body);return
    if not str(d.get('mimeType','')).startswith(('video/','audio/')):raise p.RequestError('재생할 수 없는 파일입니다.',400)
    key=accounts.config().get('GOOGLE_DRIVE_API_KEY','');query=urllib.parse.urlencode(dict(alt='media',key=key))
    headers={};ranged=h.headers.get('Range','')
    if ranged:
        if not re.fullmatch(r'bytes=\d*-\d*',ranged):raise p.RequestError('잘못된 재생 범위입니다.',416)
        headers['Range']=ranged
    try:r=urllib.request.urlopen(urllib.request.Request('https://www.googleapis.com/drive/v3/files/'+fid+'?'+query,headers=headers),timeout=20)
    except Exception:raise p.RequestError('Drive 영상에 연결할 수 없습니다.',502) from None
    with r:
        h.send_response(r.status)
        for name in ('Content-Type','Content-Length','Content-Range','Accept-Ranges'):
            if r.headers.get(name):h.send_header(name,r.headers[name])
        h.send_header('X-Content-Type-Options','nosniff');h.send_header('Cache-Control','private, max-age=300');h.end_headers()
        try:
            for _ in range(131072):
                part=r.read(65536)
                if not part:break
                h.wfile.write(part)
        except (BrokenPipeError,ConnectionResetError):pass
