"""Relocate installed VM MySQL data into project data/mysql, retaining the original."""
from pathlib import Path
import re
import shutil
import subprocess
import time
import mysql_backend


def powershell(command):
    result = subprocess.run(['powershell.exe', '-NoProfile', '-Command',
                             '$ErrorActionPreference="Stop"; ' + command], capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError('MySQL service operation failed; original configuration will be restored.')


def relocate():
    address=mysql_backend.configuration().get('LISTEN_ADDR')
    if address != '127.0.0.1:8081' and not (address == '127.0.0.1:80' and (mysql_backend.ROOT/'runtime/production-server').is_file()):
        raise RuntimeError('VM or explicitly marked production server only.')
    config = Path('C:/ProgramData/MySQL/MySQL Server 8.0/my.ini')
    original = config.read_bytes()
    text = original.decode('utf-8-sig')
    match = re.search(r'(?im)^datadir\s*=\s*"?([^"\r\n]+)', text)
    if not match:
        raise RuntimeError('Installed MySQL data directory not found.')
    source = Path(match[1].strip())
    target = mysql_backend.ROOT / 'data/mysql'
    alias = Path('C:/Users/user/luminous-site')
    if not alias.exists() or alias.resolve() != mysql_backend.ROOT.resolve():
        raise RuntimeError('Existing ASCII project junction is required.')
    if source.resolve() == target.resolve():
        print('MySQL already uses data/mysql.')
        return
    if target.exists():
        saved = target.with_name('mysql-initial-attempt-' + str(int(time.time())))
        if target.resolve().parent != (mysql_backend.ROOT / 'data').resolve() or saved.parent.resolve() != target.parent.resolve():
            raise RuntimeError('Unsafe data directory path.')
        target.rename(saved)
    backup = config.with_name('my.ini.before-data-directory')
    if not backup.exists():
        backup.write_bytes(original)
    powershell('Stop-Service -Name MySQL80')
    try:
        shutil.copytree(source, target)
        permissions = subprocess.run(['icacls.exe', str(target), '/grant',
                                      'NT SERVICE\\MySQL80:(OI)(CI)F', '*S-1-5-20:(OI)(CI)F', '/T', '/Q'],
                                     capture_output=True, timeout=60)
        if permissions.returncode:
            raise RuntimeError('Could not grant MySQL access to its new data directory.')
        updated = re.sub(r'(?im)^datadir\s*=.*$', 'datadir=C:/Users/user/luminous-site/data/mysql', text)
        config.write_text(updated, encoding='utf-8')
        powershell('Start-Service -Name MySQL80')
        check = mysql_backend.Connection()
        try:
            actual = Path(check.execute('SELECT @@datadir').fetchone()[0])
            if actual.resolve() != target.resolve():
                raise RuntimeError('MySQL data directory verification failed.')
        finally:
            check.close()
    except Exception:
        config.write_bytes(original)
        powershell('Start-Service -Name MySQL80')
        raise
    print('MySQL now stores its files in data/mysql; original directory retained.')


if __name__ == '__main__':
    relocate()
