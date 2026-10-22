(() => {
  const section = document.querySelector('.videos-section');
  const grid = section.querySelector('.video-grid');
  const status = section.querySelector('.video-status');
  const more = section.querySelector('.video-more');
  let seed = crypto.randomUUID ? crypto.randomUUID() : String(Math.random());
  let channel = new URLSearchParams(location.search).get('channel') || '';
  let query = new URLSearchParams(location.search).get('q') || '';
  let tab = new URLSearchParams(location.search).get('tab') || 'videos', includeSongs=!!document.querySelector('.include-songs')?.checked;
  let offset = 0, loading = false, hasMore = true, version = 0, timer;
  const seen = new Set();
  async function load() {
    timer = null;
    if (loading || !hasMore || section.hidden) return;
    loading = true;
    const current = version;
    status.textContent = '';
    more.hidden = true;
    try {
      const response = await fetch('/api/videos?' + new URLSearchParams({seed, offset, channel, q:query,tab,includeSongs:includeSongs?'1':'0'}));
      if (!response.ok) throw new Error();
      const data = await response.json();
      if (current !== version) return;
      for (const video of data.items) {
        if (seen.has(video.id) || !/^[\w-]{11}$/.test(video.id)) continue;
        seen.add(video.id);
        const card = document.createElement('article'); card.className = 'video-card';card.classList.toggle('is-short',video.kind==='shorts');card.dataset.videoId=video.id;
        const link = document.createElement('a'); link.href = '/watch?v=' + video.id;
        const image = document.createElement('img'); image.src = video.thumbnail; image.alt = ''; image.loading = 'lazy'; image.decoding = 'async';
        const title = document.createElement('h3'); title.textContent = video.title;
        const name = document.createElement('p'); name.textContent = video.channelName + (video.kind==='shorts'?' · Shorts':' · YouTube');
        link.append(image, title); card.append(link, name); grid.append(card);
      }
      offset = data.nextOffset; hasMore = data.hasMore;
      if (!seen.size && data.refreshing) {
        hasMore = true; status.textContent = ''; 
        timer = setTimeout(load, 5000);
      } else {
        status.textContent = hasMore ? '' : seen.size ? '준비된 동영상을 모두 확인했습니다.' : data.error ? '목록을 가져오지 못했습니다. 잠시 후 다시 시도해 주세요.' : '아직 표시할 동영상이 없습니다.';
        more.hidden = !hasMore;
      }
    } catch (_) {
      if (current === version) { status.textContent = '동영상을 불러오지 못했습니다.'; more.hidden = false; }
    } finally {
      if (current === version) loading = false;
    }
  }
  more.addEventListener('click', load);
  new IntersectionObserver(entries => {
    if (entries.some(e => e.isIntersecting) && !timer) load();
  }, {root:document.querySelector('main'), rootMargin:'600px'}).observe(status);
  window.updateVideosChannel = (value, searchTerm = '', nextTab='videos', songs=false, initialGrid=null) => {
    window.stopVideoPreview?.();
    tab=nextTab;includeSongs=songs;
    query = searchTerm;
    section.querySelector('h2').textContent = '검색 결과';
    section.querySelector('h2').hidden=!query;
    version++; clearTimeout(timer); timer = null; channel = value;
    grid.replaceChildren(); seen.clear(); offset = 0; loading = false; hasMore = true;
    if(initialGrid){grid.append(...Array.from(initialGrid.children).map(node=>node.cloneNode(true)));adoptInitial(initialGrid);}
    else load();
  };
  function adoptInitial(source){
    seed=source.dataset.seed||seed;offset=Number(source.dataset.offset||0);hasMore=source.dataset.more==='true';
    grid.querySelectorAll('[data-video-id]').forEach(card=>seen.add(card.dataset.videoId));
    status.textContent='';more.hidden=!hasMore;
    if(!seen.size&&!section.hidden)load();
  }
  section.querySelector('h2').hidden=!query;
  if(grid.dataset.seed)adoptInitial(grid);else load();
})();
