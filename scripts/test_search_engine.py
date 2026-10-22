import unittest
from unittest.mock import patch
import search_engine

class SearchTests(unittest.TestCase):
    def test_large_mixed_results_and_pagination(self):
        hits=[{'id':str(i),'kind':'shorts' if i%2 else 'video'} for i in range(60)]
        with patch.object(search_engine,'ready',True),patch.object(search_engine,'request',return_value={'hits':hits,'estimatedTotalHits':125}) as request:
            data=search_engine.search('타비',0,tab='all',include_songs=True)
        self.assertEqual(request.call_args.args[1]['limit'],60)
        self.assertNotIn('filter',request.call_args.args[1])
        self.assertEqual(data['nextOffset'],60);self.assertTrue(data['hasMore'])
        self.assertTrue(any(v['kind']=='shorts' for v in data['items']))

    def test_end_of_search_results(self):
        with patch.object(search_engine,'ready',True),patch.object(search_engine,'request',return_value={'hits':[{'id':'last'}],'estimatedTotalHits':61}):
            data=search_engine.search('타비',60,tab='all',include_songs=True)
        self.assertFalse(data['hasMore']);self.assertEqual(data['nextOffset'],61)

if __name__=='__main__':unittest.main()
