"""VM-only Nginx process lifecycle. No download or installation on the physical PC."""
from pathlib import Path
import subprocess
import socket
import urllib.request
ROOT=Path(__file__).resolve().parent.parent
# Nginx for Windows requires an ASCII prefix even when the workspace name is Korean.
HOME=ROOT.parent.parent/'luminous-site/bin/nginx'
MARKER=ROOT/'runtime/nginx-enabled'


def enabled():return MARKER.exists()


def command(*args):
    return subprocess.run([str(HOME/'nginx.exe'),'-p',str(HOME)+'/',*args],cwd=HOME,capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW,timeout=15)


def ensure():
    if not enabled():return
    target=HOME/'conf/nginx.conf';source=(ROOT/'scripts/nginx.conf').read_bytes()
    changed=not target.exists() or target.read_bytes()!=source
    if changed:target.write_bytes(source)
    try:
        with urllib.request.urlopen('http://127.0.0.1/__nginx_health',timeout=1) as r:online=r.status==204
    except Exception:online=False
    if online and not changed:return
    check=command('-t')
    if check.returncode:raise RuntimeError('Nginx configuration validation failed')
    if online:
        if command('-s','reload').returncode:raise RuntimeError('Nginx reload failed')
    else:
        try:
            with socket.create_connection(('127.0.0.1',80),timeout=.5):pass
        except OSError:pass
        else:raise RuntimeError('Port 80 is occupied by another server; refusing duplicate Nginx processes')
        subprocess.Popen([str(HOME/'nginx.exe'),'-p',str(HOME)+'/'],cwd=HOME,creationflags=subprocess.CREATE_NO_WINDOW,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


def close():
    if enabled():
        try:command('-s','quit')
        except (OSError,subprocess.SubprocessError):pass
