"""Prepare the installed VM MySQL server without modifying existing tables."""
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parent.parent


def setup():
    values = {}
    for line in (ROOT / '.env').read_text(encoding='utf-8-sig').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip().strip('"\'')
    address=values.get('LISTEN_ADDR')
    if address != '127.0.0.1:8081' and not (address == '127.0.0.1:80' and (ROOT/'runtime/production-server').is_file()):
        raise RuntimeError('Run this setup on the Windows VM or explicitly marked production server.')
    name = values.get('DB_NAME', '')
    if not re.fullmatch(r'[A-Za-z0-9_]+', name):
        raise RuntimeError('Invalid database name.')
    executable = Path('C:/Program Files/MySQL/MySQL Server 8.0/bin/mysql.exe')
    if not executable.is_file():
        raise RuntimeError('Installed MySQL 8.0 client not found.')
    env = os.environ.copy()
    env['MYSQL_PWD'] = values['DB_PASSWORD']
    args = [str(executable), '--protocol=TCP', '--host=127.0.0.1',
            '--port=' + values.get('DB_PORT', '3306'),
            '--user=' + values['DB_USER'], '--batch', '--skip-column-names']
    sql = (f'CREATE DATABASE IF NOT EXISTS `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;'
           'SELECT VERSION();'
           f"SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='{name}';")
    result = subprocess.run(args, input=sql.encode(), env=env, capture_output=True, timeout=20)
    if result.returncode:
        raise RuntimeError('MySQL connection or database creation failed. Check the VM credentials and service.')
    print('MySQL connection verified; database exists; existing tables preserved.')
    config = Path('C:/ProgramData/MySQL/MySQL Server 8.0/my.ini')
    text = config.read_text(encoding='utf-8-sig')
    section = re.search(r'(?ims)^\[mysqld\]\s*\n(.*?)(?=^\[|\Z)', text)
    if not section:
        raise RuntimeError('MySQL server configuration section not found.')
    body = section.group(1)
    for key in ('bind-address', 'mysqlx-bind-address'):
        body = re.sub(r'(?im)^\s*' + re.escape(key) + r'\s*=.*\n?', '', body)
    body = 'bind-address=127.0.0.1\nmysqlx-bind-address=127.0.0.1\n' + body
    updated = text[:section.start(1)] + body + text[section.end(1):]
    if updated != text:
        backup = config.with_name('my.ini.before-luminus')
        if not backup.exists():
            backup.write_bytes(config.read_bytes())
        config.write_text(updated, encoding='utf-8')
        restart = subprocess.run(['powershell.exe', '-NoProfile', '-Command',
                                  'Restart-Service -Name MySQL80 -ErrorAction Stop'],
                                 capture_output=True, timeout=60)
        if restart.returncode:
            raise RuntimeError('Loopback configuration saved; MySQL80 restart requires administrator privileges.')
    result = subprocess.run(args, input=b'SELECT 1;', env=env, capture_output=True, timeout=20)
    if result.returncode:
        raise RuntimeError('MySQL verification after configuration failed.')
    print('MySQL configured for loopback access only. Application data migration has not been performed.')


if __name__ == '__main__':
    setup()
