"""Local-only Meilisearch lifecycle and SQLite index synchronization."""
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
import database

ROOT = database.ROOT
process = None
stop = threading.Event()
ready = False
last_stamp = None


def key():
    file = ROOT / 'runtime/meili-master.key'
    if not file.exists():
        file.parent.mkdir(exist_ok=True)
        file.write_text(secrets.token_urlsafe(40),encoding='ascii')
    return file.read_text().strip()


def request(path, data=None, method=None):
    payload = json.dumps(data,ensure_ascii=False).encode() if data is not None else None
    req = urllib.request.Request('http://127.0.0.1:7700'+path, data=payload,method=method,
        headers={'Authorization':'Bearer '+key(),'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=8) as response:
        return json.load(response)


def task(result):
    for _ in range(100):
        if stop.wait(.15): raise RuntimeError('Search stopped')
        status=request('/tasks/'+str(result['taskUid']))
        if status['status']=='succeeded':return
        if status['status']=='failed':raise RuntimeError('Meilisearch indexing failed')
    raise RuntimeError('Meilisearch indexing timed out')


def worker():
    global process, ready, last_stamp
    while not stop.is_set():
        try:
            try:
                request('/health')
                online = True
            except Exception:
                online = False
            if not online and (process is None or process.poll() is not None):
                ready=False
                exe=ROOT/'bin/meilisearch.exe'
                if not exe.exists(): raise RuntimeError('Meilisearch executable missing')
                env=os.environ.copy();env['MEILI_MASTER_KEY']=key()
                # SYSTEM has no per-user PATH; Python's bundled VC runtime is
                # already available in the user environment where this exe works.
                env['PATH']=str(__import__('pathlib').Path(sys.executable).parent)+os.pathsep+env.get('PATH','')
                log=(ROOT/'logs/meilisearch.log').open('a',encoding='utf-8')
                try:
                    process=subprocess.Popen([str(exe),'--http-addr','127.0.0.1:7700','--db-path',str(ROOT/'DB/meilisearch'),
                        '--env','production','--no-analytics'],env=env,cwd=ROOT,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
                finally:log.close()
                last_stamp=None
                time.sleep(1)
                if process.poll() is not None:
                    raise RuntimeError('Meilisearch exited: '+str(process.returncode))
            request('/health')
            data=database.load();stamp=data['updatedAt']
            if stamp!=last_stamp:
                try:request('/indexes/pastellive_videos')
                except urllib.error.HTTPError as error:
                    if error.code!=404:raise
                    task(request('/indexes',{'uid':'pastellive_videos','primaryKey':'id'}))
                task(request('/indexes/pastellive_videos/settings',{
                    'searchableAttributes':['title','channelName'], 'filterableAttributes':['channelId','songChannelIds','isSong','isLive'],
                    'pagination':{'maxTotalHits':100000}},'PATCH'))
                profiles=database.profiles()
                names={p['youtube_channel_id']:p['name'] for p in profiles}
                import youtube_shorts, re
                combined=[dict(v,kind='video') for v in data['videos']]+[dict(v,kind='shorts') for v in data['items']]
                docs=[dict(v,channelName=names.get(v['channelId'],v['channelName']),isSong=youtube_shorts.is_song(v),isLive=bool(re.search(r'라이브|생방송|다시보기|\blive\b',v['title'],re.I))) for v in combined if v['channelId'] in names or v.get('songChannelIds')]
                task(request('/indexes/pastellive_videos/documents',method='DELETE'))
                task(request('/indexes/pastellive_videos/documents',docs,'POST'))
                last_stamp=stamp;ready=True
            stop.wait(10)
        except Exception as error:
            print('Search service:',str(error) if isinstance(error,RuntimeError) else type(error).__name__,flush=True)
            stop.wait(3)


def start():
    stop.clear()
    threading.Thread(target=worker,daemon=True).start()


def close():
    stop.set()
    if process is not None and process.poll() is None:
        process.terminate()
        try:process.wait(5)
        except subprocess.TimeoutExpired:process.kill()


def search(query,offset,channel_id='',tab='videos',include_songs=False):
    if not ready: raise RuntimeError('Search is starting')
    body={'q':query[:200],'offset':offset,'limit':60}
    filters=[]
    if channel_id:filters.append(('songChannelIds' if tab=='songs' else 'channelId')+' = '+json.dumps(channel_id))
    if tab=='songs':filters.append('isSong = true')
    elif tab=='live':filters.append('isLive = true')
    elif tab!='all' and not include_songs:filters.append('isSong = false')
    if filters:body['filter']=filters
    data=request('/indexes/pastellive_videos/search',body)
    return dict(items=data['hits'],nextOffset=offset+len(data['hits']),hasMore=offset+len(data['hits'])<data.get('estimatedTotalHits',0),
        refreshing=False,configured=True,error='',engine='meilisearch')
