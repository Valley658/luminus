import msvcrt
import subprocess
import sys
import time
import html
import hashlib
import json
import mimetypes
import re
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
import youtube_shorts
import database
import search_engine
import accounts
import admin
import admin_operations
import pages
import channel_details
import comments
import library
import luminous_port
from channel_catalog import GROUPS

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "runtime"
RUNTIME.mkdir(exist_ok=True)
STOP = RUNTIME / '.stop-request'


class Handler(BaseHTTPRequestHandler):
    _asset_versions={}
    def asset_version(self,name):
        path=ROOT/'static/luminus/assets'/name
        if not path.is_file():return 'missing'
        stamp=path.stat().st_mtime_ns
        hit=self._asset_versions.get(name)
        if not hit or hit[0]!=stamp:
            hit=(stamp,hashlib.sha256(path.read_bytes()).hexdigest()[:16]);self._asset_versions[name]=hit
        return hit[1]

    def serve_asset(self,path):
        file=ROOT/'static/luminus/assets'/Path(path).name
        if not file.is_file():return self.send_error(404)
        version=self.asset_version(file.name);etag='"'+version+'"'
        requested=parse_qs(urlsplit(self.path).query).get('v',[''])[0]
        cache='public, max-age=31536000, immutable' if requested==version else 'public, max-age=0, must-revalidate'
        unchanged=self.headers.get('If-None-Match')==etag
        self.send_response(304 if unchanged else 200)
        self.send_header('ETag',etag);self.send_header('Cache-Control',cache)
        self.send_header('X-Content-Type-Options','nosniff')
        if not unchanged:
            data=file.read_bytes();self.send_header('Content-Type',mimetypes.guess_type(file.name)[0] or 'application/octet-stream');self.send_header('Content-Length',str(len(data)))
        self.end_headers()
        if not unchanged:self.wfile.write(data)

    def log_request(self, code='-', size='-'):
        # OAuth callback URLs contain one-use authorization codes.
        path=urlsplit(self.path).path if self.path.startswith(('/auth/','/login/discord','/account/verify-email','/reset-password')) else self.path
        self.log_message('"%s %s %s" %s %s',self.command,path,self.request_version,str(code),str(size))
    def send_error(self, code, message=None, explain=None):
        if code != 404:
            return super().send_error(code, message, explain)
        data = (ROOT / 'templates/luminus/not-found.html').read_bytes()
        self.send_response(404)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def local_request(self):
        return (self.client_address[0]=='127.0.0.1' and self.headers.get('Host','').lower() in ('127.0.0.1','127.0.0.1:80','localhost','localhost:80','127.0.0.1:8081','localhost:8081')
            and not any(k.lower().startswith(('cf-','x-forwarded-')) or k.lower()=='forwarded' for k in self.headers))

    def public_auth_request(self):
        return self.client_address[0]=='127.0.0.1' and self.headers.get('Host','').lower()=='pastellive.co.kr'

    def admin_request(self):
        return self.client_address[0]=='127.0.0.1' and self.headers.get('Host','').lower()=='admin.pastellive.co.kr'

    def maintenance_response(self,path):
        if path not in ('/','/index.html','/index2.html','/watch','/schedule','/api/videos','/api/shorts') and not path.startswith('/watch/'):return False
        maintenance=admin_operations.setting('maintenance',dict(enabled=False,message='사이트 점검 중입니다.'))
        if not maintenance['enabled'] or (accounts.current(self) or {}).get('is_admin'):return False
        if path.startswith('/api/'):
            accounts.reply(self,{'error':'maintenance','items':[],'hasMore':False,'nextOffset':0},503)
        else:
            data=(ROOT/'templates/luminus/maintenance.html').read_text(encoding='utf-8').replace('<!-- MESSAGE -->',html.escape(maintenance['message'])).encode('utf-8')
            self.send_response(503);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('Retry-After','300');self.end_headers();self.wfile.write(data)
        return True

    def do_POST(self):
        if luminous_port.handle(self):return
        if self.admin_request():return admin.post(self,urlsplit(self.path).path)
        if not admin_operations.observe(self,urlsplit(self.path).path):return
        if urlsplit(self.path).path=='/api/library':
            if not (self.local_request() or self.public_auth_request()):return self.send_error(404)
            return library.post(self)
        if urlsplit(self.path).path in ('/api/comments','/api/comments/delete'):
            if not (self.local_request() or self.public_auth_request()):return self.send_error(404)
            return comments.post(self,urlsplit(self.path).path)
        if urlsplit(self.path).path=='/api/site/revive':
            if not (self.local_request() or self.public_auth_request()):return self.send_error(404)
            return accounts.revive(self)
        if not (self.local_request() or (self.public_auth_request() and urlsplit(self.path).path.startswith('/api/auth/'))): return self.send_error(404)
        return accounts.post(self,urlsplit(self.path).path)

    def do_GET(self):
        if luminous_port.handle(self):return
        path = urlsplit(self.path).path
        if self.admin_request():return admin.get(self,path)
        if not admin_operations.observe(self,path):return
        if self.maintenance_response(path):return
        if path in ('/api/library','/api/library/image'):
            if not (self.local_request() or self.public_auth_request()):return self.send_error(404)
            params=parse_qs(urlsplit(self.path).query)
            return library.image(self,params) if path.endswith('/image') else library.get(self,params)
        if path.lstrip('/') in library.VIEWS:
            if not accounts.site_enabled(self):return self.send_error(404)
            return self.serve_draft('/index2.html',view=path.lstrip('/'))
        if path in ('/api/comments','/api/comments/avatar'):
            if not (self.local_request() or self.public_auth_request()):return self.send_error(404)
            params=parse_qs(urlsplit(self.path).query)
            return comments.avatar(self,params) if path.endswith('/avatar') else comments.get(self,params)
        if path in ('/watch','/schedule') or re.fullmatch(r'/watch/[\w-]{11}',path):
            if not accounts.site_enabled(self):return self.send_error(404)
            params=parse_qs(urlsplit(self.path).query)
            video_id=path.removeprefix('/watch/') if path.startswith('/watch/') else params.get('v',[''])[0]
            return self.serve_draft('/index2.html',view='schedule' if path=='/schedule' else 'watch',video_id=video_id)
        if path in ('/assets/pages.css','/assets/watch.js','/assets/comments.js','/assets/library.js','/assets/legacy.js','/assets/legacy.css','/assets/navigation.js'):
            if not accounts.site_enabled(self):return self.send_error(404)
            return self.serve_asset(path)
        if path in ('/api/auth/me','/api/auth/avatar') or path.startswith('/auth/discord'):
            if not (self.local_request() or self.public_auth_request()): return self.send_error(404)
            if path=='/api/auth/avatar':return accounts.avatar_get(self)
            if path=='/api/auth/me':
                user=accounts.current(self)
                return accounts.reply(self,dict(user=accounts.public(user),discordConfigured=accounts.discord_ready()),cookies=accounts.admin_session_cookie(self,user))
            if path=='/auth/discord/complete':
                data=(ROOT/'templates/luminus/auth-complete.html').read_bytes()
                self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data);return
            if path=='/auth/discord/start':return accounts.oauth_start(self)
            if path=='/auth/discord/callback':return accounts.oauth_callback(self)
            return self.send_error(404)
        if path in ('/media/stars-horizontal.gif', '/media/stars-vertical.gif', '/media/stars-horizontal.png', '/media/stars-vertical.png','/media/sweetalert2.all.min.js'):
            data = (ROOT / 'public' / path.lstrip('/')).read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/javascript; charset=utf-8' if path.endswith('.js') else 'image/gif' if path.endswith('.gif') else 'image/png')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'public, max-age=300')
            self.end_headers()
            self.wfile.write(data)
            return
        is_draft = path in ('/index2.html', '/api/shorts', '/api/videos', '/assets/brand-logo.png', '/assets/accounts.js', '/assets/accounts.css', '/assets/layout.js', '/assets/layout.css', '/assets/videos.js', '/assets/videos.css', '/assets/previews.js', '/assets/shorts.js', '/assets/shorts.css') or path.startswith('/profiles/')
        if is_draft:
            if not accounts.site_enabled(self):
                self.send_error(404)
                return
            self.serve_draft(path)
            return
        if path == '/favicon.ico':
            data=(ROOT/'public/favicon.ico').read_bytes()
            self.send_response(200)
            self.send_header('Content-Type','image/x-icon')
            self.send_header('Cache-Control','public, max-age=86400')
            self.send_header('Content-Length',str(len(data)))
            self.end_headers();self.wfile.write(data)
            return
        if path not in ('/', '/index.html'):
            self.send_error(404)
            return
        if not accounts.site_enabled(self):
            return self.send_error(404)
        return self.serve_draft('/index2.html')

    def do_PUT(self):
        if not luminous_port.handle(self):self.send_error(404)

    def do_DELETE(self):
        if not luminous_port.handle(self):self.send_error(404)

    def serve_draft(self, path, view='', video_id=''):
        folder = ROOT / 'static/luminus/profiles'
        if path in ('/api/shorts', '/api/videos'):
            channel = parse_qs(urlsplit(self.path).query).get('channel', [''])[0]
            if view:channel='';query=''
            params = parse_qs(urlsplit(self.path).query)
            offset = params.get('offset', ['0'])[0]
            result = youtube_shorts.video_feed(params.get('seed', ['default'])[0], int(offset) if offset.isdigit() else 0, channel, params.get('q',[''])[0], params.get('tab',['videos'])[0], params.get('includeSongs',['0'])[0]=='1') if path == '/api/videos' else youtube_shorts.shorts_page(channel, int(offset) if offset.isdigit() else 0,params.get('seed',[''])[0])
            data = json.dumps(result, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'private, max-age=60' if path == '/api/shorts' else 'no-store')
            self.end_headers()
            self.wfile.write(data)
            return
        if path in ('/assets/previews.js', '/assets/shorts.js', '/assets/shorts.css', '/assets/brand-logo.png', '/assets/accounts.js', '/assets/accounts.css', '/assets/layout.js', '/assets/layout.css', '/assets/videos.js', '/assets/videos.css'):
            return self.serve_asset(path)
        if path == '/index2.html':
            profiles = database.profiles()
            if not profiles:
                self.send_error(500, 'Channel list missing')
                return
            rows = []
            query = parse_qs(urlsplit(self.path).query).get('q', [''])[0].strip()[:100]
            channel = parse_qs(urlsplit(self.path).query).get('channel', [''])[0]
            ordered=[]
            for group,members in GROUPS:
                rows.append('<li class="channel-group"><h2>'+html.escape(group)+'</h2></li>')
                for position,name in members:
                    found=next((p for p in profiles if p['position']==position),None)
                    if found: ordered.append((group,found))
            rows=[]
            last_group=None
            for group,profile in ordered:
                if group!=last_group:
                    
                    if group:rows.append('<li class="channel-group"><h2>'+html.escape(group)+'</h2></li>')
                    last_group=group
                index=profile['position']
                photo = str(profile['photo'])
                if Path(photo).name != photo or (photo != '01.svg' and Path(photo).suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp', '.gif')) or not (folder / photo).is_file():
                    self.send_error(500, 'Profile photo missing or invalid')
                    return
                rows.append('<li><button type="button" class="profile" data-channel="' + str(index) + '" aria-pressed="' + str(channel == str(index)).lower() + '"><img class="profile-avatar" src="/profiles/' + html.escape(photo, quote=True) + '" alt=""><span class="profile-name">' + html.escape(str(profile['name'])) + '</span></button></li>')
            template = (ROOT / 'templates/luminus/index2.html').read_text(encoding='utf-8')
            panel = ''
            selected_tab='videos';songs_only=False
            chosen = next((p for p in profiles if str(p['position'])==channel),None)
            if chosen:
                kanna=chosen['position']==2
                tabs = [('videos', '동영상')] if kanna else [('videos', '동영상'),('shorts','Shorts')]
                if not kanna and chosen['name'] not in ('강지',):
                    tabs.append(('songs', '노래'))
                if not kanna and chosen['name'] != '스텔라이브':
                    tabs.append(('live', '라이브'))
                selected_tab = parse_qs(urlsplit(self.path).query).get('tab', ['videos'])[0]
                if selected_tab not in dict(tabs):
                    selected_tab = 'videos'
                channel_videos=[v for v in youtube_shorts.cached().get('videos',[]) if v['channelId']==chosen['youtube_channel_id']]
                songs_only=kanna or bool(channel_videos) and all(youtube_shorts.is_song(v) for v in channel_videos)
                panel = '<nav class="channel-tabs" aria-label="채널 콘텐츠">' + ''.join(
                    '<button type="button" class="channel-tab" data-tab="' + key + '" aria-pressed="' + str(key == selected_tab).lower() + '">' + label + '</button>'
                    for key, label in tabs) + '<label class="song-toggle"'+(' hidden' if selected_tab!='videos' or kanna else '')+'><input type="checkbox" class="include-songs"' + (' checked' if songs_only else '') + '> 노래영상 표시</label></nav>'
            if self.public_auth_request():
                template=template.replace('http://127.0.0.1/index2.html','/')
            template = template.replace('<!-- CHANNEL_CONTENT -->', panel)
            if view:
                page=pages.watch(video_id,('https://' if self.public_auth_request() else 'http://')+self.headers.get('Host','127.0.0.1')) if view=='watch' else (library.page(view,accounts.current(self),parse_qs(urlsplit(self.path).query)) if view in library.VIEWS else pages.schedule(parse_qs(urlsplit(self.path).query).get('date',[''])[0]))
                if page is None:return self.send_error(404)
                title,content=page
                template=re.sub(r'<title>.*?</title>',lambda _: '<title>'+html.escape(title)+' · 루미너스</title>',template,count=1)
                template=template.replace('<body>','<body class="'+view+'-page'+(' sidebar-closed' if view=='watch' else '')+'" data-view="'+view+'">')
                if view=='watch':
                    template=template.replace('aria-label="사이드바 닫기"','aria-label="사이드바 열기"',1).replace('aria-expanded="true"','aria-expanded="false"',1)
                template=template.replace('</head>','<link rel="stylesheet" href="/assets/pages.css"></head>')
                if view=='watch':template=template.replace('</body>','<script src="/assets/watch.js" defer></script><script src="/assets/comments.js" defer></script></body>')
                if view=='watch' or view in library.VIEWS:template=template.replace('</body>','<script src="/assets/library.js" defer></script></body>')
                template=template.replace('<!-- PAGE_CONTENT -->',content)
                template=template.replace('class="shorts-section"','class="shorts-section" hidden').replace('class="videos-section"','class="videos-section" hidden')
            settings=admin.home_settings()
            template=template.replace('<body>', '<body style="--video-title-size:'+str(settings['titleSize'])+'px">')
            notice='<p class="home-announcement">'+html.escape(settings['announcement'])+'</p>' if settings['announcement'] and not view and not channel and not query else ''
            template=template.replace('<!-- HOME_ANNOUNCEMENT -->',notice)
            if not settings['showShorts'] and not channel:
                template=template.replace('class="shorts-section"','class="shorts-section" data-disabled="true" hidden')
            if not view and (settings['showShorts'] or channel) and not query and (not channel or selected_tab == 'shorts'):
                shorts_seed=secrets.token_hex(16) if not channel else ''
                shorts = youtube_shorts.shorts_page(channel,seed=shorts_seed)
                short_cards = []
                for index, item in enumerate(shorts['items']):
                    if not re.fullmatch(r'[\w-]{11}', item['id']):
                        continue
                    esc = lambda value: html.escape(str(value), quote=True)
                    image_loading = 'loading="eager" fetchpriority="high"' if index < 6 else 'loading="lazy"'
                    short_cards.append('<article class="short-card" data-short-id="'+esc(item['id'])+'"><a class="short-play" href="/watch?v='+esc(item['id'])+'" aria-label="'+esc(item['title'])+' 재생"><img src="'+esc(item['thumbnail'])+'" alt="" '+image_loading+' decoding="async"></a><h3><a href="/watch?v='+esc(item['id'])+'">'+esc(item['title'])+'</a></h3><a class="short-source" href="/watch?v='+esc(item['id'])+'">'+esc(item['channelName'])+'</a></article>')
                template = template.replace('<div class="shorts-track"></div>', '<div class="shorts-track">'+''.join(short_cards)+'</div>')
                bootstrap = json.dumps(dict(shorts, channel=channel, seed=shorts_seed), ensure_ascii=False).replace('<', '\\u003c')
                template = template.replace('<!-- SHORTS_BOOTSTRAP -->', '<script type="application/json" id="shorts-bootstrap">'+bootstrap+'</script>')
            seed=secrets.token_hex(12)
            initial=youtube_shorts.video_feed(seed,0,channel,query,selected_tab,songs_only) if not view and selected_tab in ('videos','songs') else dict(items=[],nextOffset=0,hasMore=False)
            cards=[]
            for index,video in enumerate(initial['items']):
                if not re.fullmatch(r'[\w-]{11}',video['id']):continue
                esc=lambda text:html.escape(str(text),quote=True)
                priority='loading="eager" fetchpriority="high"' if index<6 else 'loading="lazy"'
                cards.append('<article class="video-card'+(' is-short' if video.get('kind')=='shorts' else '')+'" data-video-id="'+esc(video['id'])+'"><a href="/watch?v='+esc(video['id'])+'"><img src="'+esc(video['thumbnail'])+'" alt="" '+priority+' decoding="async"><h3>'+esc(video['title'])+'</h3></a><p>'+esc(video['channelName'])+(' · Shorts' if video.get('kind')=='shorts' else '')+'</p></article>')
            grid='<div class="video-grid" data-seed="'+seed+'" data-offset="'+str(initial['nextOffset'])+'" data-more="'+str(initial['hasMore']).lower()+'">'+''.join(cards)+'</div>'
            template=template.replace('<div class="video-grid"></div>',grid)

            if not view:
                navigation_user=accounts.current(self);navigation_views={}
                for navigation_view in ('history','bookmarks','playlists','gallery'):
                    try:navigation_views[navigation_view]=library.dataset({'view':[navigation_view]},navigation_user)
                    except library.LibraryError as error:navigation_views[navigation_view]=dict(items=[],hasMore=False,nextOffset=0,error=error.message)
                navigation_data=json.dumps(dict(owner=('@'+navigation_user['handle']) if navigation_user else '',views=navigation_views),ensure_ascii=False).replace('<','\\u003c')
                template=template.replace('</body>','<script type="application/json" id="navigation-bootstrap">'+navigation_data+'</script></body>')
            template=re.sub(r'/assets/([A-Za-z0-9_.-]+)(?![A-Za-z0-9_.-])',lambda match: '/assets/'+match[1]+'?v='+self.asset_version(match[1]),template)
            count = len(rows)
            if not rows:
                rows.append('<li class="profile-empty">검색 결과가 없습니다.</li>')
            template = template.replace('<p class="search-status" role="status" aria-live="polite"></p>', '<p class="search-status" role="status" aria-live="polite">' + (html.escape(query) + ' 검색 결과: ' + str(count) + '명' if query else '') + '</p>')
            template = template.replace('name="q" placeholder=', 'name="q" value="' + html.escape(query, quote=True) + '" placeholder=')
            data = re.sub(r'(<ul class="profiles"[^>]*>).*?(</ul>)', lambda m: m[1] + '\n'.join(rows) + m[2], template, flags=re.S).encode('utf-8')
            content_type = 'text/html; charset=utf-8'
        else:
            name = path.removeprefix('/profiles/')
            if Path(name).name != name or not name or (name != '01.svg' and Path(name).suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp', '.gif')):
                self.send_error(404)
                return
            file = folder / name
            if not file.is_file() or not file.resolve().is_relative_to(folder.resolve()):
                self.send_error(404)
                return
            data = file.read_bytes()
            content_type = mimetypes.guess_type(name)[0] or 'application/octet-stream'
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        for value in getattr(self,'visitor_cookies',[]):self.send_header('Set-Cookie',value)
        self.send_header('Cache-Control', 'public, max-age=3600' if path.startswith('/profiles/') else 'private, no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(data)


def lock(handle):
    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)


def main():
    if '--stop' in sys.argv:
        (RUNTIME / '.supervisor-stop').touch()
    with (RUNTIME / '.running.lock').open('a+b') as handle:
        if (RUNTIME / '.running.lock').stat().st_size == 0:
            handle.write(b'0')
            handle.flush()
        try:
            lock(handle)
        except OSError:
            if '--status' in sys.argv:
                return 0
            if '--stop' not in sys.argv:
                print('Already running.')
                return 0
            STOP.touch()
            for _ in range(150):
                time.sleep(0.1)
                try:
                    lock(handle)
                except OSError:
                    continue
                print('Web server stopped.')
                return 0
            raise RuntimeError('Stop timed out. Try again.')
        if '--status' in sys.argv:
            return 1
        if '--stop' in sys.argv:
            STOP.unlink(missing_ok=True)
            print('Already stopped.')
            return 0
        STOP.unlink(missing_ok=True)
        try:
            luminous_port.init()
            with ThreadingHTTPServer(('127.0.0.1', 8081 if (RUNTIME/'nginx-enabled').exists() else 80), Handler) as server:
                server.timeout = 0.3
                youtube_shorts.feed()
                search_engine.start()
                admin_operations.start()
                channel_details.start()
                import image_cache
                image_cache.start()
                print('Running locally: http://127.0.0.1/index2.html', flush=True)
                while not STOP.exists():
                    server.handle_request()
        finally:
            search_engine.close()
            admin_operations.close()
            channel_details.close()
            if 'image_cache' in locals():image_cache.stopped.set()
            STOP.unlink(missing_ok=True)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass
    except Exception as error:
        print(f'ERROR: {error}', file=sys.stderr)
        sys.exit(1)
