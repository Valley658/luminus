"""Bounded server image cache. Originals, including animated GIFs, stay intact."""
import hashlib
import io
import os
from pathlib import Path
import queue
import re
import threading
import time
import urllib.request
from PIL import Image
import database

ROOT=database.ROOT/'data/image-cache'
LIMIT=512*1024*1024
jobs=queue.Queue(10000)
lock=threading.RLock()
known={}
pending=set()
running=False
stopped=threading.Event()


def init():
    ROOT.mkdir(parents=True,exist_ok=True)
    with database.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS image_cache(id TEXT PRIMARY KEY,source TEXT NOT NULL,mime TEXT NOT NULL,size INTEGER NOT NULL,updated REAL NOT NULL)')
        rows=db.execute('SELECT * FROM image_cache').fetchall()
    with lock:
        known.clear()
        known.update({r['id']:dict(r) for r in rows if (ROOT/r['id']).is_file()})


def key(source):return hashlib.sha256(source.encode()).hexdigest()


def thumbnail(source):
    if not re.fullmatch(r'https://i\.ytimg\.com/vi(?:_webp)?/[A-Za-z0-9_-]{11}/[A-Za-z0-9_-]+\.(?:jpg|webp)',source or ''):return source
    rid=key(source)
    enqueue('remote',source)
    with lock:ready=rid in known
    return '/api/image-cache?id='+rid if ready else source


def gallery(rid):
    source='gallery:'+rid
    enqueue('gallery',rid)
    with lock:ready=key(source) in known
    return '/api/image-cache?id='+key(source) if ready else '/api/library/image?id='+rid


def enqueue(kind,value):
    source=value if kind=='remote' else 'gallery:'+value
    rid=key(source)
    with lock:
        entry=known.get(rid)
        if rid in pending or (entry and (kind=='gallery' or time.time()-entry['updated']<7*86400)):return
        try:jobs.put_nowait((kind,value));pending.add(rid)
        except queue.Full:pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def store(source,raw):
    if not raw or len(raw)>8*1024*1024:raise ValueError('Invalid image size')
    with Image.open(io.BytesIO(raw)) as im:
        if im.format not in ('JPEG','PNG','WEBP','GIF') or max(im.size)>8192 or im.width*im.height>40000000:raise ValueError('Invalid image')
        mime=Image.MIME[im.format]
        im.verify()
    if source.startswith('https://i.ytimg.com/') and mime!='image/gif':
        with Image.open(io.BytesIO(raw)) as im:
            im=im.convert('RGB');im.thumbnail((640,640))
            output=io.BytesIO();im.save(output,format='WEBP',quality=80,method=4)
            raw=output.getvalue();mime='image/webp'
    rid=key(source);temp=ROOT/(rid+'.'+str(threading.get_ident())+'.tmp')
    temp.write_bytes(raw);os.replace(temp,ROOT/rid)
    row=dict(id=rid,source=source,mime=mime,size=len(raw),updated=time.time())
    with database.connect() as db:db.execute('INSERT OR REPLACE INTO image_cache VALUES(?,?,?,?,?)',tuple(row.values()))
    with lock:known[rid]=row
    prune()
    return rid


def prune():
    with lock:
        total=sum(r['size'] for r in known.values())
        for rid,row in sorted(list(known.items()),key=lambda pair:pair[1]['updated']):
            if total<=LIMIT:break
            known.pop(rid,None);total-=row['size']
            (ROOT/rid).unlink(missing_ok=True)
            with database.connect() as db:db.execute('DELETE FROM image_cache WHERE id=?',(rid,))


def worker():
    while not stopped.is_set():
        try:kind,value=jobs.get(timeout=1)
        except queue.Empty:continue
        source=value if kind=='remote' else 'gallery:'+value
        try:
            if kind=='remote':
                # Queue entries originate from the database; validate again here.
                if not re.fullmatch(r'https://i\.ytimg\.com/vi(?:_webp)?/[A-Za-z0-9_-]{11}/[A-Za-z0-9_-]+\.(?:jpg|webp)',value):continue
                req=urllib.request.Request(value,headers={'User-Agent':'Pastellive-image-cache/1.0'})
                with urllib.request.build_opener(NoRedirect).open(req,timeout=8) as response:raw=response.read(8*1024*1024+1)
            else:
                with database.connect() as db:row=db.execute('SELECT image FROM library_gallery WHERE id=?',(value,)).fetchone()
                if not row:continue
                raw=bytes(row['image'])
            store(source,raw)
        except Exception:pass  # Serve original URLs while retries wait for the next scan.
        finally:
            with lock:pending.discard(key(source))
            jobs.task_done()


def scan():
    while not stopped.is_set():
        try:
            with database.connect() as db:
                videos=db.execute('SELECT thumbnail FROM videos ORDER BY publishedAt DESC').fetchall()
                images=db.execute('SELECT id FROM library_gallery ORDER BY created DESC').fetchall()
            for row in images:gallery(row['id'])
            for row in videos:thumbnail(row['thumbnail'])
        except Exception:pass
        stopped.wait(300)


def start():
    global running
    if running:return
    init();running=True;stopped.clear()
    for i in range(4):threading.Thread(target=worker,daemon=True,name='image-cache-'+str(i)).start()
    threading.Thread(target=scan,daemon=True,name='image-cache-prefetch').start()


def serve(h,rid):
    if not re.fullmatch('[a-f0-9]{64}',rid):h.send_error(404);return
    with lock:row=known.get(rid)
    if not row or not (ROOT/rid).is_file():h.send_error(404);return
    # Deleted gallery items must never remain accessible through their cache URL.
    if row['source'].startswith('gallery:'):
        with database.connect() as db:exists=db.execute('SELECT 1 FROM library_gallery WHERE id=?',(row['source'][8:],)).fetchone()
        if not exists:h.send_error(404);return
    raw=(ROOT/rid).read_bytes();etag='"'+hashlib.sha256(raw).hexdigest()+'"'
    match=h.headers.get('If-None-Match')==etag
    h.send_response(304 if match else 200);h.send_header('Content-Type',row['mime']);h.send_header('ETag',etag)
    h.send_header('Cache-Control','public, max-age=3600');h.send_header('X-Content-Type-Options','nosniff')
    if not match:h.send_header('Content-Length',str(len(raw)))
    h.end_headers()
    if not match:h.wfile.write(raw)
