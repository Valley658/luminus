"""Non-blocking, SQL-backed Chzzk status cache for the copied sidebar."""
import concurrent.futures
import json
import threading
import time
import urllib.request
import luminous_port as p

CHANNELS = {'유니': '45e71a76e949e16a34764deb962f9d9f', '후야': '36ddb9bb4f17593b60f1b63cec86611d', '히나': 'b044e3a3b9259246bc92e863e7d3f3b8', '리제': '4325b1d5bbc321fad3042306646e2e50', '마시로': '4515b179f86b67b4981e16190817c580', '타비': 'a6c4ddb09cdb160478996007bff35296', '린': '516937b5f85cbf2249ce31b0ad046b0f', '시부키': '64d76089fba26b180d9c9e48a32600d9', '나나': '4d812b586ff63f8a2946e64fa860bbf5', '리코': '8fd39bb8de623317de90654718638b10', '강지': 'b5ed5db484d04faf4d150aedd362f34b', '김블루': '0de8f1807076169b7eb9218137e99c51'}
_lock=threading.Lock()
_running=False
_next=0

def _one(entry):
    name,cid=entry
    req=urllib.request.Request('https://api.chzzk.naver.com/polling/v2/channels/'+cid+'/live-status',headers={'User-Agent':'Mozilla/5.0','Accept':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=6) as response:body=json.loads(response.read(262144))
        content=body.get('content')
        if isinstance(content,dict):p.record('live-status',parent=name,data={'name':name,'is_live':content.get('status')=='OPEN','checked':time.time()},rid='live-'+cid)
    except Exception:
        pass  # Retain the last successful status until its freshness limit.

def _refresh():
    global _running,_next
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(_one,CHANNELS.items()))
    finally:
        with _lock:_running=False;_next=time.monotonic()+60

def status():
    global _running
    rows=p.records('live-status',limit=100)
    result={r['name']:bool(r.get('is_live')) for r in rows if time.time()-r.get('checked',0)<300}
    with _lock:
        if not _running and time.monotonic()>=_next:
            _running=True;threading.Thread(target=_refresh,daemon=True,name='chzzk-status-cache').start()
    return result
