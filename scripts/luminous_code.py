"""Read-only source viewer; uses Git's public tracked file allowlist."""
import json
import re
import subprocess
import accounts
import luminous_port as p

EXCLUDED={'AGENTS.md','scripts/sync-vm.py','scripts/install-nginx.py','scripts/setup-sync.ps1','scripts/start-sync.ps1'}


def git(*args):
    try:return subprocess.check_output(['git',*args],cwd=p.ROOT,timeout=10,stderr=subprocess.DEVNULL)
    except (OSError,subprocess.SubprocessError):raise p.RequestError('소스 이력을 확인할 수 없습니다.',503) from None


def files():
    return [name for name in git('ls-files','-z').decode().split('\0') if name and name not in EXCLUDED and not name.startswith(('.','data/','DB/','runtime/','logs/','bin/','reference/'))]


def read(name):
    if name not in files():raise p.RequestError('공개 소스 파일이 아닙니다.',404)
    f=(p.ROOT/name).resolve()
    if not f.is_relative_to(p.ROOT.resolve()) or not f.is_file() or f.stat().st_size>1024*1024:raise p.RequestError('표시할 수 없는 파일입니다.',404)
    try:body=f.read_text(encoding='utf-8-sig')
    except UnicodeError:raise p.RequestError('텍스트 파일만 볼 수 있습니다.')
    c=accounts.config()
    for key,value in c.items():
        if len(value)>=8 and any(k in key.upper() for k in ('SECRET','PASSWORD','API_KEY','TOKEN','EMAIL')) and value in body:raise p.RequestError('운영 설정이 포함된 파일은 공개하지 않습니다.',403)
    if re.search(r'112\.168\.22\.237|121\.135\.|AIza[\w-]{25,}|BEGIN (?:OPENSSH |RSA )?PRIVATE KEY',body):raise p.RequestError('운영 파일은 공개하지 않습니다.',403)
    return body


def api(h,path,user,data,q):
    if path.endswith('/github'):p.reply(h,url='https://github.com/Valley658/luminus',github_url='https://github.com/Valley658/luminus');return
    if path.endswith('/tree'):
        names=files();p.reply(h,files=names,tree=[dict(path=n,name=n.rsplit('/',1)[-1],type='file') for n in names]);return
    if path.endswith(('/file','/raw')):
        name=q.get('path','');body=read(name)
        if path.endswith('/raw'):p.send_bytes(h,body.encode(),'text/plain')
        else:p.reply(h,path=name,content=body,language=name.rsplit('.',1)[-1],lines=body.splitlines())
        return
    if path.endswith('/search'):
        query=q.get('q','')[:100];matches=[]
        if query:
            for name in files():
                try:body=read(name)
                except p.RequestError:continue
                for i,line in enumerate(body.splitlines(),1):
                    if query.lower() in line.lower():matches.append(dict(path=name,line=i,text=line[:500]))
                    if len(matches)>=100:break
                if len(matches)>=100:break
        p.reply(h,results=matches);return
    if path.endswith('/commit'):
        sha=q.get('sha',q.get('commit',''))
        if not re.fullmatch(r'[a-fA-F0-9]{7,40}',sha):raise p.RequestError('커밋을 확인해 주세요.')
        # Metadata only: do not expose deleted historical file contents.
        value=git('show','--no-patch','--format=%H%n%an%n%aI%n%s',sha).decode(errors='replace').splitlines()
        p.reply(h,commit=dict(sha=value[0],author=value[1],date=value[2],message='\n'.join(value[3:])));return
    if path.endswith(('/history','/commits','/blame')):
        name=q.get('path','');args=['log','-30','--format=%H%x09%an%x09%aI%x09%s']
        if name:
            read(name);args+=['--',name]
        rows=git(*args).decode(errors='replace').splitlines();values=[]
        for row in rows:
            parts=row.split('\t',3)
            if len(parts)==4:values.append(dict(zip(('sha','author','date','message'),parts)))
        p.reply(h,commits=values,history=values);return
    raise p.RequestError('없는 소스 요청입니다.',404)
