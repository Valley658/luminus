"""Server-side YouTube metadata cache; video playback uses official embeds."""
import json
import database
import music_catalog
import hashlib
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = database.DB
TTL = 7200
lock = threading.Lock()
running = False
attempted = 0
last_error = ''


def api_key():
    for line in (ROOT / '.env').read_text(encoding='utf-8-sig').splitlines():
        if line.strip().startswith('YOUTUBE_API_KEY='):
            return line.split('=', 1)[1].strip().strip('"\'')
    return ''


def api(resource, key, **params):
    url = 'https://www.googleapis.com/youtube/v3/' + resource + '?' + urllib.parse.urlencode(dict(params, key=key))
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        # Never include request URLs (which contain the private API key) in logs.
        raise RuntimeError(f'YouTube API HTTP {error.code}; check API activation, key restrictions and quota.') from None
    except Exception:
        raise RuntimeError('YouTube API connection failed.') from None


def seconds(duration):
    match = re.fullmatch(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration)
    return sum(int(value or 0) * scale for value, scale in zip(match.groups(), (3600, 60, 1))) if match else 0


def is_short(video):
    snippet = video['snippet']
    text = snippet.get('title', '') + ' ' + snippet.get('description', '')
    marked = re.search(r'#(?:shorts?|쇼츠|쇼트)(?![\w])', text, re.I) or any(
        tag.casefold() in ('short', 'shorts', '쇼츠', '쇼트') for tag in snippet.get('tags', []))
    # Data API exposes no definitive public isShort flag. Use conservative labels.
    return bool(marked and 0 < seconds(video['contentDetails']['duration']) <= 180
                and video.get('status', {}).get('embeddable')
                and video.get('status', {}).get('privacyStatus') == 'public'
                and snippet.get('liveBroadcastContent', 'none') == 'none')


def collect(key):
    profiles = database.profiles()
    names = {p['youtube_channel_id']: p['name'] for p in profiles}
    channels = api('channels', key, part='contentDetails', id=','.join(names), maxResults=50)['items']
    if len(channels) != len(names):
        raise RuntimeError('Some configured YouTube channel IDs were not found.')

    with database.connect() as db:
        previous=db.execute("SELECT value FROM metadata WHERE name='shortsHistoryAt'").fetchone()
    deep=not previous or time.time()-previous[0]>86400*7
    pages=10 if deep else 2

    def playlist_ids(playlist, optional=False):
        ids=[];token=''
        for _ in range(pages):
            params=dict(part='contentDetails',playlistId=playlist,maxResults=50)
            if token:params['pageToken']=token
            try:result=api('playlistItems',key,**params)
            except RuntimeError:
                if optional:break
                raise
            ids.extend(item['contentDetails']['videoId'] for item in result.get('items',[]) if item.get('contentDetails',{}).get('videoId'))
            token=result.get('nextPageToken','')
            if not token:break
        return ids

    short_ids=set()
    def uploads(channel):
        ids=playlist_ids(channel['contentDetails']['relatedPlaylists']['uploads'])
        # YouTube's per-channel Shorts playlist also finds Shorts beyond recent uploads.
        dedicated=playlist_ids('UUSH'+channel['id'][2:],optional=True)
        with lock:short_ids.update(dedicated)
        ids.extend(dedicated)
        return ids

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(dict.fromkeys(video for group in pool.map(uploads, channels) for video in group))
    sources,members=music_catalog.discover(api,key,profiles)
    ids=list(dict.fromkeys(ids+[m[1] for m in members]))
    official_id=next((p['youtube_channel_id'] for p in profiles if p['position']==1),'')
    items = []
    regular = []
    for offset in range(0, len(ids), 50):
        videos = api('videos', key, part='snippet,contentDetails,status', id=','.join(ids[offset:offset + 50]))['items']
        for video in videos:
            snippet = video['snippet']
            thumbnails = snippet.get('thumbnails', {})
            thumbnail = thumbnails.get('maxres') or thumbnails.get('standard') or thumbnails.get('high') or thumbnails.get('medium')
            if not thumbnail:
                continue
            record = dict(id=video['id'], title=snippet['title'], channelId=snippet['channelId'],
                              channelName=names.get(snippet['channelId'], snippet['channelTitle']),
                              thumbnail=thumbnail['url'], publishedAt=snippet['publishedAt'])
            if snippet['channelId']!=official_id and (snippet.get('categoryId')=='10' or strong_music_title(snippet['title'])):
                members.append((snippet['channelId'],video['id'],'video-metadata','metadata'))
            dedicated_short=(video['id'] in short_ids and 0<seconds(video['contentDetails']['duration'])<=180
                and video.get('status',{}).get('embeddable') and video.get('status',{}).get('privacyStatus')=='public'
                and snippet.get('liveBroadcastContent','none')=='none')
            if is_short(video) or dedicated_short:
                items.append(record)
            elif (video.get('status', {}).get('embeddable') and video.get('status', {}).get('privacyStatus') == 'public'
                  and snippet.get('liveBroadcastContent', 'none') == 'none' and seconds(video['contentDetails']['duration']) > 0):
                regular.append(record)
    items.sort(key=lambda item: item['publishedAt'], reverse=True)
    # Mix channels round-robin so one active uploader cannot occupy the whole row.
    groups = {channel_id: [v for v in items if v['channelId'] == channel_id] for channel_id in names}
    mixed = []
    while any(groups.values()):
        for group in groups.values():
            if group:
                mixed.append(group.pop(0))
    return dict(updatedAt=time.time(), items=mixed, videos=regular,musicSources=sources,musicMembers=members,shortsHistoryAt=time.time() if deep else previous[0])


memory_cache = None


def cached():
    global memory_cache
    if memory_cache is None:
        memory_cache = database.load()
    return memory_cache


def refresh(key):
    global running, last_error, memory_cache
    try:
        data = collect(key)
        database.save(data)
        memory_cache = database.load()
        last_error = ''
    except Exception as error:
        last_error = 'fetch_failed'
        print('Shorts sync failed: ' + (str(error) if isinstance(error, RuntimeError) else type(error).__name__), flush=True)
    finally:
        with lock:
            running = False


def feed(channel=''):
    global running, attempted
    data = cached()
    key = api_key()
    with lock:
        if key and not running and (time.time() - data['updatedAt'] >= TTL or 'videos' not in data) and time.time() - attempted > 300:
            running = True
            attempted = time.time()
            threading.Thread(target=refresh, args=(key,), daemon=True).start()
    items = data['items']
    if channel:
        profiles = database.profiles()
        if any(str(p['position'])==channel for p in profiles):
            channel_id = next(p['youtube_channel_id'] for p in profiles if str(p['position'])==channel)
            items = [item for item in items if item['channelId'] == channel_id]
        else:
            items = []
    return dict(items=items, configured=bool(key), refreshing=running, error=last_error,
                updatedAt=data['updatedAt'])


page_cache = {}
page_lock = threading.Lock()


def shorts_page(channel='', offset=0, seed=''):
    """Shared server cache; send a small page rather than the entire catalog."""
    offset = max(0, min(int(offset), 100000))
    seed=str(seed)[:64] if not channel else ''
    key = (str(channel), offset, seed)
    now = time.time()
    stamp = cached()['updatedAt']
    with page_lock:
        hit = page_cache.get(key)
        if hit and hit[0] == stamp and now - hit[1] < 60:
            return hit[2]
    state = feed(str(channel))
    items = state['items']
    if seed:items=sorted(items,key=lambda item:hashlib.sha256((seed+item['id']).encode()).digest())
    result = dict(state, items=items[offset:offset+40], nextOffset=offset+len(items[offset:offset+40]),
                  hasMore=offset+40 < len(items))
    with page_lock:
        if len(page_cache) >= 256:
            page_cache.clear()
        page_cache[key] = (stamp, now, result)
    return result


def strong_music_title(title):
    return bool(re.search(r'(?:\bcover(?:ed)?\b|커버\s*곡|歌ってみた|오리지널\s*곡|original\s*song|music\s*video|뮤직비디오|\bMV\b|\bM/V\b)',title,re.I))


def is_song(video):
    return bool(video['isSong']) if 'isSong' in video else strong_music_title(video['title'])


def video_feed(seed, offset=0, channel='', query='', tab='videos', include_songs=False, sort='random'):
    if str(channel)=='2':tab='videos';include_songs=True
    if query.strip():
        import search_engine
        profiles = database.profiles()
        cid = next((p['youtube_channel_id'] for p in profiles if str(p['position'])==channel),'')
        try:
            return search_engine.search(query, max(0,min(offset,100000)), '', 'all', True)
        except Exception:
            return dict(items=[],nextOffset=offset,hasMore=False,refreshing=False,configured=True,error='search_unavailable')
    state = feed()
    data = cached()
    items = data.get('videos', [])
    if query.strip():
        terms = query.casefold().split()[:12]
        items = [v for v in items if all(term in (v['title']+' '+v['channelName']).casefold() for term in terms)]
    if channel:
        profiles = database.profiles()
        cid = next((p['youtube_channel_id'] for p in profiles if str(p['position'])==channel),'')
        items = [v for v in items if (cid in v.get('songChannelIds',[]) if tab=='songs' else v['channelId']==cid)]
    if tab=='songs':items=[v for v in items if is_song(v)]
    elif tab=='live':items=[v for v in items if re.search(r'라이브|생방송|다시보기|\blive\b',v['title'],re.I)]
    elif not include_songs:items=[v for v in items if not is_song(v)]
    ordered = sorted(items, key=lambda v: v.get('publishedAt',''), reverse=sort!='oldest') if sort in ('latest','oldest') else sorted(items, key=lambda v: hashlib.sha256((seed[:64] + v['id']).encode()).digest())
    offset = max(0, min(offset, 10000))
    page = ordered[offset:offset+18]
    return dict(items=page, nextOffset=offset+len(page), hasMore=offset+len(page)<len(ordered),
                refreshing=state['refreshing'], configured=state['configured'], error=state['error'])
