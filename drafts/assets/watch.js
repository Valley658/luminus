(() => {
  const frame=document.querySelector('#watch-player');
  if(!frame)return;
  const soundButton=document.querySelector('.watch-sound-start');
  let player,disablingCaptions=false,captionDeadline=Infinity;
  const stage=document.querySelector('.watch-stage'),endPanel=document.querySelector('.watch-end');
  const countdown=document.querySelector('.watch-next-countdown'),autoButton=document.querySelector('.watch-auto-toggle');
  let endTimer=null,ended=false,secondsLeft=8,autoNext=true,playbackTimer=null,lastPlaybackTime=0;
  try{autoNext=sessionStorage.getItem('luminous-watch-auto-next')!=='0';}catch(_){}
  function stopCountdown(){if(endTimer!==null)clearInterval(endTimer);endTimer=null;}
  function paintAuto(){autoButton.textContent=autoNext?'자동 넘김 켜짐':'자동 넘김 꺼짐';autoButton.setAttribute('aria-pressed',String(autoNext));}
  function beginCountdown(){
    stopCountdown();paintAuto();secondsLeft=8;
    if(!autoNext||!endPanel.dataset.next){countdown.textContent='영상을 선택해 주세요.';return;}
    countdown.textContent=secondsLeft+'초 후 다음 영상';
    endTimer=setInterval(()=>{secondsLeft--;if(secondsLeft<=0){stopCountdown();location.assign('/watch?v='+encodeURIComponent(endPanel.dataset.next));}else countdown.textContent=secondsLeft+'초 후 다음 영상';},1000);
  }
  function showEnd(){if(ended)return;ended=true;try{player?.pauseVideo();}catch(_){}stage.classList.add('is-ended');endPanel.hidden=false;soundButton.hidden=true;if(document.fullscreenElement)document.exitFullscreen?.().catch(()=>{});beginCountdown();}
  function hideEnd(){ended=false;lastPlaybackTime=0;stopCountdown();endPanel.hidden=true;stage.classList.remove('is-ended');}
  autoButton.addEventListener('click',()=>{autoNext=!autoNext;try{sessionStorage.setItem('luminous-watch-auto-next',autoNext?'1':'0');}catch(_){}if(ended)beginCountdown();else paintAuto();});
  document.querySelector('.watch-replay').addEventListener('click',()=>{hideEnd();if(player){player.seekTo(0,true);playWithSound(player);}});
  window.addEventListener('pagehide',()=>{stopCountdown();if(playbackTimer!==null)clearInterval(playbackTimer);},{once:true});
  function monitorShortsEnd(){
    if(ended||!stage.classList.contains('is-short')||!player)return;
    try{
      if(player.getPlayerState()!==YT.PlayerState.PLAYING)return;
      const duration=player.getDuration(),current=player.getCurrentTime();
      if(duration>0&&(current>=duration-.12||(lastPlaybackTime>=duration-.8&&current<.5))){showEnd();return;}
      lastPlaybackTime=current;
    }catch(_){}
  }
  paintAuto();

  const captionTimers=[];
  function defaultCaptionsOff(target){
    if(disablingCaptions||performance.now()>captionDeadline)return;
    disablingCaptions=true;
    try{
      const modules=target.getOptions?.()||[];
      if(!modules.includes('captions'))return;
      target.setOption?.('captions','track',{});
      target.unloadModule?.('captions');
    }catch(_){}finally{disablingCaptions=false;}
  }
  function initializeCaptionDefaults(target){
    captionDeadline=performance.now()+10000;
    defaultCaptionsOff(target);
    for(const delay of [250,1000,2500,5000,9000])captionTimers.push(setTimeout(()=>defaultCaptionsOff(target),delay));
  }
  window.addEventListener('pagehide',()=>captionTimers.forEach(clearTimeout),{once:true});
  function playWithSound(target){
    target.unMute();
    if(target.getVolume()===0)target.setVolume(70);
    target.playVideo();
  }
  async function initialize(){
    try{
      await window.luminousYouTubeReady();
      player=new YT.Player(frame,{events:{
        onReady:event=>{try{event.target.setLoop?.(false);}catch(_){}initializeCaptionDefaults(event.target);playWithSound(event.target);if(stage.classList.contains('is-short'))playbackTimer=setInterval(monitorShortsEnd,200);},
        onApiChange:event=>defaultCaptionsOff(event.target),
        onStateChange:event=>{if(event.data===YT.PlayerState.ENDED){showEnd();}else if(event.data===YT.PlayerState.PLAYING){if(ended){event.target.pauseVideo();return;}soundButton.hidden=true;defaultCaptionsOff(event.target);}},
        onAutoplayBlocked:()=>{soundButton.hidden=false;}
      }});
    }catch(_){/* The regular iframe player remains usable if the API cannot load. */}
  }
  soundButton.addEventListener('click',()=>{if(player)playWithSound(player);});
  initialize();

  const track=document.querySelector('.watch-shorts');
  const previous=document.querySelector('[data-watch-scroll="-1"]');
  const next=document.querySelector('[data-watch-scroll="1"]');
  const seen=new Set(Array.from(track.querySelectorAll('a')).map(a=>new URL(a.href).searchParams.get('v')));
  let offset=Number(track.dataset.offset||0),more=track.dataset.more==='true',loading=false;
  function update(){
    const image=track.querySelector('img');
    if(image?.clientHeight)track.closest('.watch-shorts-carousel').style.setProperty('--watch-arrow-top',(image.clientHeight/2)+'px');
    previous.disabled=track.scrollLeft<=2;
    next.disabled=loading||(!more&&track.scrollLeft+track.clientWidth>=track.scrollWidth-3);
    next.setAttribute('aria-busy',String(loading));
  }
  async function loadMore(){
    if(loading||!more)return;
    loading=true;update();
    try{
      const response=await fetch('/api/shorts?'+new URLSearchParams({offset,channel:''}));
      if(!response.ok)throw Error();
      const data=await response.json();
      for(const item of data.items){
        if(!/^[A-Za-z0-9_-]{11}$/.test(item.id)||seen.has(item.id)||item.id===track.dataset.exclude)continue;
        seen.add(item.id);
        const card=document.createElement('article');card.className='watch-card watch-short';
        const link=document.createElement('a');link.href='/watch?v='+item.id;
        const image=document.createElement('img');image.src=item.thumbnail;image.alt='';image.loading='lazy';image.decoding='async';
        const description=document.createElement('div'),title=document.createElement('h3'),channel=document.createElement('p');
        title.textContent=item.title;channel.textContent=item.channelName;description.append(title,channel);link.append(image,description);card.append(link);track.append(card);
      }
      offset=data.nextOffset;more=data.hasMore;
      next.title='다음 추천 Shorts';
    }catch(_){next.title='목록을 불러오지 못했습니다. 다시 눌러 주세요.';}
    finally{loading=false;update();}
  }
  previous.addEventListener('click',()=>track.scrollBy({left:-(track.clientWidth+12),behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'}));
  next.addEventListener('click',async()=>{
    if(track.scrollLeft+track.clientWidth>=track.scrollWidth-track.clientWidth-24&&more)await loadMore();
    track.scrollBy({left:track.clientWidth+12,behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
    update();
  });
  track.addEventListener('scroll',update,{passive:true});
  track.addEventListener('load',update,true);
  new ResizeObserver(update).observe(track);
  update();
})();
