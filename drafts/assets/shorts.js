(() => {
  const section = document.querySelector('.shorts-section');
  const track = section.querySelector('.shorts-track');
  const message = section.querySelector('.shorts-message');
  const modal = document.querySelector('.short-viewer');
  let videos = [],rendered=0,nextOffset=0,hasMore=false,loading=false;
  const more=section.querySelector('.shorts-more');
  let preview = null;
  let fullPlayer = null;
  let hoverTimer;
  let hovering = null;
  let pollTimer;
  let generation = 0;
  let youtubeReady;
  const arrows = section.querySelector('.shorts-arrows');
  const carousel = section.querySelector('.shorts-carousel');
  function updateArrows() {
    const card = track.querySelector('.short-play');
    arrows.hidden = !card || section.classList.contains('channel-shorts');
    if (!card) return;
    carousel.style.setProperty('--shorts-arrow-top', (card.clientHeight / 2) + 'px');
    arrows.querySelector('[data-scroll="-1"]').disabled = track.scrollLeft <= 1;
    arrows.querySelector('[data-scroll="1"]').disabled = track.scrollLeft + track.clientWidth >= track.scrollWidth - 2;
  }
  track.addEventListener('scroll', () => {updateArrows();if(track.scrollLeft+track.clientWidth>=track.scrollWidth-500&&!section.classList.contains('channel-shorts'))loadMore();}, {passive: true});
  new ResizeObserver(updateArrows).observe(track);
  function youtube() {
    if (window.YT?.Player) return Promise.resolve();
    if (!youtubeReady) youtubeReady = new Promise((resolve, reject) => {
      const previous = window.onYouTubeIframeAPIReady;
      window.onYouTubeIframeAPIReady = () => { if (previous) previous(); resolve(); };
      const script = document.createElement('script');
      script.src = 'https://www.youtube.com/iframe_api';
      script.onerror = () => reject(new Error('player unavailable'));
      document.head.append(script);
    });
    return youtubeReady;
  }
  window.luminousYouTubeReady=youtube;
  function stopPreview() {
    clearTimeout(hoverTimer);
    hovering = null;
    if (preview) { preview.destroy(); preview = null; }
    track.querySelectorAll('.short-preview').forEach(node => node.remove());
  }
  window.addEventListener('luminous-preview-start',stopPreview);
  async function hover(button, video) {
    window.dispatchEvent(new Event('luminous-preview-start'));
    stopPreview();
    hovering = button;
    hoverTimer = setTimeout(async () => {
      try {
        await youtube();
        if (hovering !== button || modal.open) return;
        const wrapper = document.createElement('div');
        wrapper.className = 'short-preview';
        const mount = document.createElement('div');
        wrapper.append(mount); button.append(wrapper);
        preview = new YT.Player(mount, {videoId: video.id,
          playerVars: {autoplay: 1, controls: 0, playsinline: 1, origin: location.origin},
          events: {onReady: event => { event.target.mute(); event.target.playVideo(); }, onError: stopPreview}});
      } catch (_) { stopPreview(); }
    }, 450);
  }
  modal.addEventListener('close', () => {
    if (fullPlayer) { fullPlayer.destroy(); fullPlayer = null; }
    modal.querySelector('.short-player').replaceChildren();
  });
  modal.querySelector('.short-close').addEventListener('click', () => modal.close());
  document.addEventListener('visibilitychange', () => { if (document.hidden) { stopPreview(); fullPlayer?.pauseVideo(); } });
  function wire(card, video) {
    const button=card.querySelector('.short-play');
    card.addEventListener('pointerenter',event=>{if(event.pointerType==='mouse'&&!document.querySelector('.fan-notice').open)hover(button,video);});
    card.addEventListener('pointerleave',stopPreview);
    button.addEventListener('click',stopPreview);
  }
  function render(items) {
    stopPreview(); track.replaceChildren();
    videos = items;rendered=0;appendCards();
  }
  function appendCards() {
    const batch=section.classList.contains('channel-shorts')?40:videos.length;
    const next=videos.slice(rendered,rendered+batch);rendered+=next.length;
    for (const video of next) {
      if (!/^[\w-]{11}$/.test(video.id)) continue;
      const card = document.createElement('article'); card.className = 'short-card';
      const button = document.createElement('a'); button.className = 'short-play'; button.href = '/watch?v='+video.id;
      button.setAttribute('aria-label', video.title + ' 재생');
      const image = document.createElement('img'); image.src = video.thumbnail; image.alt = ''; image.loading = 'lazy';
      button.append(image);
      wire(card,video);
      const title = document.createElement('h3');const titleLink=document.createElement('a');titleLink.href='/watch?v='+video.id;titleLink.textContent=video.title;title.append(titleLink);
      const source = document.createElement('a'); source.className = 'short-source'; source.textContent = video.channelName + ' · YouTube';
      source.href = '/watch?v=' + video.id;
      card.append(button, title, source); track.append(card);
    }
    more.hidden=!section.classList.contains('channel-shorts')||(rendered>=videos.length&&!hasMore);
    updateArrows();
  }
  function loadMore(){if(loading||!hasMore)return;if(rendered<videos.length)appendCards();else load(true);}
  more.addEventListener('click',loadMore);
  new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting)&&!section.hidden&&section.classList.contains('channel-shorts'))loadMore();},{root:document.querySelector('main'),rootMargin:'500px'}).observe(section.querySelector('.shorts-sentinel'));
  async function load(append=false) {
    clearTimeout(pollTimer);
    if(append&&loading)return;loading=true;
    const current = append?generation:++generation;
    const channel = section.dataset.channel || '';
    try {
      const response = await fetch('/api/shorts?' + new URLSearchParams({channel,offset:append?nextOffset:0}));
      if (!response.ok) throw new Error('fetch failed');
      const data = await response.json();
      if (current !== generation) return;
      nextOffset=data.nextOffset;hasMore=data.hasMore;
      if(append){videos.push(...data.items);appendCards();}else render(data.items);
      message.textContent = data.items.length ? '' : !data.configured ? 'Shorts를 준비 중입니다.' : data.refreshing ? 'Shorts를 불러오는 중입니다.' : data.error ? 'Shorts를 불러오지 못했습니다. 잠시 후 다시 확인해 주세요.' : '아직 표시할 Shorts가 없습니다.';
      message.hidden = !!data.items.length;
      if (data.refreshing) pollTimer = setTimeout(()=>load(), 5000);
      else if (data.configured) pollTimer = setTimeout(()=>load(), data.error ? 300000 : 1800000);
    } catch (_) {
      if (current === generation && !videos.length){message.hidden=false;message.textContent='Shorts를 불러오지 못했습니다. 잠시 후 다시 확인해 주세요.';}
    }finally{if(current===generation)loading=false;}
  }
  section.querySelectorAll('[data-scroll]').forEach(button => button.addEventListener('click', () => {
    track.scrollBy({left: Number(button.dataset.scroll) * track.clientWidth * .85, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
  }));
  window.updateShortsChannel = channel => { section.dataset.channel = channel; load(); };
  section.dataset.channel = new URLSearchParams(location.search).get('channel') || '';
  const bootstrap=document.querySelector('#shorts-bootstrap');
  let adopted=false;
  try{
    const data=JSON.parse(bootstrap?.textContent||'null');
    if(data&&data.channel===section.dataset.channel){
      videos=data.items;rendered=videos.length;nextOffset=data.nextOffset;hasMore=data.hasMore;
      for(const card of track.querySelectorAll('[data-short-id]')){const video=videos.find(v=>v.id===card.dataset.shortId);if(video)wire(card,video);}
      message.hidden=!!videos.length;more.hidden=!section.classList.contains('channel-shorts')||!hasMore;updateArrows();adopted=true;
      pollTimer=setTimeout(()=>load(),data.refreshing?5000:1800000);
    }
  }catch(_){}
  if (!section.hidden&&!adopted) load();
})();
