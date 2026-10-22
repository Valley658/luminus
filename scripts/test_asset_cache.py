import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import server

class AssetHandler:
    _asset_versions={}
    asset_version=server.Handler.asset_version
    serve_asset=server.Handler.serve_asset
    def __init__(self,path,headers=None):self.path=path;self.headers=headers or {};self.wfile=io.BytesIO();self.sent={}
    def send_response(self,status):self.status=status
    def send_header(self,key,value):self.sent[key]=value
    def end_headers(self):pass

class AssetCacheTests(unittest.TestCase):
    def test_versioned_assets_cache_and_conditional_request(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(server,'ROOT',Path(folder)):
            file=Path(folder)/'static/luminus/assets/example.js';file.parent.mkdir(parents=True);file.write_text('const example=1;',encoding='utf-8')
            first=AssetHandler('/assets/example.js');version=first.asset_version('example.js');first.path+='?v='+version;first.serve_asset('/assets/example.js')
            self.assertEqual(first.status,200);self.assertIn('immutable',first.sent['Cache-Control'])
            cached=AssetHandler(first.path,{'If-None-Match':first.sent['ETag']});cached.serve_asset('/assets/example.js');self.assertEqual(cached.status,304);self.assertEqual(cached.wfile.getvalue(),b'')
            file.write_text('const example=222;',encoding='utf-8')
            updated=AssetHandler(first.path,{'If-None-Match':first.sent['ETag']});updated.serve_asset('/assets/example.js');self.assertEqual(updated.status,200);self.assertNotIn('immutable',updated.sent['Cache-Control']);self.assertNotEqual(updated.sent['ETag'],first.sent['ETag'])

if __name__=='__main__':unittest.main()
