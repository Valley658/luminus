"""Keep the server running and reload valid Python changes automatically."""
import hashlib
import msvcrt
from pathlib import Path
import subprocess
import sys
import time
import nginx_runtime

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / 'runtime'
STATE.mkdir(exist_ok=True)
STOP = STATE / '.supervisor-stop'


def signature():
    files = [p for p in (ROOT / 'scripts').glob('*.py') if p.name.startswith('luminous_') or p.name in ('server.py', 'image_cache.py', 'pages.py', 'comments.py', 'library.py', 'legacy_ui.py', 'channel_details.py', 'admin.py', 'admin_operations.py', 'youtube_shorts.py', 'database.py', 'mysql_backend.py', 'search_engine.py', 'accounts.py', 'channel_catalog.py','music_catalog.py','nginx_runtime.py')]
    for file in files:
        compile(file.read_text(encoding='utf-8-sig'), str(file), 'exec')
    return tuple((p.name, hashlib.sha256(p.read_bytes()).hexdigest()) for p in files)


def main():
    with (STATE / 'supervisor.lock').open('a+b') as lock:
        if lock.tell() == 0:
            lock.write(b'0'); lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return
        STOP.unlink(missing_ok=True)
        child = None
        active = None
        try:
            while not STOP.exists():
                try:
                    nginx_runtime.ensure()
                    current = signature()
                except (SyntaxError, OSError, RuntimeError):
                    time.sleep(2)
                    continue
                if child is None or child.poll() is not None or current != active:
                    if current != active:
                        time.sleep(1)
                        if signature() != current:
                            continue
                    if child is not None and child.poll() is None:
                        child.terminate(); child.wait(timeout=10)
                    (ROOT / 'logs').mkdir(exist_ok=True)
                    with (ROOT / 'logs' / 'web-server.log').open('a', encoding='utf-8') as log:
                        child = subprocess.Popen([sys.executable, '-u', str(ROOT / 'scripts/server.py')], cwd=ROOT,
                            stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
                    active = current
                time.sleep(2)
        finally:
            if child is not None and child.poll() is None:
                child.terminate(); child.wait(timeout=10)
            nginx_runtime.close()


if __name__ == '__main__':
    main()
