"""Local persistence, selected export, sanitized photos, and HTTP access boundary."""
import copy
import http.client
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
HAS_PILLOW = importlib.util.find_spec('PIL') is not None
if HAS_PILLOW:
    from PIL import Image
    from editor import Conflict, Store, encoded, make_server
    from record_media import clean_jpeg, image_path


@unittest.skipUnless(HAS_PILLOW,'Run with .local/editor-venv/bin/python for editor tests')
class EditorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='record-editor-test-')
        self.root = Path(self.temp.name).resolve()
        for folder in ('scripts','assets','editor','deploy'):
            shutil.copytree(ROOT/folder,self.root/folder,ignore=shutil.ignore_patterns('__pycache__'))
        (self.root/'content').mkdir()
        shutil.copyfile(ROOT/'content/public.json',self.root/'content/public.json')
        (self.root/'content/records.json').write_text('{"entries":[]}')
        (self.root/'tests').mkdir()
        shutil.copyfile(ROOT/'tests/nginx_integration.py',self.root/'tests/nginx_integration.py')
        self.store = Store(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def filled(self, title='검증용 기록'):
        draft = self.store.create()
        entry = copy.deepcopy(draft['entry'])
        entry.update(title=title,body=['검증용 본문입니다.'])
        return self.store.save(entry['id'],entry,draft['revision'])

    @staticmethod
    def photo():
        output = io.BytesIO()
        exif = Image.Exif()
        exif[271] = 'PRIVATE_TEST_CAMERA'
        exif[274] = 6
        Image.new('RGB',(40,20),'#497354').save(output,'JPEG',exif=exif)
        return output.getvalue()

    def test_draft_survives_restart_without_becoming_public(self):
        draft = self.filled()
        other = Store(self.root)
        self.assertEqual(other.load(draft['entry']['id'])['entry'],draft['entry'])
        self.assertEqual(other.published(),[])
        self.assertFalse((self.root/'dist').exists())
        self.assertEqual((other.post_dir(draft['entry']['id'])/'draft.json').stat().st_mode & 0o777,0o600)

    def test_stale_revision_does_not_overwrite_a_saved_draft(self):
        draft = self.filled()
        updated = self.store.save(draft['entry']['id'],draft['entry'],draft['revision'])
        with self.assertRaises(Conflict):
            self.store.save(draft['entry']['id'],{**draft['entry'],'title':'덮어쓰기'},draft['revision'])
        self.assertEqual(self.store.load(draft['entry']['id']),updated)

    def test_images_are_reencoded_and_originals_stay_private(self):
        draft = self.filled()
        raw = self.photo()
        block = self.store.upload(draft['entry']['id'],raw)
        converted = self.store.media_file(block['src']).read_bytes()
        self.assertTrue(clean_jpeg(converted))
        self.assertFalse(clean_jpeg(raw))
        self.assertNotIn(b'PRIVATE_TEST_CAMERA',converted)
        with Image.open(io.BytesIO(converted)) as image:
            self.assertFalse(image.getexif())
            self.assertEqual(image.size,(20,40))
        originals = list((self.store.post_dir(draft['entry']['id'])/'originals').glob('*'))
        self.assertEqual([path.read_bytes() for path in originals],[raw])
        for raw_bad in (b'<svg xmlns="http://www.w3.org/2000/svg"/>',b'not an image'):
            with self.assertRaises(ValueError):
                self.store.upload(draft['entry']['id'],raw_bad)
        for url in ('/media/records/../../draft.json','https://evil.example/p.jpg',block['src'].replace('.jpg','.svg')):
            with self.assertRaises(ValueError):
                image_path(url)

    def test_prepare_exports_only_selected_snapshot_and_images(self):
        draft = self.filled()
        unselected = self.filled('비공개 초안 검증용')
        image = self.store.upload(draft['entry']['id'],self.photo())
        entry = {**draft['entry'],'body':['첫 문단',image,'마지막 문단 <script>bad()</script>']}
        draft = self.store.save(entry['id'],entry,draft['revision'])
        result = self.store.prepare(entry['id'],draft['revision'])
        self.assertIn('서버에는 아직',result['message'])
        self.assertEqual([item['id'] for item in self.store.published()],[entry['id']])
        page = (self.root/'dist'/result['path'].lstrip('/')/'index.html').read_text()
        self.assertLess(page.index('첫 문단'),page.index('<figure'))
        self.assertLess(page.index('<figure'),page.index('마지막 문단'))
        self.assertNotIn('<script>bad()',page)
        self.assertIn('&lt;script&gt;bad()',page)
        manifest = json.loads((self.root/'artifacts/manifest.json').read_text())
        self.assertIn(image['src'],manifest['routes'])
        with tarfile.open(self.root/'artifacts/yangarch-home.tar.gz') as archive:
            names = archive.getnames()
            self.assertIn('dist'+image['src'],names)
            self.assertFalse(any('/editor' in name or '/originals/' in name or name.startswith('content/') for name in names))
            self.assertFalse(any(unselected['entry']['id'] in name for name in names))
        # Saving changes after preparing a public snapshot cannot change that snapshot.
        self.store.save(entry['id'],{**entry,'title':'아직 반영하지 않은 수정'},draft['revision'])
        self.assertEqual(self.store.published()[0]['title'],'검증용 기록')
        self.assertEqual(self.store.listing()[0]['status'],'수정 중')

    def test_failed_prepare_keeps_previous_public_files(self):
        draft = self.filled()
        self.store.prepare(draft['entry']['id'],draft['revision'])
        before = (self.root/'artifacts/manifest.json').read_bytes()
        records_before = (self.root/'content/records.json').read_bytes()
        blocked = {**draft['entry'],'title':'unpublished-example-service'}
        draft = self.store.save(blocked['id'],blocked,draft['revision'])
        with self.assertRaises(ValueError):
            self.store.prepare(blocked['id'],draft['revision'])
        self.assertEqual((self.root/'artifacts/manifest.json').read_bytes(),before)
        self.assertEqual((self.root/'content/records.json').read_bytes(),records_before)
        self.assertEqual(self.store.load(blocked['id'])['entry']['title'],'unpublished-example-service')
        # A filesystem failure during the swap also restores the prior release.
        draft = self.store.save(blocked['id'],{**blocked,'title':'교체 실패 검증'},draft['revision'])
        import os
        replace = os.replace
        failed = False
        def fail_once(source, target):
            nonlocal failed
            if Path(target)==self.root/'dist' and not failed:
                failed = True
                raise OSError('Synthetic commit failure')
            return replace(source,target)
        with patch('editor.os.replace',side_effect=fail_once), self.assertRaises(OSError):
            self.store.prepare(blocked['id'],draft['revision'])
        self.assertTrue(failed)
        self.assertEqual((self.root/'artifacts/manifest.json').read_bytes(),before)
        self.assertEqual((self.root/'content/records.json').read_bytes(),records_before)

    def test_http_requires_loopback_host_session_origin_and_csrf(self):
        server = make_server(root=self.root,port=0)
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        def request(path,method='GET',headers=None,data=None):
            connection = http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            connection.request(method,path,body=encoded(data) if data is not None else None,headers=headers or {})
            response = connection.getresponse()
            result = (response.status,dict(response.getheaders()),response.read())
            connection.close()
            return result
        try:
            self.assertEqual(request('/api/posts')[0],403)
            status,headers,body = request('/')
            self.assertEqual(status,200)
            self.assertIn('SameSite=Strict',headers['Set-Cookie'])
            cookie = headers['Set-Cookie'].split(';')[0]
            self.assertEqual(request('/',headers={'Host':'evil.example'})[0],403)
            self.assertEqual(request('/',headers={'Origin':'https://evil.example'})[0],403)
            status,_,body = request('/api/session',headers={'Cookie':cookie})
            self.assertEqual(status,200)
            token = json.loads(body)['token']
            auth = {'Cookie':cookie,'Origin':server.origin,'X-Editor-Token':token,'Content-Type':'application/json'}
            self.assertEqual(request('/api/posts','POST',{'Cookie':cookie},{} )[0],403)
            self.assertEqual(request('/api/posts','POST',{**auth,'Origin':'https://evil.example'},{} )[0],403)
            self.assertEqual(request('/api/posts','POST',{**auth,'X-Editor-Token':'wrong'},{} )[0],403)
            self.assertEqual(request('/api/import','POST',{'Cookie':cookie},{'url':'https://example.com','category':'cycling'})[0],403)
            self.assertEqual(request('/api/import','POST',auth,{'url':'http://127.0.0.1/private','category':'cycling'})[0],400)
            status,_,body = request('/api/posts','POST',auth,{})
            self.assertEqual(status,201)
            entry = json.loads(body)['entry']
            for path in ('/.env','/.local/editor/posts/'+entry['id']+'/draft.json','/scripts/editor.py','/originals/test','/media/records/../../draft.json'):
                self.assertIn(request(path,headers={'Cookie':cookie})[0],(400,404))
            self.assertEqual(request('/api/posts','POST',{**auth,'Content-Length':'2000001'},None)[0],413)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_local_publication_policy_is_preserved_in_prepared_release(self):
        term='local-only-publication-example'
        (self.root/'content/privacy.local.json').write_text(json.dumps({'blocked_terms':[term]}))
        draft=self.filled()
        self.store.prepare(draft['entry']['id'],draft['revision'])
        with tarfile.open(self.root/'artifacts/yangarch-home.tar.gz') as archive:
            policy=json.load(archive.extractfile('artifacts/content-policy.json'))
            self.assertIn(term,policy['blocked_terms'])
        draft=self.store.save(draft['entry']['id'],{**draft['entry'],'title':term},draft['revision'])
        with self.assertRaises(ValueError):self.store.prepare(draft['entry']['id'],draft['revision'])
        self.assertEqual(self.store.published()[0]['title'],'검증용 기록')


if __name__=='__main__':
    unittest.main()
