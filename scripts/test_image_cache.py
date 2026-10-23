import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
import database
import image_cache as cache
from test_luminous_port import Request


class ImageCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=patch.object(database,'DB',Path(self.temp.name)/'test.sqlite3');self.db.start()
        self.root=patch.object(cache,'ROOT',Path(self.temp.name)/'images');self.root.start()
        cache.init()

    def tearDown(self):
        cache.known.clear();cache.pending.clear();self.root.stop();self.db.stop();self.temp.cleanup()

    def test_animated_gif_bytes_and_conditional_request(self):
        a=Image.new('RGB',(8,8),'red');b=Image.new('RGB',(8,8),'blue');stream=io.BytesIO()
        a.save(stream,format='GIF',save_all=True,append_images=[b],duration=100,loop=0)
        raw=stream.getvalue();rid=cache.store('fixture',raw)
        h=Request();cache.serve(h,rid)
        self.assertEqual(h.status,200);self.assertEqual(h.wfile.getvalue(),raw)
        etag=next(v for k,v in h.sent if k=='ETag')
        h=Request();h.headers['If-None-Match']=etag;cache.serve(h,rid)
        self.assertEqual(h.status,304);self.assertEqual(h.wfile.getvalue(),b'')

    def test_only_youtube_thumbnail_origins_are_enqueued(self):
        with patch.object(cache,'enqueue') as enqueue:
            for url in ('http://127.0.0.1/private','https://i.ytimg.com.evil/vi/abcdefghijk/hqdefault.jpg','https://i.ytimg.com/../private'):
                self.assertEqual(cache.thumbnail(url),url)
            enqueue.assert_not_called()

    def test_cache_limit_is_enforced(self):
        stream=io.BytesIO();Image.new('RGB',(8,8)).save(stream,format='PNG');raw=stream.getvalue()
        with patch.object(cache,'LIMIT',len(raw)):
            first=cache.store('first',raw);second=cache.store('second',raw)
        self.assertFalse((cache.ROOT/first).exists());self.assertTrue((cache.ROOT/second).exists())

    def test_large_video_thumbnail_becomes_small_webp(self):
        stream=io.BytesIO();Image.new('RGB',(1920,1080),'blue').save(stream,format='JPEG')
        rid=cache.store('https://i.ytimg.com/vi/abcdefghijk/maxresdefault.jpg',stream.getvalue())
        with Image.open(cache.ROOT/rid) as im:
            self.assertEqual(im.format,'WEBP');self.assertEqual(im.size,(640,360))
