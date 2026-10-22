if (location.protocol === 'file:') location.replace('http://127.0.0.1/index2.html');
const notice = document.querySelector('.fan-notice');
const noticeKey='luminous-fan-notice-accepted-v3';
window.addEventListener('pageshow',()=>{
  let accepted=false;try{accepted=localStorage.getItem(noticeKey)==='1';}catch(_){}
  if(!accepted&&!notice.open)notice.showModal();
});
notice.querySelector('form').addEventListener('submit',()=>{try{localStorage.setItem(noticeKey,'1');}catch(_){}});
// The toggle lives in the fixed header, outside the panel that is hidden.
const topbar = document.querySelector('.topbar');
const header = document.querySelector('.sidebar-header');
topbar.append(header);
const toggle = document.querySelector('.sidebar-toggle');
toggle.addEventListener('click', () => {
  const closed = document.body.classList.toggle('sidebar-closed');
  toggle.setAttribute('aria-expanded', String(!closed));
  toggle.setAttribute('aria-label', closed ? '사이드바 열기' : '사이드바 닫기');
});
const params = new URLSearchParams(location.search);
let channel = params.get('channel') || '';
let tab = params.get('tab') || 'videos';
const search = document.querySelector('.search-form');
const input = search.querySelector('input');
const suggestions = document.querySelector('.search-suggestions');
const shorts = document.querySelector('.shorts-section');
function updateContentMode() {
  if(document.body.dataset.view){shorts.hidden=true;document.querySelector('.videos-section').hidden=true;document.querySelector('.channel-panel').hidden=true;return;}
  const channelShorts=!!channel&&tab==='shorts',live=!!channel&&tab==='live';
  shorts.classList.toggle('channel-shorts',channelShorts);
  shorts.querySelector('.shorts-heading').hidden=!!channel;
  shorts.hidden=(!channel&&shorts.dataset.disabled==='true')||live|| (!!channel&&!channelShorts) || !!input.value.trim();
  document.querySelector('.videos-section').hidden=channelShorts||live;
  document.querySelector('.live-unavailable').hidden=!live;
}
updateContentMode();
let pageController, suggestionController, timer;
async function selectChannel(value, nextTab = 'videos') {
  if(document.body.dataset.view){location.assign('/?'+new URLSearchParams({channel:value,tab:nextTab}));return;}
  window.dispatchEvent(new Event('luminous-preview-start'));
  channel = value; tab = value==='2'?'videos':nextTab; input.value = ''; suggestions.hidden = true; shorts.hidden = true;
  if (pageController) pageController.abort();
  pageController = new AbortController();
  try {
    const response = await fetch('/index2.html?' + new URLSearchParams({channel,tab}), {signal:pageController.signal});
    if (!response.ok) throw new Error();
    const page = new DOMParser().parseFromString(await response.text(),'text/html');
    document.querySelector('.profiles').replaceChildren(...page.querySelector('.profiles').childNodes);
    document.querySelector('.channel-panel').replaceChildren(...page.querySelector('.channel-panel').childNodes);
    updateContentMode();
    document.querySelector('#shorts-title').textContent=channel?'Shorts':'추천 Shorts';
    if(tab==='shorts')window.updateShortsChannel?.(channel);
    else if(tab!=='live')window.updateVideosChannel?.(channel,'',tab,!!document.querySelector('.include-songs')?.checked,page.querySelector('.video-grid'));
    document.querySelector('main').scrollTop = 0;
  } catch(error) { if (error.name !== 'AbortError') document.querySelector('.search-status').textContent = '채널을 불러오지 못했습니다.'; }
}
document.querySelector('.profiles').addEventListener('click', event => {
  const button = event.target.closest('[data-channel]'); if(button) selectChannel(button.dataset.channel);
});
document.querySelector('.channel-panel').addEventListener('click', event => {
  const button = event.target.closest('[data-tab]'); if(button) selectChannel(channel,button.dataset.tab);
});
search.addEventListener('submit', event => {
  event.preventDefault();if(document.body.dataset.view){location.assign('/?'+new URLSearchParams({q:input.value.trim()}));return;} suggestions.hidden = true; shorts.hidden = !!channel || !!input.value.trim();
  document.querySelector('.live-unavailable').hidden=true;
  document.querySelector('.videos-section').hidden=false;
  window.updateVideosChannel?.(channel,input.value.trim(),tab==='shorts'?'videos':tab,!!document.querySelector('.include-songs')?.checked); document.querySelector('main').scrollTop = 0;
});
let suggestionOffset=0,suggestionMore=false,suggestionLoading=false,suggestionQuery='';
async function loadSuggestions(reset=false) {
  const query=input.value.trim();
  if(suggestionLoading&&!reset)return;
  if(!query){suggestions.hidden=true;return;}
  if(!reset&&!suggestionMore)return;
  if(reset){suggestionController?.abort();suggestions.replaceChildren();suggestionOffset=0;suggestionQuery=query;}
  const controller=new AbortController();suggestionController=controller;suggestionLoading=true;
  try {
    const response=await fetch('/api/videos?'+new URLSearchParams({q:query,channel:'',seed:'suggestions',includeSongs:'1',offset:suggestionOffset}),{signal:controller.signal});
    if(!response.ok)throw Error();const data=await response.json();
    if(query!==input.value.trim()||controller!==suggestionController)return;
    for(const video of data.items){
      if(!/^[\w-]{11}$/.test(video.id))continue;
      const link=document.createElement('a');link.href='/watch?v='+video.id;
      const image=document.createElement('img');image.src=video.thumbnail;image.alt='';image.loading='lazy';
      const title=document.createElement('span');title.textContent=video.title;
      const name=document.createElement('small');name.textContent=video.channelName+(video.kind==='shorts'?' · Shorts':' · 동영상');title.append(name);link.append(image,title);suggestions.append(link);
    }
    suggestionOffset=data.nextOffset;suggestionMore=data.hasMore;
    if(!suggestions.childElementCount){const p=document.createElement('p');p.textContent='검색 결과가 없습니다.';suggestions.append(p);}
    suggestions.hidden=false;
  }catch(e){if(e.name!=='AbortError')suggestions.hidden=true;}
  finally{if(controller===suggestionController)suggestionLoading=false;}
}
input.addEventListener('input',()=>{
  clearTimeout(timer);suggestionController?.abort();suggestionLoading=false;
  if(!input.value.trim()){suggestions.hidden=true;return;}
  timer=setTimeout(()=>loadSuggestions(true),200);
});
suggestions.addEventListener('scroll',()=>{if(suggestions.scrollTop+suggestions.clientHeight>=suggestions.scrollHeight-150)loadSuggestions();});
input.addEventListener('keydown', event => { if(event.key==='Escape') suggestions.hidden=true; if(event.key==='ArrowDown') { const first=suggestions.querySelector('a');if(first){event.preventDefault();first.focus();} } });
document.addEventListener('click', event => { if(!event.target.closest('.search-wrap')) suggestions.hidden=true; });

document.querySelector('.channel-panel').addEventListener('change',event=>{
  if(event.target.matches('.include-songs'))window.updateVideosChannel?.(channel,input.value.trim(),tab,event.target.checked);
});

const searchToggle=document.querySelector('.search-toggle');
function toggleSearch(open){
 document.body.classList.toggle('search-open',open);searchToggle.setAttribute('aria-expanded',String(open));searchToggle.setAttribute('aria-label',open?'검색':'검색창 열기');
 if(open)input.focus();else suggestions.hidden=true;
}
searchToggle.addEventListener('click',()=>{
 if(!document.body.classList.contains('search-open'))toggleSearch(true);
 else if(input.value.trim())search.requestSubmit();
 else toggleSearch(false);
});
document.addEventListener('keydown',event=>{if(event.key==='Escape')toggleSearch(false);});
