import datetime
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import accounts
import admin_operations
import pages
from test_accounts import Handler


class PageTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.patch=patch.object(accounts.database,'DB',Path(self.folder.name)/'test.sqlite3')
        self.patch.start();accounts.init();admin_operations.init();pages.init()
        self.comments_patch=patch.object(pages.comments,'youtube',return_value=dict(items=[],nextCursor='',refreshing=True));self.comments_patch.start()
        with accounts.database.connect() as db:
            db.execute("INSERT INTO users(id,handle,name,created,is_admin) VALUES('owner','owner','Owner',0,1)")
            db.execute('INSERT INTO videos VALUES(?,?,?,?,?,?,?,?)',('abcdefghijk','video','<script>unsafe</script>','UCtest','Test channel','https://i.ytimg.com/a.jpg','2026-10-22',0))

    def tearDown(self):self.comments_patch.stop();self.patch.stop();self.folder.cleanup()

    def test_watch_escapes_metadata_and_uses_same_site_recommendations(self):
        related=dict(id='zyxwvutsrqp',title='Related',thumbnail='https://i.ytimg.com/b.jpg',channelName='Channel')
        with patch.object(pages.youtube_shorts,'shorts_page',return_value=dict(items=[related])),patch.object(pages.youtube_shorts,'video_feed',return_value=dict(items=[related])):
            title,body=pages.watch('abcdefghijk')
        self.assertIn('youtube-nocookie.com/embed/abcdefghijk',body)
        self.assertIn('/watch?v=zyxwvutsrqp',body)
        self.assertNotIn('<script>unsafe</script>',body)
        self.assertIn('&lt;script&gt;',body)
        self.assertIn('enablejsapi=1',body)
        self.assertIn('mute=0',body)
        self.assertIn('cc_load_policy=0',body)
        self.assertIn('data-watch-scroll="-1"',body)
        self.assertIn('data-watch-scroll="1"',body)
        self.assertIsNone(pages.watch('../bad'))

    def test_watch_shorts_pagination_does_not_skip_unrendered_cached_items(self):
        items=[dict(id='abcdefghijk',title='Current',thumbnail='https://i.ytimg.com/a.jpg',channelName='Current')]
        items += [dict(id='v'+str(i).zfill(10),title='Next',thumbnail='https://i.ytimg.com/a.jpg',channelName='Channel') for i in range(39)]
        with patch.object(pages.youtube_shorts,'shorts_page',return_value=dict(items=items,hasMore=True)),patch.object(pages.youtube_shorts,'video_feed',return_value=dict(items=[])):
            _,body=pages.watch('abcdefghijk','https://pastellive.co.kr')
        self.assertIn('data-offset="9"',body)
        self.assertIn('data-more="true"',body)
        self.assertEqual(body.count('watch-card watch-short'),8)
        self.assertIn('origin=https%3A%2F%2Fpastellive.co.kr',body)

    def test_watch_uses_cached_channel_photo_name_and_subscribers(self):
        with accounts.database.connect() as db:
            db.execute('INSERT INTO channels VALUES(?,?,?,?,?,?)',(1,'Channel name','01.svg','','','UCtest'))
            db.execute('INSERT INTO channel_details VALUES(?,?,?,?,?)',('UCtest','API name','https://example.invalid/photo',123000,1))
        with patch.object(pages.youtube_shorts,'shorts_page',return_value=dict(items=[])),patch.object(pages.youtube_shorts,'video_feed',return_value=dict(items=[])):
            _,body=pages.watch('abcdefghijk')
        self.assertIn('src="/profiles/01.svg"',body)
        self.assertIn('<strong>Channel name</strong>',body)
        self.assertIn('구독자 12.3만명',body)

    def test_shorts_use_vertical_player_and_nine_end_choices(self):
        with accounts.database.connect() as db:db.execute("UPDATE videos SET kind='shorts' WHERE id='abcdefghijk'")
        items=[dict(id='v'+str(i).zfill(10),title='Choice',thumbnail='https://i.ytimg.com/a.jpg',channelName='Channel') for i in range(12)]
        with patch.object(pages.youtube_shorts,'shorts_page',return_value=dict(items=items,hasMore=False)),patch.object(pages.youtube_shorts,'video_feed',return_value=dict(items=[])):
            _,body=pages.watch('abcdefghijk')
        self.assertIn('class="watch-stage is-short"',body)
        self.assertEqual(body.count('class="watch-end-card"'),9)
        self.assertIn('data-next="v0000000000"',body)
        self.assertIn('class="watch-end" hidden',body)
        self.assertIn('class="watch-replay"',body)

    def test_admin_schedule_is_visible_on_correct_korean_week(self):
        start=datetime.datetime(2026,10,22,21,30,tzinfo=pages.ZONE).timestamp()
        h=Handler({});actor={'id':'owner'}
        admin_operations.post(h,'/api/admin/schedule',dict(title='Test schedule',starts_at=start,ends_at=0,link='https://youtu.be/abcdefghijk',note='<script>no</script>'),actor)
        self.assertEqual(h.status,200)
        with patch.object(pages.database,'profiles',return_value=[]):title,body=pages.schedule('2026-10-22')
        self.assertIn('21:30',body);self.assertIn('Test schedule',body)
        self.assertIn('href="/watch?v=abcdefghijk"',body)
        self.assertNotIn('<script>no</script>',body)
        with patch.object(pages.database,'profiles',return_value=[]):_,other=pages.schedule('2026-11-12')
        self.assertNotIn('Test schedule',other)

    def test_schedule_rejects_script_links(self):
        h=Handler({})
        admin_operations.post(h,'/api/admin/schedule',dict(title='Invalid',starts_at=1792680000,link='javascript:alert(1)'),{'id':'owner'})
        self.assertEqual(h.status,400)
        with accounts.database.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM schedule_events').fetchone()[0],0)
