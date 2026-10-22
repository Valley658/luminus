(() => {
  const root=document.querySelector('.watch-comments');if(!root)return;
  const list=root.querySelector('.comment-list'),status=root.querySelector('.comment-status'),more=root.querySelector('.comment-more'),form=root.querySelector('form');
  let source='site',cursor='',generation=0,poll=null,busy=false;
  const message=text=>window.Swal?Swal.fire({text,background:'#222',color:'#eee',confirmButtonColor:'#bda4eb'}):status.textContent=text;
  function element(tag,text,className){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;}
  async function request(url,options){const response=await fetch(url,options),data=await response.json();if(!response.ok)throw new Error(data.error||'요청에 실패했습니다.');return data;}
  async function mutate(path,data){return request(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});}
  function card(item){
    const article=element('article',undefined,'comment-item'),header=element('div',undefined,'comment-header');
    if(item.avatar&&(/^(https:\/\/|\/api\/comments\/avatar\?)/).test(item.avatar)){const img=element('img');img.src=item.avatar;img.alt='';img.loading='lazy';img.referrerPolicy='no-referrer';header.append(img);}
    header.append(element('strong',item.author));if(item.isAdmin)header.append(element('span','관리자','comment-admin'));
    const date=new Date(typeof item.created==='number'?item.created*1000:item.created);if(!isNaN(date))header.append(element('time',date.toLocaleDateString('ko-KR')));
    if(item.canDelete){const button=element('button','삭제');button.type='button';button.onclick=async()=>{button.disabled=true;try{await mutate('/api/comments/delete',{id:item.id});await load(true);}catch(error){message(error.message);button.disabled=false;}};header.append(button);}
    article.append(header,element('p',item.body,'comment-body'));if(source==='youtube'&&item.likes)article.append(element('small','좋아요 '+Number(item.likes).toLocaleString()));return article;
  }
  async function load(reset=false){
    if(reset){generation++;cursor='';clearTimeout(poll);busy=false;list.replaceChildren();}
    if(busy)return;busy=true;const version=generation,kind=source;more.disabled=true;
    try{
      const data=await request('/api/comments?'+new URLSearchParams({video:root.dataset.video,source:kind,cursor}));if(version!==generation)return;
      for(const item of data.items||[])list.append(card(item));cursor=data.nextCursor||'';more.hidden=!cursor;
      status.textContent=data.message||(list.children.length?'':kind==='site'?'첫 댓글을 남겨 보세요.':'표시할 YouTube 댓글이 없습니다.');
      if(kind==='site'&&data.total)status.textContent='댓글 '+data.total+'개';
      if(data.refreshing)poll=setTimeout(()=>load(),2000);
    }catch(error){if(version===generation)status.textContent=error.message;}
    finally{if(version===generation){busy=false;more.disabled=false;}}
  }
  root.querySelectorAll('[data-source]').forEach(button=>button.onclick=()=>{source=button.dataset.source;root.querySelectorAll('[data-source]').forEach(tab=>tab.setAttribute('aria-pressed',String(tab===button)));form.hidden=source!=='site';load(true);});
  form.onsubmit=async event=>{event.preventDefault();const button=form.querySelector('button');button.disabled=true;try{await mutate('/api/comments',{video:root.dataset.video,body:form.querySelector('textarea').value});form.reset();await load(true);}catch(error){message(error.message);}finally{button.disabled=false;}};
  more.onclick=()=>load();window.addEventListener('pagehide',()=>clearTimeout(poll));load(true);
})();
