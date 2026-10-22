"""Shared storage; VM uses MySQL after a verified SQLite migration."""
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / 'DB' / 'pastellive.sqlite3'
DEFAULT_DB = DB


@contextmanager
def connect():
    import mysql_backend
    if DB == DEFAULT_DB and mysql_backend.enabled():
        connection = mysql_backend.Connection()
        try:
            with connection:
                yield connection
        finally:
            connection.close()
        return
    DB.parent.mkdir(exist_ok=True)
    connection = sqlite3.connect(DB, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('PRAGMA busy_timeout=15000')
    connection.executescript('''
    CREATE TABLE IF NOT EXISTS channels(position INTEGER PRIMARY KEY, name TEXT NOT NULL, photo TEXT NOT NULL,
      source TEXT NOT NULL DEFAULT '', image_source TEXT NOT NULL DEFAULT '', youtube_channel_id TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS videos(id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
      channelId TEXT NOT NULL, channelName TEXT NOT NULL, thumbnail TEXT NOT NULL, publishedAt TEXT NOT NULL, fetchedAt REAL NOT NULL);
    CREATE INDEX IF NOT EXISTS videos_channel_kind ON videos(channelId,kind,publishedAt DESC);
    CREATE TABLE IF NOT EXISTS music_sources(channel_id TEXT NOT NULL,source_id TEXT NOT NULL,title TEXT NOT NULL,kind TEXT NOT NULL,PRIMARY KEY(channel_id,source_id));
    CREATE TABLE IF NOT EXISTS music_memberships(channel_id TEXT NOT NULL,video_id TEXT NOT NULL,source_id TEXT NOT NULL,kind TEXT NOT NULL,PRIMARY KEY(channel_id,video_id,source_id));
    CREATE INDEX IF NOT EXISTS music_video ON music_memberships(video_id);
    CREATE TABLE IF NOT EXISTS metadata(name TEXT PRIMARY KEY, value REAL NOT NULL);
    ''')
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def migrate():
    with connect() as db:
        profile_file = ROOT / 'drafts/profiles.json'
        if not db.execute('SELECT 1 FROM channels LIMIT 1').fetchone() and profile_file.exists():
            profiles = json.loads(profile_file.read_text(encoding='utf-8'))
            db.executemany('INSERT INTO channels VALUES(?,?,?,?,?,?)', [(i, p['name'], p['photo'], p.get('source',''),p.get('image_source',''),p['youtube_channel_id']) for i,p in enumerate(profiles,1)])
        old = ROOT / 'runtime/shorts-cache.json'
        if not db.execute('SELECT 1 FROM videos LIMIT 1').fetchone() and old.exists():
            data = json.loads(old.read_text(encoding='utf-8'))
            write_videos(db, data)
        from channel_catalog import NEW_CHANNELS, GROUPS
        for position,name,photo,cid in NEW_CHANNELS:
            db.execute('INSERT OR REPLACE INTO channels VALUES(?,?,?,?,?,?)', (position,name,photo,'https://namu.wiki/w/'+name,'',cid))
        for group,members in GROUPS:
            for position,name in members:
                db.execute('UPDATE channels SET name=? WHERE position=?',(name,position))
        removed = db.execute('SELECT youtube_channel_id FROM channels WHERE position=14').fetchone()
        if removed:
            db.execute('DELETE FROM videos WHERE channelId=?',(removed[0],))
        db.execute('DELETE FROM channels WHERE position=14')
        revision=db.execute("SELECT value FROM metadata WHERE name='catalog_revision'").fetchone()
        if not revision or revision[0]<5:
            db.execute("INSERT OR REPLACE INTO metadata VALUES('catalog_revision',5)")
            db.execute("INSERT OR REPLACE INTO metadata VALUES('updatedAt',0)")


def profiles():
    with connect() as db:
        return [dict(row) for row in db.execute('SELECT * FROM channels ORDER BY position')]


def write_videos(db, data):
    stamp = data['updatedAt']
    if data.get('shortsHistoryAt'):
        db.execute("INSERT OR REPLACE INTO metadata VALUES('shortsHistoryAt',?)",(data['shortsHistoryAt'],))
    db.executemany('INSERT OR REPLACE INTO music_sources VALUES(?,?,?,?)',data.get('musicSources',[]))
    db.executemany('INSERT OR REPLACE INTO music_memberships VALUES(?,?,?,?)',data.get('musicMembers',[]))
    for kind, key in [('shorts','items'), ('video','videos')]:
        for item in data.get(key, []):
            db.execute('INSERT OR REPLACE INTO videos VALUES(?,?,?,?,?,?,?,?)',
                (item['id'],kind,item['title'],item['channelId'],item['channelName'],item['thumbnail'],item['publishedAt'],stamp))
    db.execute('DELETE FROM videos WHERE fetchedAt < ?', (time.time()-86400*29,))
    db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)', ('updatedAt',stamp))


def save(data):
    with connect() as db:
        write_videos(db,data)


def load():
    with connect() as db:
        stamp = db.execute("SELECT value FROM metadata WHERE name='updatedAt'").fetchone()
        data = dict(updatedAt=stamp[0] if stamp else 0,items=[],videos=[])
        membership={}
        for row in db.execute('SELECT channel_id,video_id FROM music_memberships'):
            membership.setdefault(row['video_id'],set()).add(row['channel_id'])
        for row in db.execute('SELECT * FROM videos WHERE fetchedAt > ? ORDER BY publishedAt DESC', (time.time()-86400*29,)):
            item=dict(row);kind=item.pop('kind');item.pop('fetchedAt');item['songChannelIds']=sorted(membership.get(item['id'],[]));item['isSong']=bool(item['songChannelIds'])
            data['items' if kind=='shorts' else 'videos'].append(item)
        return data


migrate()
