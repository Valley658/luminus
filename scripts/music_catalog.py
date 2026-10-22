"""Discover official release albums and music playlists; persist their provenance in SQLite."""
import json
import re
import urllib.request


def music_playlist(title):
    return bool(re.search(r'커버|cover|original|오리지널|발매|발표곡|음반|앨범|뮤직|music|노래|歌|singing|karaoke',title,re.I))


def releases(channel_id):
    url='https://www.youtube.com/channel/'+channel_id+'/releases'
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Accept-Language':'ko-KR,ko;q=0.9'})
    with urllib.request.urlopen(req,timeout=20) as response:page=response.read().decode('utf-8')
    match=re.search(r'var ytInitialData = (.*?);</script>',page)
    if not match:raise RuntimeError('Release page unavailable')
    data=json.loads(match[1]);ids=set()
    def walk(node):
        if isinstance(node,dict):
            pid=node.get('playlistId','')
            if pid.startswith('OLAK5uy_'):ids.add(pid)
            for value in node.values():walk(value)
        elif isinstance(node,list):
            for value in node:walk(value)
    walk(data)
    return sorted(ids)


def discover(api,key,profiles):
    sources=[];members=[]
    for profile in profiles:
        cid=profile['youtube_channel_id'];official=profile['position']==1
        candidates=[]
        try:
            if official:
                candidates=[(pid,'공식 발표곡','release') for pid in releases(cid)]
            else:
                token=''
                for _ in range(4):
                    kwargs=dict(part='snippet',channelId=cid,maxResults=50)
                    if token:kwargs['pageToken']=token
                    page=api('playlists',key,**kwargs)
                    candidates.extend((p['id'],p['snippet']['title'],'playlist') for p in page.get('items',[]) if music_playlist(p['snippet']['title']))
                    token=page.get('nextPageToken','')
                    if not token:break
            for pid,title,kind in candidates:
                token='';found=[]
                for _ in range(4):
                    kwargs=dict(part='contentDetails',playlistId=pid,maxResults=50)
                    if token:kwargs['pageToken']=token
                    page=api('playlistItems',key,**kwargs)
                    found.extend(p['contentDetails']['videoId'] for p in page.get('items',[]) if p.get('contentDetails',{}).get('videoId'))
                    token=page.get('nextPageToken','')
                    if not token:break
                sources.append((cid,pid,title,kind))
                members.extend((cid,vid,pid,kind) for vid in found)
        except Exception:
            # Keep already stored music catalog if an individual source is unavailable.
            print('Music source refresh unavailable for channel position',profile['position'],flush=True)
    return sources,members
