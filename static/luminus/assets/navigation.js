(() => {
 const panel=document.querySelector('#library-panel');if(!panel)return;
 const titles={history:'시청 기록',bookmarks:'북마크',playlists:'재생목록',gallery:'팬 갤러리',schedule:'스케줄',highlights:'하이라이트'},cache=new Map(),pending=new Map();
 let epoch=0,selection=0,owner=window.luminousAccount?.handle||'',saved=null;
 try{const initial=JSON.parse(document.querySelector('#navigation-bootstrap')?.textContent||'null');if(initial){owner=initial.owner;for(const [key,value] of Object.entries(initial.views))cache.set(key,value);}}catch(_){}
 async function prepare(view){
  if(cache.has(view))return cache.get(view);if(pending.has(view))return pending.get(view);
  const current=epoch;const job=(async()=>{let data;
   if(view==='schedule'){const response=await fetch('/schedule',{cache:'no-store'});if(!response.ok)throw Error('스케줄을 가져오지 못했습니다.');const page=new DOMParser().parseFromString(await response.text(),'text/html');data={html:page.querySelector('section.schedule-page')?.outerHTML||''};}
   else{const response=await fetch('/api/library?'+new URLSearchParams({view}),{cache:'no-store'});data=await response.json();if(!response.ok)data={items:[],hasMore:false,nextOffset:0,error:data.error};}
   if(current===epoch)cache.set(view,data);return data;
  })();pending.set(view,job);try{return await job;}finally{if(current===epoch)pending.delete(view);}
 }
 function warm(){for(const view of ['history','bookmarks','playlists','gallery','schedule'])prepare(view).catch(()=>{});}
 function hideHome(){if(saved)return;saved={view:document.body.dataset.view||'',children:Array.from(document.querySelector('main').children).filter(child=>child!==panel).map(child=>[child,child.hidden])};for(const [child] of saved.children)child.hidden=true;}
 function restore(){if(!saved)return;for(const [child,hidden] of saved.children)child.hidden=hidden;if(saved.view)document.body.dataset.view=saved.view;else delete document.body.dataset.view;saved=null;panel.hidden=true;panel.replaceChildren();selection++;}
 async function show(view){
  window.dispatchEvent(new Event('luminous-preview-start'));hideHome();document.body.dataset.view=view;panel.hidden=false;panel.replaceChildren();const current=++selection;
  const root=document.createElement('section');root.className='library-page';const heading=document.createElement('h1');heading.textContent=titles[view];root.append(heading);panel.append(root);document.querySelector('main').scrollTop=0;
  try{const data=cache.get(view)||await prepare(view);if(current!==selection)return;
   if(view==='schedule'){panel.innerHTML=data.html;return;}
   root.dataset.library=view;
   const tools=document.createElement('div');tools.className='library-tools';const status=document.createElement('p');status.className='library-status';status.setAttribute('role','status');
   const grid=document.createElement('div');grid.className='library-grid';const more=document.createElement('button');more.className='library-more';more.type='button';more.hidden=true;more.textContent='더 보기';
   const initial=document.createElement('script');initial.type='application/json';initial.className='library-bootstrap';initial.textContent=JSON.stringify(data);
   root.append(tools,status,grid,more,initial);window.initializeLibrary?.(panel);
  }catch(error){if(current===selection){const message=document.createElement('p');message.textContent=error.message;root.append(message);}}
 }
 document.querySelector('.sidebar-links')?.addEventListener('pointerover',event=>{const link=event.target.closest('a');if(link){const view=new URL(link.href).pathname.slice(1);if(titles[view])prepare(view).catch(()=>{});}});
 document.querySelector('.sidebar-links')?.addEventListener('click',event=>{const link=event.target.closest('a');if(!link||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey||event.button!==0)return;const view=new URL(link.href).pathname.slice(1);if(!titles[view])return;event.preventDefault();show(view);});
 document.querySelector('.profiles')?.addEventListener('click',()=>restore(),true);
 window.addEventListener('luminous-account-change',event=>{const next=event.detail.handle;if(next!==owner){owner=next;epoch++;cache.clear();pending.clear();if(saved)restore();warm();}});
 window.addEventListener('luminous-library-changed',()=>{epoch++;cache.clear();pending.clear();warm();});
 if('requestIdleCallback' in window)requestIdleCallback(warm,{timeout:1500});else setTimeout(warm,500);
})();
