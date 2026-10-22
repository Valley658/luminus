"""Shared, persistent YouTube channel statistics; never fetch per viewer."""
import threading
import time
from pathlib import Path
import database
import youtube_shorts

TTL=21600
stop=threading.Event()


def init():
    with database.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS channel_details(channel_id TEXT PRIMARY KEY,name TEXT NOT NULL,photo TEXT NOT NULL,subscribers BIGINT,updated REAL NOT NULL)')


def refresh():
    key=youtube_shorts.api_key()
    if not key:return
    profiles=database.profiles()
    ids=[profile['youtube_channel_id'] for profile in profiles]
    for offset in range(0,len(ids),50):
        data=youtube_shorts.api('channels',key,part='snippet,statistics',id=','.join(ids[offset:offset+50]))
        with database.connect() as db:
            for item in data.get('items',[]):
                info=item['snippet'];stats=item.get('statistics',{})
                count=None if stats.get('hiddenSubscriberCount') or 'subscriberCount' not in stats else int(stats['subscriberCount'])
                thumbnails=info.get('thumbnails',{})
                photo=(thumbnails.get('medium') or thumbnails.get('default') or {}).get('url','')
                db.execute('INSERT OR REPLACE INTO channel_details VALUES(?,?,?,?,?)',(item['id'],info['title'],photo,count,time.time()))


def get(channel_id):
    with database.connect() as db:
        row=db.execute('SELECT * FROM channel_details WHERE channel_id=?',(channel_id,)).fetchone()
    result=dict(row) if row else dict(channel_id=channel_id,name='',photo='',subscribers=None,updated=0)
    for profile in database.profiles():
        if profile['youtube_channel_id']==channel_id:
            photo=str(profile['photo'])
            if Path(photo).name==photo:result['photo']='/profiles/'+photo
            result['name']=profile['name'];result['position']=profile['position']
            break
    return result


def start():
    stop.clear()
    def worker():
        while not stop.is_set():
            try:
                with database.connect() as db:row=db.execute('SELECT MIN(updated) FROM channel_details').fetchone()
                if not row or row[0] is None or time.time()-row[0]>=TTL:refresh()
            except Exception:print('Channel statistics refresh failed; keeping cached values.',flush=True)
            stop.wait(600)
    threading.Thread(target=worker,daemon=True).start()


def close():stop.set()


init()
