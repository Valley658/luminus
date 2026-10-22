(() => {
  const button=document.querySelector('.account-button');
  const menu=document.querySelector('.account-menu');
  const auth=document.querySelector('.auth-dialog');
  const profile=document.querySelector('.profile-dialog');
  const settings=document.querySelector('.settings-dialog');
  const form=document.querySelector('.auth-form');
  function notify(icon,title,text){return Swal.fire({icon,titleText:title,text,confirmButtonText:'확인',background:'#202020',color:'#eee',confirmButtonColor:'#a88bdf',target:document.querySelector('dialog[open]')||document.body,heightAuto:false});}
  const error=document.querySelector('.auth-error');
  const cacheKey='luminous-account-display-v1';
  let user=null,configured=false,mode='login',requestVersion=0;
  try{user=JSON.parse(localStorage.getItem(cacheKey)||'null');}catch(_){}
  function avatar(node,large=false) {
    node.replaceChildren();
    if(user?.avatar) { const image=document.createElement('img');image.src=user.avatar;image.alt='';node.append(image); }
    else node.textContent=user ? Array.from(user.name)[0] : '나';
  }
  function showAuth() {
    me().catch(()=>{error.textContent='서버 연결을 확인해 주세요.';});
    if(!auth.open) auth.showModal();
    form.elements.handle.focus();
  }
  function paint() {
    const image=button.querySelector('img'),initial=button.querySelector('span');
    image.hidden=!user?.avatar;initial.hidden=!!user?.avatar;
    if(user?.avatar) image.src=user.avatar; else image.removeAttribute('src');
    initial.textContent=user ? Array.from(user.name)[0] : '로그인';
    button.classList.toggle('is-guest',!user);
    button.setAttribute('aria-label',user ? user.name+' 프로필' : '로그인 및 회원가입');
    const adminLink=document.querySelector('.admin-link');if(adminLink)adminLink.hidden=!user?.isAdmin;
    document.querySelector('.account-tooltip').textContent=user ? user.name : '로그인';
    if(user) {
      document.querySelector('.account-name').textContent=user.name;
      document.querySelector('.account-handle').textContent=user.handle;
      document.querySelector('.member-profile-name').textContent=user.name+(user.isAdmin?' · 관리자':'');
      document.querySelectorAll('.profile-name-form').forEach(form=>{form.elements.name.value=user.name;form.elements.name.disabled=!!user.isAdmin;form.querySelector('button').disabled=!!user.isAdmin;form.querySelector('.profile-name-status').textContent=user.isAdmin?'관리자 이름은 변경할 수 없습니다. 사진은 변경할 수 있습니다.':'';});
      document.querySelector('.member-profile-handle').textContent=user.handle;
      avatar(document.querySelector('.menu-avatar'));document.querySelectorAll('.member-profile-avatar').forEach(node=>avatar(node));
    }
    document.querySelectorAll('.discord-button').forEach(node => {
      node.disabled=!configured || !!(user?.discordLinked && node.closest('.profile-dialog,.settings-dialog'));
      if(node.closest('.profile-dialog,.settings-dialog')) node.textContent=user?.discordLinked ? '디스코드 연동됨' : '디스코드 연동하기';
    });
    const discord=user?.discordAccount;
    document.querySelector('.discord-link-status').textContent=user?.discordLinked ? '연동 계정: '+(discord?.name||discord?.username||'Discord')+(discord?.username?' (@'+discord.username+')':discord?.id?' · ID '+discord.id:'') : '디스코드 계정이 연동되어 있지 않습니다.';
    document.querySelectorAll('.discord-hint').forEach(node=>node.hidden=configured);
  }
  async function me() {
    const version=++requestVersion;
    const response=await fetch('/api/auth/me',{credentials:'same-origin',cache:'no-store'});if(!response.ok) throw new Error();
    const data=await response.json();if(version!==requestVersion)return;user=data.user;configured=data.discordConfigured;
    try{if(user)localStorage.setItem(cacheKey,JSON.stringify(user));else localStorage.removeItem(cacheKey);}catch(_){}
    paint();
  }
  button.addEventListener('click',()=> {
    if(!user) {setMode('login');return showAuth();}
    menu.hidden=!menu.hidden;button.setAttribute('aria-expanded',String(!menu.hidden));
  });
  document.addEventListener('click',event=>{if(!event.target.closest('.account')) {menu.hidden=true;button.setAttribute('aria-expanded','false');}});
  document.addEventListener('keydown',event=>{if(event.key==='Escape') menu.hidden=true;});
  document.querySelector('.account-settings').addEventListener('click',async()=>{menu.hidden=true;button.setAttribute('aria-expanded','false');try{await me();if(!user){showAuth();return;}settings.showModal();}catch(_){notify('error','설정을 열지 못했습니다.','서버 연결을 확인해 주세요.');}});
  document.querySelector('.settings-close').addEventListener('click',()=>settings.close());
  document.querySelector('.view-profile').addEventListener('click',()=>{menu.hidden=true;profile.showModal();});
  document.querySelectorAll('.profile-name-form').forEach(node=>node.addEventListener('submit',async event=>{
    event.preventDefault();const form=event.currentTarget,status=form.querySelector('.profile-name-status'),submit=form.querySelector('button');submit.disabled=true;status.textContent='';
    try{const response=await fetch('/api/auth/profile',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:form.elements.name.value})});const data=await response.json();if(!response.ok)throw Error(data.error||'이름을 변경하지 못했습니다.');await me();status.textContent='이름을 변경했습니다.';}catch(e){status.textContent=e.message;}finally{submit.disabled=!!user?.isAdmin;}
  }));
  document.querySelector('.profile-close').addEventListener('click',()=>profile.close());
  document.querySelectorAll('.avatar-camera').forEach(button=>button.addEventListener('click',()=>button.closest('.avatar-editor').querySelector('.avatar-file').click()));
  document.querySelectorAll('.avatar-file').forEach(node=>node.addEventListener('change',async event=>{
    const file=event.target.files[0];const status=event.target.closest('dialog').querySelector('.avatar-status');if(!file)return;
    try {
      if(file.size>5*1024*1024)throw new Error('5MB 이하 사진을 선택해 주세요.');
      let image;
      if(file.type==='image/gif'||/\.gif$/i.test(file.name)){image=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);});}else{
      const bitmap=await createImageBitmap(file);const canvas=document.createElement('canvas');canvas.width=256;canvas.height=256;
      const size=Math.min(bitmap.width,bitmap.height);canvas.getContext('2d').drawImage(bitmap,(bitmap.width-size)/2,(bitmap.height-size)/2,size,size,0,0,256,256);bitmap.close();
      image=canvas.toDataURL('image/jpeg',.85).split(',')[1];}
      const response=await fetch('/api/auth/avatar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({image})});
      if(!response.ok){const data=await response.json();throw new Error(data.error||'사진을 저장하지 못했습니다.');}await me();status.textContent='사진을 변경했습니다.';
    }catch(e){status.textContent=e.message;}finally{event.target.value='';}
  }));
  function setMode(next) {
    mode=next;form.reset();error.textContent='';
    const signup=mode==='signup',name=form.elements.name;
    name.closest('label').hidden=!signup;name.required=signup;
    document.querySelector('#auth-title').textContent=signup?'회원가입':'로그인';
    form.elements.password.autocomplete=signup?'new-password':'current-password';
    form.querySelector('.auth-submit').textContent=signup?'회원가입':'로그인';
    document.querySelector('.auth-switch span').textContent=signup?'이미 계정이 있나요?':'계정이 없나요?';
    document.querySelector('.auth-switch button').textContent=signup?'로그인':'회원가입';
  }
  document.querySelector('.auth-switch button').addEventListener('click',()=>setMode(mode==='login'?'signup':'login'));
  document.querySelector('.dialog-close').addEventListener('click',()=>auth.close());
  form.addEventListener('submit',async event=>{
    event.preventDefault();error.textContent='';const submit=form.querySelector('.auth-submit');submit.disabled=true;
    try {
      const response=await fetch('/api/auth/'+mode,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(new FormData(form)))});
      const data=await response.json();if(!response.ok) throw new Error(data.error||'다시 시도해 주세요.');
      await me();auth.close();form.reset();
      await notify('success',mode==='signup'?'회원가입 완료':'로그인 완료',user.name+'님, 환영합니다.');
      if(new URLSearchParams(location.search).get('next')==='admin'){if(user.isAdmin)location.assign('https://admin.pastellive.co.kr/');else notify('error','접근 권한 없음','관리자만 접근할 수 있습니다.');}
    } catch(e) {await notify('error',mode==='signup'?'회원가입 실패':'로그인 실패',e.message);} finally {submit.disabled=false;}
  });
  document.querySelectorAll('.profile-logout,.menu-logout').forEach(node=>node.addEventListener('click',async()=>{
    const response=await fetch('/api/auth/logout',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    if(response.ok){if(profile.open)profile.close();if(settings.open)settings.close();menu.hidden=true;button.setAttribute('aria-expanded','false');await me();}
  }));
  document.querySelectorAll('.discord-button').forEach(node=>node.addEventListener('click',()=>{if(configured)location.assign('/auth/discord/start');else notify('info','디스코드 연동 준비 중','이 서버의 Discord 설정이 아직 적용되지 않았습니다.');}));
  const notice=document.querySelector('.fan-notice');
  const passwordDialog=document.querySelector('.password-dialog'),passwordForm=document.querySelector('.password-form');
  function offerPassword() {
    if(user?.needsPassword && !notice.open && !passwordDialog.open) {
      passwordForm.elements.handle.value=user.handle.slice(1).length<=24?user.handle.slice(1):'user_'+user.handle.slice(-12);
      passwordDialog.showModal();
    }
  }
  passwordForm.addEventListener('submit',async event=>{
    event.preventDefault();const message=document.querySelector('.password-error'),submit=passwordForm.querySelector('button');
    message.textContent='';submit.disabled=true;
    try {
      const response=await fetch('/api/auth/password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(new FormData(passwordForm)))});
      const data=await response.json();if(!response.ok)throw new Error(data.error||'다시 시도해 주세요.');
      await me();passwordDialog.close();passwordForm.reset();
    }catch(e){message.textContent=e.message;}finally{submit.disabled=false;}
  });
  notice.addEventListener('close',()=>me().then(offerPassword).catch(()=>{}));
  paint();
  me().then(()=>{
    offerPassword();
    const loginParams=new URLSearchParams(location.search);
    if(loginParams.get('login')==='1'){if(user?.isAdmin&&loginParams.get('next')==='admin'){location.replace('https://admin.pastellive.co.kr/');return;}if(!user){if(notice.open)notice.addEventListener('close',showAuth,{once:true});else showAuth();}}
    const code=new URLSearchParams(location.search).get('auth_error');
    if(code) {
      error.textContent=code==='discord_setup'?'디스코드 앱 설정이 아직 완료되지 않았습니다.':'디스코드 인증을 완료하지 못했습니다. 다시 시도해 주세요.';
      if(!notice.open)showAuth();else notice.addEventListener('close',showAuth,{once:true});
      history.replaceState(null,'','/index2.html');
    }
  }).catch(()=>{error.textContent='서버 연결을 확인해 주세요.';});
})();
