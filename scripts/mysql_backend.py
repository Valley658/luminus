"""MySQL storage adapter for the existing site's parameterized queries."""
import os
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data'
MARKER = DATA / 'mysql-enabled'


def configuration():
    values = {}
    for line in (ROOT / '.env').read_text(encoding='utf-8-sig').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip().strip('"\'')
    return values


class Row(dict):
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


class Result:
    def __init__(self, cursor):
        self.rowcount = cursor.rowcount
        self.lastrowid = cursor.lastrowid
        columns = [column[0] for column in cursor.description] if cursor.description else []
        self.rows = [Row(zip(columns, row)) for row in cursor.fetchall()] if columns else []
        self.position = 0
        cursor.close()

    def fetchone(self):
        if self.position >= len(self.rows):
            return None
        row = self.rows[self.position]
        self.position += 1
        return row

    def fetchall(self):
        rows = self.rows[self.position:]
        self.position = len(self.rows)
        return rows

    def __iter__(self):
        return iter(self.fetchall())


def translate(sql):
    sql = re.sub(r'INSERT OR REPLACE', 'REPLACE', sql, flags=re.I)
    sql = re.sub(r'INSERT OR IGNORE', 'INSERT IGNORE', sql, flags=re.I)
    sql = re.sub(r'ON CONFLICT\(ip\) DO UPDATE SET after_sites=1,last_seen=excluded.last_seen',
                 'ON DUPLICATE KEY UPDATE after_sites=1,last_seen=VALUES(last_seen)', sql, flags=re.I)
    if re.match(r'\s*(CREATE|ALTER) ', sql, re.I):
        sql = re.sub(r'\bREAL\b', 'DOUBLE', sql, flags=re.I)
        sql = re.sub(r'\bBLOB\b', 'LONGBLOB', sql, flags=re.I)
        sql = re.sub(r'\bTEXT\b', 'VARCHAR(1024)', sql, flags=re.I)
        indexed = ('id', 'handle', 'discord_id', 'token', 'token_hash', 'ip', 'name', 'path',
                   'channelId', 'kind', 'publishedAt', 'channel_id', 'source_id', 'video_id', 'user_id')
        for column in indexed:
            sql = re.sub(r'\b' + column + r'\s+VARCHAR\(1024\)', column + ' VARCHAR(191)', sql)
        sql = re.sub(r'\btitle\s+VARCHAR\(1024\)', 'title TEXT', sql)
        sql = sql.replace('CREATE INDEX IF NOT EXISTS', 'CREATE INDEX')
        if re.match(r'\s*CREATE TABLE', sql, re.I):
            sql += ' ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin'
    # Replace placeholders only outside SQL string literals.
    parts = re.split(r"('(?:''|[^'])*')", sql)
    return ''.join(part if i % 2 else part.replace('?', '%s') for i, part in enumerate(parts))


class Connection:
    def __init__(self):
        import pymysql
        config = configuration()
        if config.get('DB_HOST') not in ('127.0.0.1', 'localhost'):
            raise RuntimeError('MySQL must remain on loopback.')
        self.raw = pymysql.connect(host=config['DB_HOST'], port=int(config.get('DB_PORT', '3306')),
                                   user=config['DB_USER'], password=config['DB_PASSWORD'],
                                   database=config['DB_NAME'], charset='utf8mb4', autocommit=False,
                                   connect_timeout=10, read_timeout=30, write_timeout=30)

    def execute(self, sql, params=()):
        import pymysql
        pragma = re.fullmatch(r'PRAGMA table_info\((\w+)\)', sql.strip(), re.I)
        if pragma:
            cursor = self.raw.cursor()
            cursor.execute('SELECT ORDINAL_POSITION AS cid,COLUMN_NAME AS name,DATA_TYPE AS type '
                           'FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s '
                           'ORDER BY ORDINAL_POSITION', (pragma[1],))
            return Result(cursor)
        cursor = self.raw.cursor()
        try:
            cursor.execute(translate(sql), params)
            return Result(cursor)
        except pymysql.IntegrityError as error:
            cursor.close()
            raise sqlite3.IntegrityError('Database constraint violation') from error
        except Exception:
            cursor.close()
            raise

    def executemany(self, sql, rows):
        import pymysql
        cursor = self.raw.cursor()
        try:
            cursor.executemany(translate(sql), rows)
            return Result(cursor)
        except pymysql.IntegrityError as error:
            cursor.close()
            raise sqlite3.IntegrityError('Database constraint violation') from error
        except Exception:
            cursor.close()
            raise

    def executescript(self, script):
        for sql in script.split(';'):
            if not sql.strip():
                continue
            try:
                self.execute(sql)
            except Exception as error:
                if not (re.match(r'\s*CREATE INDEX', sql, re.I) and error.args[0] == 1061):
                    raise

    def close(self):
        self.raw.close()

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        self.raw.rollback() if kind else self.raw.commit()


def enabled():
    return MARKER.is_file() and os.environ.get('LUMINOUS_SQLITE_MAINTENANCE') != '1'
