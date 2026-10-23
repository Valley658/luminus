"""SMTP email verification and expiring, single-use password reset tokens."""
import hashlib
import json
import re
import secrets
import smtplib
import time
from email.message import EmailMessage
import accounts
import database
import luminous_port as p


def ready():
    c=accounts.config()
    return bool(c.get('SMTP_HOST') and (c.get('SMTP_FROM') or c.get('SMTP_USER')))


def send(address,subject,body):
    c=accounts.config()
    if not ready():raise p.RequestError('메일 서버가 구성되지 않았습니다.',503)
    message=EmailMessage();message['From']=c.get('SMTP_FROM') or c['SMTP_USER'];message['To']=address;message['Subject']=subject;message.set_content(body)
    port=int(c.get('SMTP_PORT','587'));smtp=smtplib.SMTP_SSL if port==465 else smtplib.SMTP
    try:
        with smtp(c['SMTP_HOST'],port,timeout=10) as client:
            if port!=465:client.starttls()
            if c.get('SMTP_USER'):client.login(c['SMTP_USER'],c.get('SMTP_PASS',c.get('SMTP_PASSWORD','')))
            client.send_message(message)
    except (OSError,smtplib.SMTPException):raise p.RequestError('메일을 보내지 못했습니다. 잠시 후 다시 시도해 주세요.',503) from None


def token(kind,uid,email):
    raw=secrets.token_urlsafe(32);rid=hashlib.sha256(raw.encode()).hexdigest()
    p.record(kind,uid,data=dict(email=email,expires=time.time()+1800),rid=rid)
    return raw


def update_email(h,user,email):
    uid=p.require(user)
    with database.connect() as db:db.execute('INSERT OR REPLACE INTO luminous_settings VALUES(?,?,0)',(uid,email))
    if not ready():return p.reply(h,message='이메일이 저장되었습니다. 메일 서버 설정 후 인증할 수 있습니다.',email_verified=False)
    raw=token('email-verification',uid,email)
    send(email,'루미너스 이메일 인증',accounts.public_origin()+'/account/verify-email?token='+raw+'\n\n30분 안에 인증해 주세요.')
    return p.reply(h,message='인증 메일을 보냈습니다.',email_verified=False)


def verify_email(h,raw):
    rid=hashlib.sha256(raw.encode()).hexdigest()
    with p.mutex,database.connect() as db:
        r=db.execute("SELECT * FROM luminous_records WHERE id=? AND kind='email-verification'",(rid,)).fetchone()
        if not r:raise p.RequestError('인증 링크가 만료되었거나 사용되었습니다.',400)
        d=json.loads(r['payload'])
        if d['expires']<time.time():raise p.RequestError('인증 링크가 만료되었습니다.')
        duplicate=db.execute('SELECT 1 FROM luminous_settings WHERE email=? AND email_verified=1 AND user_id<>?',(d['email'],r['owner'])).fetchone()
        if duplicate:raise p.RequestError('다른 계정에서 인증한 이메일입니다.',409)
        db.execute('UPDATE luminous_settings SET email_verified=1 WHERE user_id=? AND email=?',(r['owner'],d['email']))
        db.execute('DELETE FROM luminous_records WHERE id=?',(rid,))
    accounts.redirect(h,'/?email_verified=1')


def password_reset(h,path,data):
    if path.endswith('forgot-password'):
        if not ready():raise p.RequestError('메일 서버가 구성되지 않아 비밀번호 재설정을 사용할 수 없습니다.',503)
        email=str(data.get('email','')).strip()
        if not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+',email):raise p.RequestError('이메일을 확인해 주세요.')
        with database.connect() as db:r=db.execute('SELECT user_id FROM luminous_settings WHERE email=? AND email_verified=1',(email,)).fetchone()
        if r:
            raw=token('password-reset',r[0],email)
            send(email,'루미너스 비밀번호 재설정',accounts.public_origin()+'/reset-password?token='+raw+'\n\n30분 안에 사용해 주세요.')
        return p.reply(h,message='인증된 이메일이 등록되어 있으면 재설정 메일이 발송됩니다.')
    password=str(data.get('password',''));raw=str(data.get('token',''))
    if not 10<=len(password)<=128:raise p.RequestError('비밀번호는 10~128자로 입력해 주세요.')
    rid=hashlib.sha256(raw.encode()).hexdigest()
    hashed=accounts.password_hash(password)
    with p.mutex,database.connect() as db:
        r=db.execute("SELECT * FROM luminous_records WHERE id=? AND kind='password-reset'",(rid,)).fetchone()
        if not r or json.loads(r['payload'])['expires']<time.time():raise p.RequestError('링크가 만료되었거나 사용되었습니다.')
        db.execute('UPDATE users SET password=? WHERE id=?',(hashed,r['owner']))
        db.execute('DELETE FROM sessions WHERE user_id=?',(r['owner'],))
        db.execute("DELETE FROM luminous_records WHERE owner=? AND kind='password-reset'",(r['owner'],))
    return p.reply(h,message='비밀번호가 변경되었습니다. 다시 로그인해 주세요.')
