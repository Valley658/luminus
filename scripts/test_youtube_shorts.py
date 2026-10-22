import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch
import youtube_shorts as shorts


def video(index=0, **changes):
    item = dict(id=f'v{index:010}', snippet=dict(title='예시 #shorts', description='',
                channelId=f'UC{index:022}', channelTitle='채널', publishedAt='2026-10-19T00:00:00Z',
                thumbnails={'high': {'url': 'https://i.ytimg.com/test.jpg'}}),
                contentDetails={'duration': 'PT30S'}, status={'embeddable': True, 'privacyStatus': 'public'})
    item.update(changes)
    return item


class ShortsTests(unittest.TestCase):
    def test_home_shorts_random_seed_keeps_pages_consistent(self):
        items=[dict(id=f'v{i:010}') for i in range(90)]
        state=dict(items=items,updatedAt=123,configured=True,refreshing=False,error='')
        with patch.object(shorts,'cached',return_value=state),patch.object(shorts,'feed',return_value=state):
            first=shorts.shorts_page(seed='visit-one');second=shorts.shorts_page(offset=40,seed='visit-one')
            self.assertNotEqual(first['items'],shorts.shorts_page(seed='visit-two')['items'])
            self.assertEqual(first,shorts.shorts_page(seed='visit-one'))
            self.assertFalse({i['id'] for i in first['items']}&{i['id'] for i in second['items']})
            self.assertEqual(shorts.shorts_page('channel',seed='one')['items'],shorts.shorts_page('channel',seed='two')['items'])

    def test_short_history_pagination_without_four_item_cap(self):
        profile=shorts.database.profiles()[0];cid=profile['youtube_channel_id'];pages=[]
        def fake(resource,key,**params):
            if resource=='channels':return {'items':[dict(id=cid,contentDetails={'relatedPlaylists':{'uploads':'uploads'}})]}
            if resource=='playlistItems':
                if params['playlistId']=='uploads':return {'items':[]}
                token=params.get('pageToken','');pages.append(token)
                start=3 if token else 0
                data={'items':[{'contentDetails':{'videoId':f'v{i:010}'}} for i in range(start,start+3)]}
                if not token:data['nextPageToken']='second'
                return data
            items=[]
            for vid in params['id'].split(','):
                item=video(int(vid[1:]));item['snippet'].update(channelId=cid,title='해시태그 없는 짧은 영상');items.append(item)
            return {'items':items}
        with patch.object(shorts.database,'profiles',return_value=[profile]),patch.object(shorts,'api',side_effect=fake),patch.object(shorts.music_catalog,'discover',return_value=([],[])):
            data=shorts.collect('test')
        self.assertEqual(len(data['items']),6)
        self.assertEqual(pages,['','second'])

    def test_music_metadata_and_channel_membership(self):
        self.assertFalse(shorts.is_song({'title':'노래 부르는 친구 놀리기'}))
        self.assertFalse(shorts.is_song({'title':'Cover 이야기','isSong':False}))
        self.assertTrue(shorts.is_song({'title':'제목에 음악 표시 없는 발표곡','isSong':True}))
        track=dict(id='release',title='발표곡',channelId='topic_channel',isSong=True,songChannelIds=['official'])
        profile=dict(position=1,youtube_channel_id='official')
        with patch.object(shorts.database,'profiles',return_value=[profile]),patch.object(shorts,'feed',return_value=dict(refreshing=False,configured=True,error='')),patch.object(shorts,'cached',return_value={'videos':[track]}):
            self.assertEqual(shorts.video_feed('seed',channel='1',tab='songs')['items'],[track])
            self.assertEqual(shorts.video_feed('seed',channel='1')['items'],[])

    def test_release_and_music_playlist_discovery(self):
        profiles=[dict(position=1,youtube_channel_id='official'),dict(position=13,youtube_channel_id='gangji')]
        def fake(resource,key,**params):
            if resource=='playlists':return {'items':[{'id':'music','snippet':{'title':'커버곡'}},{'id':'game','snippet':{'title':'게임'}}]}
            return {'items':[{'contentDetails':{'videoId':params['playlistId']+'_track'}}]}
        with patch.object(shorts.music_catalog,'releases',return_value=['album']):
            sources,members=shorts.music_catalog.discover(fake,'test',profiles)
        self.assertIn(('official','album','공식 발표곡','release'),sources)
        self.assertIn(('official','album_track','album','release'),members)
        self.assertIn(('gangji','music_track','music','playlist'),members)
        self.assertFalse(any(v[2]=='game' for v in members))

    def test_music_filter_and_pagination(self):
        songs=[dict(id='song',title='신곡 Cover',channelId='test')]
        regular=[dict(id=str(i),title='게임 영상',channelId='test') for i in range(25)]
        with patch.object(shorts,'feed',return_value=dict(refreshing=False,configured=True,error='')),patch.object(shorts,'cached',return_value={'videos':songs+regular}):
            result=shorts.video_feed('test')
            self.assertEqual(len(result['items']),18)
            self.assertTrue(result['hasMore'])
            self.assertNotIn('song',[v['id'] for v in result['items']])
            self.assertEqual(shorts.video_feed('test',18)['hasMore'],False)
            self.assertEqual(shorts.video_feed('test',tab='songs')['items'],songs)
            self.assertEqual(len(shorts.video_feed('test',18,include_songs=True)['items']),8)

    def test_short_filter(self):
        self.assertTrue(shorts.is_short(video()))
        self.assertFalse(shorts.is_short(video(contentDetails={'duration': 'PT3M1S'})))
        self.assertFalse(shorts.is_short(video(status={'embeddable': False, 'privacyStatus': 'public'})))
        item = video()
        item['snippet']['title'] = '일반 동영상'
        self.assertFalse(shorts.is_short(item))

    def test_all_channels_are_collected(self):
        profiles = shorts.database.profiles()
        ids = [p['youtube_channel_id'] for p in profiles]
        calls = []
        def fake(resource, key, **params):
            calls.append(resource)
            if resource == 'channels':
                self.assertEqual(set(params['id'].split(',')), set(ids))
                return {'items': [dict(id=c, contentDetails={'relatedPlaylists': {'uploads': str(i)}}) for i, c in enumerate(ids)]}
            if resource == 'playlistItems':
                if params['playlistId'].startswith('UUSH'):return {'items':[]}
                return {'items': [{'contentDetails': {'videoId': f"v{int(params['playlistId']):010}"}}]}
            items = []
            for vid in params['id'].split(','):
                i = int(vid[1:]); item = video(i); item['snippet']['channelId'] = ids[i]; items.append(item)
            return {'items': items}
        with patch.object(shorts, 'api', side_effect=fake), patch.object(shorts.music_catalog,'discover',return_value=([],[])):
            data = shorts.collect('private-test-key')
        self.assertEqual(len(data['items']), len(profiles))
        self.assertEqual(calls.count('playlistItems'), len(profiles)*2)

    def test_cache_and_channel_filter_without_key(self):
        profiles = shorts.database.profiles()
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(shorts.database, 'DB', Path(directory) / 'test.sqlite3'), patch.object(shorts, 'api_key', return_value=''):
                with shorts.database.connect() as db:
                    db.executemany('INSERT INTO channels VALUES(?,?,?,?,?,?)', [(i,p['name'],p['photo'],p.get('source',''),p.get('image_source',''),p['youtube_channel_id']) for i,p in enumerate(profiles,1)])
                item = dict(id='v0000000000',title='test',channelId=profiles[0]['youtube_channel_id'],channelName='test',thumbnail='https://example.com/x.jpg',publishedAt='2026-10-21')
                shorts.database.save(dict(updatedAt=shorts.time.time(),items=[item],videos=[]))
                with patch.object(shorts,'memory_cache',None):
                    self.assertEqual(len(shorts.feed('1')['items']),1)
                    self.assertEqual(shorts.feed('2')['items'],[])
                    self.assertFalse(shorts.feed()['configured'])

    def test_api_error_does_not_expose_key(self):
        with patch('urllib.request.urlopen', side_effect=urllib.error.HTTPError('https://example/?key=secret', 403, 'denied', {}, None)):
            with self.assertRaises(RuntimeError) as caught:
                shorts.api('videos', 'secret')
        self.assertNotIn('secret', str(caught.exception))


if __name__ == '__main__':
    unittest.main()


class SharedShortsCacheTests(unittest.TestCase):
    def setUp(self):
        shorts.page_cache.clear()

    def test_pages_share_server_cache_and_limit_payload(self):
        items=[{'id':str(i)} for i in range(95)]
        state=dict(items=items,updatedAt=42,configured=True,refreshing=False,error='')
        with patch.object(shorts,'cached',return_value=state),patch.object(shorts,'feed',return_value=state) as feed:
            first=shorts.shorts_page('3',0)
            again=shorts.shorts_page('3',0)
            second=shorts.shorts_page('3',40)
            self.assertIs(first,again)
            self.assertEqual(feed.call_count,2)
            self.assertEqual(len(first['items']),40)
            self.assertTrue(first['hasMore'])
            self.assertEqual(second['items'][0]['id'],'40')
            self.assertEqual(shorts.shorts_page('3',80)['nextOffset'],95)
            self.assertFalse(shorts.shorts_page('3',80)['hasMore'])

    def test_database_refresh_invalidates_cached_page(self):
        state=dict(items=[{'id':'old'}],updatedAt=1,configured=True,refreshing=False,error='')
        with patch.object(shorts,'cached',return_value=state),patch.object(shorts,'feed',side_effect=lambda channel:state):
            self.assertEqual(shorts.shorts_page()['items'][0]['id'],'old')
            state['updatedAt']=2;state['items']=[{'id':'new'}]
            self.assertEqual(shorts.shorts_page()['items'][0]['id'],'new')
