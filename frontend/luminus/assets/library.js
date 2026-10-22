window.initializeLibrary=(scope=document)=>{
  const root=scope.querySelector('.library-page'),watch=scope.querySelector('.library-watch-tools');
  if(root?.dataset.initialized==='true'||watch?.dataset.initialized==='true')return;
  if(root)root.dataset.initialized='true';if(watch)watch.dataset.initialized='true';
  if(!root&&!watch)return;
  const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  const tell=text=>window.Swal?Swal.fire({text,background:'#222',color:'#eee',confirmButtonColor:'#bda4eb'}):Promise.resolve();
  async function api(params){const r=await fetch('/api/library?'+new URLSearchParams(params)),data=await r.json();if(!r.ok)throw Error(data.error||'불러오지 못했습니다.');return data;}
  async function post(data){const r=await fetch('/api/library',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}),result=await r.json();if(!r.ok)throw Error(result.error||'저장하지 못했습니다.');window.dispatchEvent(new Event('luminous-library-changed'));return result;}
  async function input(title){const result=await Swal.fire({title,input:'text',inputAttributes:{maxlength:100},showCancelButton:true,confirmButtonText:'저장',cancelButtonText:'취소',background:'#222',color:'#eee'});return result.isConfirmed?result.value?.trim():null;}
  if(watch){
    const video=watch.dataset.video;
    watch.addEventListener('click',async event=>{
      const button=event.target.closest('[data-save]');if(!button)return;button.disabled=true;
      try{
        if(button.dataset.save==='bookmark'){await post({action:'bookmark',video});button.textContent='북마크 저장됨';}
        if(button.dataset.save==='playlist'){
          const data=await api({view:'playlists'}),options={};data.items.forEach(p=>options[p.id]=p.title);options.new='＋ 새 재생목록';
          const result=await Swal.fire({title:'재생목록 선택',input:'select',inputOptions:options,showCancelButton:true,confirmButtonText:'추가',cancelButtonText:'취소',background:'#222',color:'#eee'});
          if(!result.isConfirmed)return;let playlist=result.value;
          if(playlist==='new'){const title=await input('새 재생목록 이름');if(!title)return;playlist=(await post({action:'playlist-create',title})).id;}
          await post({action:'playlist-add',playlist,video});await tell('재생목록에 추가했습니다.');
        }
        if(button.dataset.save==='highlight'){
          const title=await input('하이라이트 제목');if(!title)return;
          const result=await Swal.fire({title:'시작 시간 (초)',input:'number',inputValue:Math.floor(window.luminousWatchTime?.()||0),inputAttributes:{min:0,max:86400},showCancelButton:true,confirmButtonText:'저장',cancelButtonText:'취소',background:'#222',color:'#eee'});
          if(!result.isConfirmed)return;await post({action:'highlight',video,title,start:Number(result.value)});await tell('하이라이트를 저장했습니다.');
        }
      }catch(error){tell(error.message);}finally{button.disabled=false;}
    });
  }
  if(!root)return;
  const mypage=root.dataset.library==='mypage',grid=root.querySelector('.library-grid'),tools=root.querySelector('.library-tools'),status=root.querySelector('.library-status'),more=root.querySelector('.library-more');
  let bootstrap=null;try{bootstrap=JSON.parse(root.querySelector('.library-bootstrap')?.textContent||'null');}catch(_){}
  let view=mypage?'history':root.dataset.library,offset=0,playlist=new URLSearchParams(location.search).get('playlist')||'',busy=false,generation=0;
  function button(label,action){const b=node('button',label);b.type='button';b.onclick=async()=>{b.disabled=true;try{await action();}catch(error){tell(error.message);}finally{b.disabled=false;}};return b;}
  async function profile(){
    if(!mypage)return;
    const data=await api({view:'mypage'}),hero=root.querySelector('.mypage-hero');
    if(!hero){const top=root.querySelector('.profile-top-section');if(top&&!top.querySelector('[data-legacy-settings]')){const settings=button('프로필 설정',()=>document.querySelector('.account-settings').click());settings.dataset.legacySettings='true';top.append(settings);}return;}
    hero.replaceChildren();
    const avatar=data.user.avatar;
    if(avatar){const image=node('img');image.src=avatar;image.alt='내 프로필';hero.append(image);}else hero.append(node('span',data.user.name.slice(0,1),'mypage-avatar'));
    const identity=node('div');identity.append(node('p','나만의 루미너스','mypage-eyebrow'),node('h2',data.user.name),node('p',data.user.handle));hero.append(identity);
    const stats=node('div',undefined,'mypage-stats');for(const [key,label] of [['history','시청 기록'],['bookmarks','북마크'],['playlists','재생목록'],['gallery','내 업로드']]){const box=node('div');box.append(node('strong',String(data.counts[key])),node('span',label));stats.append(box);}hero.append(stats);
    hero.append(button('프로필 설정',()=>document.querySelector('.profile-dialog').showModal()));
  }
  function controls(){
    tools.replaceChildren();
    if(view==='playlists'){
      if(playlist)tools.append(button('목록으로',()=>{playlist='';load(true);}));
      else tools.append(button('＋ 재생목록 만들기',async()=>{const title=await input('새 재생목록 이름');if(!title)return;await post({action:'playlist-create',title});await load(true);await profile();}));
    }
    if(view==='gallery'){
      const form=node('form',undefined,'gallery-upload'),title=node('input'),file=node('input'),submit=node('button','사진 올리기');title.placeholder='작품 제목';title.maxLength=100;title.required=true;title.setAttribute('aria-label','작품 제목');file.type='file';file.accept='image/jpeg,image/png,image/webp,image/gif';file.required=true;file.setAttribute('aria-label','작품 사진');submit.type='submit';form.append(title,file,submit);
      form.onsubmit=async event=>{event.preventDefault();submit.disabled=true;try{const selected=file.files[0];if(!selected||selected.size>5000000)throw Error('사진은 5MB 이하로 올려 주세요.');const image=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(selected);});await post({action:'gallery',title:title.value,image});await load(true);await profile();}catch(error){tell(error.message);}finally{submit.disabled=false;}};
      tools.append(form,node('p','JPEG·PNG·WebP·GIF, 최대 5MB. 갤러리에는 첫 프레임 사진으로 게시됩니다.'));
    }
    if(view==='highlights')tools.append(node('p','시청 페이지에서 원하는 시점을 하이라이트로 저장해 보세요.'));
  }
  function card(item){
    const card=node('article',undefined,'library-card'+(view!=='gallery'&&!(view==='playlists'&&!playlist)?' video-card':''));
    if(view==='playlists'&&!playlist){
      card.append(button(item.title+' · '+item.count+'개',()=>{playlist=item.id;load(true);}));
    }else if(view==='gallery'){
      const link=node('a'),image=node('img');link.href=item.image;link.target='_blank';link.rel='noopener';image.src=item.image;image.alt=item.title;image.loading='lazy';link.append(image);card.append(link,node('h2',item.title),node('p',item.author||'탈퇴한 회원'));
    }else{
      const link=node('a'),image=node('img');link.href='/watch?'+new URLSearchParams({v:item.video_id||item.id,...(view==='highlights'?{t:item.starts_at}:{})});image.src=item.thumbnail||'https://i.ytimg.com/vi/'+(item.video_id||item.id)+'/hqdefault.jpg';image.alt='';image.loading='lazy';link.append(image,node('h2',item.title));card.append(link,node('p',view==='highlights'?(item.author||'탈퇴한 회원')+' · '+Math.floor(item.starts_at/60)+':'+String(item.starts_at%60).padStart(2,'0'):item.channelName));
    }
    if(item.canDelete)card.append(button(view==='playlists'&&playlist?'목록에서 제거':'삭제',async()=>{
      const confirm=await Swal.fire({text:'삭제하시겠습니까?',showCancelButton:true,confirmButtonText:'삭제',cancelButtonText:'취소',background:'#222',color:'#eee'});if(!confirm.isConfirmed)return;
      const data=view==='playlists'?{action:playlist?'playlist-remove':'playlist-delete',playlist:playlist||item.id,video:item.id}:view==='gallery'||view==='highlights'?{action:view==='gallery'?'gallery-delete':'highlight-delete',id:item.id}:{action:'item-delete',kind:view==='history'?'history':'bookmark',video:item.id};await post(data);await load(true);await profile();
    }));return card;
  }
  async function load(reset=false){
    if(reset){generation++;offset=0;grid.replaceChildren();busy=false;controls();}
    if(busy)return;busy=true;const version=generation;more.disabled=true;
    try{const data=await api({view,offset,playlist,mine:mypage?'1':'0'});if(version!==generation)return;data.items.forEach(item=>grid.append(card(item)));offset=data.nextOffset;more.hidden=!data.hasMore;status.textContent=grid.children.length?'':'아직 등록된 항목이 없습니다.';}
    catch(error){if(version===generation){status.textContent=error.message;more.hidden=true;}}
    finally{if(version===generation){busy=false;more.disabled=false;}}
  }
  root.querySelectorAll('[data-view]').forEach(tab=>tab.onclick=()=>{view=tab.dataset.view;playlist='';root.querySelectorAll('[data-view]').forEach(t=>t.setAttribute('aria-pressed',String(t===tab)));load(true);});
  root.querySelector('[data-view="history"]')?.setAttribute('aria-pressed','true');
  more.onclick=()=>load();profile().catch(error=>status.textContent=error.message);
  if(bootstrap){controls();grid.replaceChildren(...bootstrap.items.map(card));offset=bootstrap.nextOffset;more.hidden=!bootstrap.hasMore;status.textContent=bootstrap.error||(grid.children.length?'':'아직 등록된 항목이 없습니다.');}else load(true);
};
window.initializeLibrary();
