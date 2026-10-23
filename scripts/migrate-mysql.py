"""Copy this machine's SQLite snapshot, verify every row, then activate MySQL."""
import datetime
import hashlib
import os
from pathlib import Path
import sqlite3
import mysql_backend

ROOT = mysql_backend.ROOT


def fingerprint(rows):
    hashes = []
    for row in rows:
        digest = hashlib.sha256()
        for value in row:
            # SQLite REAL values and MySQL DOUBLE values must compare identically.
            if isinstance(value, (int, float)):
                value = float(value)
            encoded = value if isinstance(value, bytes) else repr(value).encode('utf-8')
            digest.update(len(encoded).to_bytes(8, 'big'))
            digest.update(encoded)
        hashes.append(digest.digest())
    return hashlib.sha256(b''.join(sorted(hashes))).hexdigest()


def migrate():
    address=mysql_backend.configuration().get('LISTEN_ADDR')
    if address != '127.0.0.1:8081' and not (address == '127.0.0.1:80' and (ROOT/'runtime/production-server').is_file()):
        raise RuntimeError('Run the migration on the Windows VM or the explicitly marked production server.')
    if mysql_backend.MARKER.exists():
        raise RuntimeError('MySQL is already active; refusing to overwrite live data.')
    directory = ROOT / 'data' / 'backups'
    directory.mkdir(parents=True, exist_ok=True)
    backup = directory / ('before-mysql-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + '.sqlite3')
    source = sqlite3.connect(ROOT / 'DB' / 'pastellive.sqlite3')
    snapshot = sqlite3.connect(backup)
    source.backup(snapshot)
    source.close()
    tables = snapshot.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    target = mysql_backend.Connection()
    try:
        with target:
            existing = [row[0] for row in target.execute('SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE()')]
            expected = {name for name, _ in tables}
            if any(name not in expected or target.execute(f'SELECT COUNT(*) FROM `{name}`').fetchone()[0] for name in existing):
                raise RuntimeError('Target database contains data. Existing MySQL tables were preserved.')
            for name, sql in tables:
                if not name.replace('_', '').isalnum():
                    raise RuntimeError('Invalid table name.')
                target.execute(sql.replace('CREATE TABLE ', 'CREATE TABLE IF NOT EXISTS ', 1))
            for sql, in snapshot.execute("SELECT sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"):
                target.executescript(sql)
            for name, _ in tables:
                columns = [row[1] for row in snapshot.execute(f'PRAGMA table_info("{name}")')]
                names = ','.join('`' + column + '`' for column in columns)
                rows = snapshot.execute(f'SELECT {names} FROM "{name}"').fetchall()
                query = f'INSERT INTO `{name}` ({names}) VALUES(' + ','.join('?' for _ in columns) + ')'
                for offset in range(0, len(rows), 250):
                    target.executemany(query, rows[offset:offset + 250])
                copied = [tuple(row.values()) for row in target.execute(f'SELECT {names} FROM `{name}`')]
                if len(rows) != len(copied) or fingerprint(rows) != fingerprint(copied):
                    raise RuntimeError('Verification failed for table ' + name)
                print(name + ': ' + str(len(rows)) + ' rows verified', flush=True)
        mysql_backend.DATA.mkdir(exist_ok=True)
        temporary = mysql_backend.MARKER.with_suffix('.tmp')
        temporary.write_text('mysql\n', encoding='ascii')
        os.replace(temporary, mysql_backend.MARKER)
        print('MySQL activated. Original SQLite and data/backups snapshot preserved.', flush=True)
    finally:
        snapshot.close()
        target.close()


if __name__ == '__main__':
    migrate()
