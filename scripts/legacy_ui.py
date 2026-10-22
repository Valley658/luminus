"""Render preserved legacy templates with current DB authentication and content APIs."""
import copy
import hashlib
import html
import json
import re
import secrets
from pathlib import Path
from urllib.parse import urlencode
from bs4 import BeautifulSoup
from jinja2 import Environment,FileSystemLoader,ChainableUndefined,select_autoescape
import accounts
import database
import youtube_shorts
import library
import pages
import comments
import channel_details
ROOT=database.ROOT
ENV=Environment(loader=FileSystemLoader(str(ROOT/'templates')),undefined=ChainableUndefined,autoescape=select_autoescape(['html']))

def version(name):
    path=ROOT/'static/luminus/assets'/name
    return '/assets/'+name+'?v='+hashlib.sha256(path.read_bytes()).hexdigest()[:16] if path.exists() else ''

def member_context():
    source=json.loads((ROOT/'rust-server/resources/members.json').read_text(encoding='utf-8'))
    profiles=database.profiles()
    mapping={}
    source=json.loads(json.dumps(source))
    for member in source.get('sidebar_members',[]):
        full=source.get('MEMBER_FULL_NAMES',{}).get(member['name'],member['name'])
        profile=next((p for p in profiles if p['name']==full or member['name'] in p['name']),None)
        if profile:member.update(img='/profiles/'+profile['photo'],position=profile['position']);mapping[member['name']]=member
    for group in source.get('sidebar_generations',[]):
        for member in group['members']:
            member.update(mapping.get(member['name'],{}))
        if group['members']:group['img']=group['members'][0]['img']
    for key in ('sidebar_ungrouped_top','sidebar_ungrouped_bottom'):
        for member in source.get(key,[]):member.update(mapping.get(member['name'],{}))
    return source

def render_template(name,context):
    template_name='index.html' if name=='index.html' else 'luminus/legacy/'+name
    return ENV.get_template(template_name).render(**context,versioned_static=lambda name:'/static/'+name,url_for=lambda endpoint,filename='',**kwargs:'/static/'+filename)

def serve(handler,path):
    user=accounts.current(handler);params=__import__('urllib.parse',fromlist=['parse_qs']).parse_qs(__import__('urllib.parse',fromlist=['urlsplit']).urlsplit(handler.path).query)
    view=path.strip('/') or 'home'
    if view in ('index.html','index2.html'):view='home'
    if view=='profile':view='mypage'
    if path.startswith('/watch/'):view='watch'
    channel=params.get('channel',[''])[0];query=params.get('q',[''])[0];tab=params.get('tab',['videos'])[0]
    state=youtube_shorts.shorts_page(channel,seed=secrets.token_hex(12) if not channel else '')
    video_state=youtube_shorts.video_feed(secrets.token_hex(12),channel=channel,query=query,tab=tab)
    convert=lambda item:dict(item,videoId=item['id'],is_short=item.get('kind')=='shorts')
    ctx=member_context();ctx.update(request={'path':path},init_videos=[convert(v) for v in video_state['items']],init_home_shorts=[convert(v) for v in state['items'][:15]],today_schedules=[])
    soup=BeautifulSoup(render_template('index.html',ctx),'html.parser')
    header=soup.select_one('#ytFixedTop');sidebar=soup.select_one('#sidebarList');main=soup.select_one('#mainContent')
    if not all((header,sidebar,main)):raise RuntimeError('Legacy template structure missing')
    # Keep the original layout, but do not execute scripts for missing historical services.
    for script in soup.find_all('script'):script.decompose()
    styles=''.join(str(style) for style in soup.find_all('style'))
    for element in soup.find_all(True):
        for attr in list(element.attrs):
            if attr.lower().startswith('on'):del element.attrs[attr]
    for item in sidebar.select('[data-member-name]'):
        member=next((m for m in ctx.get('sidebar_members',[]) if m['name']==item.get('data-member-name')),None)
        if member and member.get('position'):item['data-channel']=str(member['position'])
    for image in soup.select('img[src^="/static/"]'):image['src']='/assets/brand-logo.png'
    for element in sidebar.select('[style]'):
        style=element.get('style','')
        if '/static/images/members/' in style:
            match=re.search(r"/static/images/members/([^.'\"]+)",style)
            member=next((m for m in ctx.get('sidebar_members',[]) if match and match[1] in m.get('img','')),None)
            if not member:element['style']='background:linear-gradient(135deg,#51bbf8,#9a7dff)'
    # Remove redundant home overlays/content from subpages while retaining the old shell.
    extra='';title='루미너스'
    if view in library.VIEWS:
        title,content=library.page(view,user,params)
        fragment=BeautifulSoup(content,'html.parser')
        for item in fragment.select('.library-grid'):item['class']=['library-grid','item-grid']
        for item in fragment.select('.library-card'):item['class']=['library-card','item-card']
        if view=='mypage':
            olduser=dict(nickname=user['name'],picture=user['avatar'],discord_username=user.get('discord_username','')) if user else dict(nickname='로그인이 필요합니다.',picture='')
            profile=BeautifulSoup(render_template('profile.html',dict(ctx,user=olduser,attendance_infinite=False)),'html.parser')
            top=profile.select_one('.profile-top-section')
            if top:
                stats=top.select_one('.profile-stats')
                if stats:stats.decompose()
                nav=top.select_one('.profile-nav')
                if nav:nav.decompose()
                fragment.select_one('.mypage-hero').replace_with(top)
            fragment.select_one('h1').string='마이페이지'
        main.clear();main.append(fragment);extra+='<script src="'+version('library.js')+'" defer></script>'
    elif view=='schedule':
        title,content=pages.schedule(params.get('date',[''])[0]);main.clear();main.append(BeautifulSoup(content,'html.parser'))
    elif view=='watch' or path.startswith('/watch/'):
        vid=params.get('v',[''])[0] or (path.rsplit('/',1)[-1] if path.startswith('/watch/') else '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}',vid):return handler.send_error(404)
        with database.connect() as db:row=db.execute('SELECT * FROM videos WHERE id=?',(vid,)).fetchone()
        video=dict(row) if row else dict(title='동영상',channelName='YouTube',channelId='')
        watch=BeautifulSoup(render_template('watch.html',dict(ctx,video_id=vid,page_title=video['title'],video_description='')),'html.parser')
        container=watch.select_one('.watch-container');main.clear();main.append(container)
        for element in container.find_all(True):
            for attr in list(element.attrs):
                if attr.startswith('on'):del element.attrs[attr]
        player=container.select_one('#yt-player');player.replace_with(BeautifulSoup('<iframe id="legacy-player" src="https://www.youtube-nocookie.com/embed/'+vid+'?enablejsapi=1&amp;autoplay=1&amp;mute=0&amp;cc_load_policy=0&amp;rel=0&amp;playsinline=1" allow="autoplay; encrypted-media; fullscreen" allowfullscreen referrerpolicy="strict-origin-when-cross-origin"></iframe>','html.parser'))
        container.select_one('#watchVideoTitle').string=video['title'];title=video['title']
        for selector in ('#videoReactionGroup','#highlightTagOverlay','.video-title-area > div'):
            element=container.select_one(selector)
            if element:element.decompose()
        section=container.select_one('.main-player-section')
        details=channel_details.get(video.get('channelId',''));count=details.get('subscribers')
        identity='<p class="legacy-channel">'+('<img width="44" height="44" style="border-radius:50%;vertical-align:middle;margin-right:12px" src="'+html.escape(details['photo'],quote=True)+'">' if details.get('photo') else '')+html.escape(details.get('name') or video['channelName'])+(' · 구독자 '+format(count,',')+'명' if count is not None else '')+'</p>'
        section.insert(2,BeautifulSoup(identity+library.watch_tools(vid),'html.parser'))
        oldcomments=container.select_one('.comment-section')
        if oldcomments:oldcomments.replace_with(BeautifulSoup(comments.render(vid),'html.parser'))
        extra+='<script src="'+version('comments.js')+'" defer></script><script src="'+version('library.js')+'" defer></script>'
        # Reuse the old recommendation container and the current server video cache.
        related=container.select_one('.recommend-sidebar')
        if related:related.clear();related.append(BeautifulSoup(''.join(library.server_card(v,'history',False) for v in video_state['items'][:12]),'html.parser'))
    else:
        # Only original home/channel sections are kept; missing legacy scripts must not leave overlays visible.
        allowed={'homeShortsSection','videoGrid','tabContainer'}
        for child in list(main.children):
            if getattr(child,'name',None) and child.get('id') not in allowed:child.extract()
        for card in main.select('.shorts-card'):
            image=card.select_one('img')
            if image:
                item=next((v for v in state['items'] if v['thumbnail'].split('/')[-2] in image.get('src','')),None)
                if item:card['data-video-id']=item['id']
        if channel:
            nav=main.select_one('#tabContainer')
            if nav:nav['style']='display:block'
            shorts=main.select_one('#homeShortsSection')
            if shorts and tab!='shorts':shorts.decompose()
    for item in sidebar.select('a[href*="discord.gg"]'):item['href']='https://discord.gg/9Ccf94UJrF'
    sidebar_links={'spaHistoryBtn':'/history','spaBookmarksBtn':'/bookmarks','spaPlaylistsBtn':'/playlists','spaGalleryBtn':'/gallery','spaScheduleBtn':'/schedule','spaHighlightsBtn':'/highlights'}
    for key,href in sidebar_links.items():
        element=sidebar.select_one('#'+key)
        if element:element.name='a';element['href']=href
    markup='<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>'+html.escape(title)+' · 루미너스</title><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css"><link rel="stylesheet" href="'+version('legacy.css')+'">'+styles+'</head><body data-legacy="'+html.escape(view)+'">'+str(header)+str(sidebar)+str(main)+'<script src="/media/sweetalert2.all.min.js"></script><script src="'+version('legacy.js')+'" defer></script>'+extra+'</body></html>'
    raw=markup.encode();handler.send_response(200);handler.send_header('Content-Type','text/html; charset=utf-8');handler.send_header('Content-Length',str(len(raw)));handler.send_header('Cache-Control','private, no-store');handler.send_header('X-Content-Type-Options','nosniff');handler.end_headers();handler.wfile.write(raw)
