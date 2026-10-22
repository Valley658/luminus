"""Server-rendered watch pages and weekly schedules using the existing DB cache."""
import datetime
import html
import re
import secrets
from urllib.parse import parse_qs, quote
import database
import youtube_shorts
import channel_details
import comments
import library

ZONE=datetime.timezone(datetime.timedelta(hours=9))
esc=lambda value:html.escape(str(value),quote=True)


def init():
    channel_details.init()
    with database.connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS schedule_events(id TEXT PRIMARY KEY,title TEXT NOT NULL,channel_id TEXT NOT NULL DEFAULT '',starts_at REAL NOT NULL,ends_at REAL NOT NULL DEFAULT 0,link TEXT NOT NULL DEFAULT '',note TEXT NOT NULL DEFAULT '')")


def watch(video_id, origin=''):
    if not re.fullmatch(r'[\w-]{11}',video_id):return None
    with database.connect() as db:
        row=db.execute('SELECT * FROM videos WHERE id=?',(video_id,)).fetchone()
    video=dict(row) if row else dict(id=video_id,title='동영상',channelName='YouTube')
    state=youtube_shorts.shorts_page();shorts=[];consumed=0
    for item in state['items']:
        consumed+=1
        if item['id']!=video_id:shorts.append(item)
        if len(shorts)>=8:break
    more=consumed<len(state['items']) or state.get('hasMore',False)
    origin_arg='&amp;origin='+quote(origin,safe='') if origin else ''
    videos=[item for item in youtube_shorts.video_feed(secrets.token_hex(12),include_songs=True)['items'] if item['id']!=video_id][:12]
    channel=channel_details.get(video.get('channelId',''))
    name=channel['name'] or video['channelName']
    count=channel['subscribers']
    subscribers=('구독자 '+(str(round(count/10000,1)).removesuffix('.0')+'만' if count>=10000 else f'{count:,}')+'명') if count is not None else ('구독자 수 비공개' if channel['updated'] else '구독자 수 확인 중')
    photo='<img src="'+esc(channel['photo'])+'" alt="" width="52" height="52">' if channel['photo'] else '<span class="watch-channel-placeholder">'+esc(name[:1])+'</span>'
    identity='<div class="watch-channel-identity">'+photo+'<div><strong>'+esc(name)+'</strong><p>'+subscribers+'</p></div></div>'
    def card(item,short=False):
        return '<article class="watch-card'+(' watch-short' if short else ' video-card')+'"><a href="/watch?v='+esc(item['id'])+'"><img src="'+esc(item['thumbnail'])+'" alt="" loading="lazy" decoding="async"><div><h3>'+esc(item['title'])+'</h3><p>'+esc(item['channelName'])+'</p></div></a></article>'
    content='<div class="watch-layout"><section class="watch-primary" aria-label="영상 시청"><div class="watch-player"><iframe id="watch-player" src="https://www.youtube-nocookie.com/embed/'+esc(video_id)+'?autoplay=1&amp;loop=0&amp;mute=0&amp;cc_load_policy=0&amp;enablejsapi=1&amp;playsinline=1&amp;rel=0'+origin_arg+'" title="'+esc(video['title'])+'" allow="autoplay; encrypted-media; picture-in-picture; fullscreen" referrerpolicy="strict-origin-when-cross-origin" allowfullscreen></iframe></div><button type="button" class="watch-sound-start" hidden>소리 켜고 재생</button><h1>'+esc(video['title'])+'</h1><p class="watch-channel">'+esc(video['channelName'])+'</p></section><aside class="watch-related" aria-label="추천 영상"><div class="watch-shorts-heading"><h2>추천 Shorts</h2></div><div class="watch-shorts-carousel"><div class="watch-shorts-controls"><button type="button" data-watch-scroll="-1" aria-label="이전 추천 Shorts" disabled>‹</button><button type="button" data-watch-scroll="1" aria-label="다음 추천 Shorts">›</button></div><div class="watch-shorts" data-offset="'+str(consumed)+'" data-more="'+str(more).lower()+'" data-exclude="'+esc(video_id)+'">'+''.join(card(item,True) for item in shorts)+'</div></div><h2>추천 동영상</h2><div class="watch-videos">'+''.join(card(item) for item in videos)+'</div></aside></div>'
    content=content.replace('<p class="watch-channel">'+esc(video['channelName'])+'</p>',identity+library.watch_tools(video_id)+comments.render(video_id))
    is_short=video.get('kind')=='shorts'
    if is_short:content=content.replace('class="watch-layout"','class="watch-layout watch-layout-short"',1)
    candidates=[];seen={video_id}
    for item in (state['items']+videos if is_short else videos+state['items']):
        if item['id'] in seen or not re.fullmatch(r'[A-Za-z0-9_-]{11}',item['id']):continue
        seen.add(item['id']);candidates.append(item)
        if len(candidates)>=9:break
    tiles=''.join('<a class="watch-end-card" href="/watch?v='+esc(item['id'])+'"><img src="'+esc(item['thumbnail'])+'" alt="" loading="lazy"><span>'+esc(item['title'])+'</span></a>' for item in candidates)
    end_panel='<section class="watch-end" hidden aria-label="다음 영상 선택" data-next="'+esc(candidates[0]['id'] if candidates else '')+'"><div class="watch-end-heading"><strong>다음 영상 선택</strong><button type="button" class="watch-replay">다시 보기</button></div><div class="watch-end-grid">'+tiles+'</div><div class="watch-end-footer"><span class="watch-next-countdown" role="status"></span><button type="button" class="watch-auto-toggle" aria-pressed="true">자동 넘김 켜짐</button></div></section>'
    content=re.sub(r'(<article class="watch-card video-card"><a[^>]+>)(<img[^>]+>)',r'\1<span class="watch-thumb">\2</span>',content)
    content=content.replace('<div class="watch-player"><iframe','<div class="watch-stage'+(' is-short' if is_short else '')+'"><div class="watch-player"><iframe',1)
    content=content.replace('</iframe></div><button','</iframe></div>'+end_panel+'</div><button',1)
    return video['title'],content


def schedule(date_value=''):
    try:selected=datetime.date.fromisoformat(date_value)
    except ValueError:selected=datetime.datetime.now(ZONE).date()
    if not 2000<=selected.year<=2099:selected=datetime.datetime.now(ZONE).date()
    monday=selected-datetime.timedelta(days=selected.weekday())
    start=datetime.datetime.combine(monday,datetime.time(),ZONE)
    end=start+datetime.timedelta(days=7)
    with database.connect() as db:
        rows=[dict(row) for row in db.execute('SELECT * FROM schedule_events WHERE starts_at>=? AND starts_at<? ORDER BY starts_at',(start.timestamp(),end.timestamp()))]
    names={profile['youtube_channel_id']:profile['name'] for profile in database.profiles()}
    previous=(monday-datetime.timedelta(days=7)).isoformat();following=(monday+datetime.timedelta(days=7)).isoformat()
    content='<section class="schedule-page"><div class="schedule-heading"><h1>스케줄</h1><nav aria-label="주간 일정 이동"><a href="/schedule?date='+previous+'">이전 주</a><a href="/schedule">이번 주</a><a href="/schedule?date='+following+'">다음 주</a></nav></div><p>한국 시간 (KST) · '+monday.isoformat()+' ~ '+(monday+datetime.timedelta(days=6)).isoformat()+'</p><div class="schedule-week">'
    today=datetime.datetime.now(ZONE).date()
    for number in range(7):
        day=monday+datetime.timedelta(days=number)
        content+='<section class="schedule-day'+(' is-today' if day==today else '')+'"><h2>'+day.strftime('%m.%d')+' '+['월','화','수','목','금','토','일'][number]+'</h2>'
        events=[row for row in rows if datetime.datetime.fromtimestamp(row['starts_at'],ZONE).date()==day]
        if not events:content+='<p class="schedule-empty">등록된 일정이 없습니다.</p>'
        for event in events:
            label=datetime.datetime.fromtimestamp(event['starts_at'],ZONE).strftime('%H:%M')
            content+='<article class="schedule-event"><time>'+label+'</time><h3>'+esc(event['title'])+'</h3><p>'+esc(names.get(event['channel_id'],'루미너스'))+'</p>'
            if event['note']:content+='<p class="schedule-note">'+esc(event['note'])+'</p>'
            if event['link']:
                content+='<a href="'+esc(event['link'])+'"'+(' target="_blank" rel="noopener noreferrer"' if not event['link'].startswith('/') else '')+'>시청하기</a>'
            content+='</article>'
        content+='</section>'
    return '스케줄',content+'</div></section>'


init()
