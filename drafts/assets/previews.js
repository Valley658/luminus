(() => {
  let active=null,frame=null,timer=null,player=null;
  function stop(){clearTimeout(timer);timer=null;if(player){player.destroy();player=null;}frame?.remove();frame=null;active=null;}
  window.stopVideoPreview=stop;
  window.addEventListener('luminous-preview-start',stop);
  function candidate(target){return target instanceof Element?target.closest('.video-card a,.search-suggestions a'):null;}
  document.addEventListener('pointerover',event=>{
    if(event.pointerType!=='mouse')return;
    const link=candidate(event.target);
    if(!link||link.contains(event.relatedTarget)||document.querySelector('dialog[open]'))return;
    stop();window.dispatchEvent(new Event('luminous-preview-start'));active=link;
    timer=setTimeout(async()=>{
      const url=new URL(link.href),id=url.searchParams.get('v');
      if(!/^[\w-]{11}$/.test(id||''))return;
      try {
        await window.luminousYouTubeReady();
        if(active!==link||!link.isConnected||document.querySelector('dialog[open]'))return;
        frame=document.createElement('div');frame.className='video-hover-preview';
        const mount=document.createElement('div');frame.append(mount);(link.querySelector('.watch-thumb')||link).append(frame);
        player=new YT.Player(mount,{videoId:id,playerVars:{autoplay:1,controls:0,playsinline:1,origin:location.origin},
          events:{onReady:event=>{if(active!==link){event.target.destroy();return;}event.target.mute();event.target.playVideo();},onError:stop}});
      }catch(_){if(active===link)stop();}
    },350);
  });
  document.addEventListener('pointerout',event=>{if(active&&active.contains(event.target)&&!active.contains(event.relatedTarget))stop();});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});
  document.addEventListener('click',()=>window.dispatchEvent(new Event('luminous-preview-start')),true);
  document.querySelector('main').addEventListener('scroll',stop,{passive:true});
  document.querySelector('.search-suggestions').addEventListener('scroll',stop,{passive:true});
  new MutationObserver(()=>{if(active&&(!active.isConnected||document.querySelector('dialog[open]')))stop();}).observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['open']});
})();
