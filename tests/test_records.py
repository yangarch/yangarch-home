"""Exercise records without publishing sample personal entries."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from public_links import is_record_source
from records import validate_records


def fixture():
    return {'id': 'test-ride', 'category': 'cycling', 'title': '검증용 <제목>',
            'date': '2026-11-03', 'body': ['검증용 본문 <script>alert(1)</script>', '두 번째 문단'],
            'source': {'label': '원문 & 출처', 'url': 'https://gall.dcinside.com/board/view/?id=cycle&no=1'}}


class RecordsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('records_build', ROOT / 'scripts/build.py')
        cls.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.builder)

    def test_empty_categories_are_linked_and_navigable(self):
        data = json.loads((ROOT / 'content/public.json').read_text())
        home = self.builder.home(data)
        self.assertIn('href="/records/"', home)
        for slug in self.builder.CATEGORIES:
            self.assertIn(f'href="/records/{slug}/"', home)
            page = self.builder.record_index([], slug)
            self.assertIn('아직 등록한 기록이 없습니다.', page)
            self.assertIn(f'href="/records/{slug}/" aria-current="page"', page)
            for other in self.builder.CATEGORIES:
                self.assertIn(f'href="/records/{other}/"', page)

    def test_content_dates_sources_and_category_filter(self):
        entry = fixture()
        older = {**copy.deepcopy(entry), 'id': 'older-swim', 'category': 'swimming', 'date': '2025-12-31'}
        records = validate_records({'entries': [older, entry]})
        self.assertEqual([item['id'] for item in records], ['test-ride', 'older-swim'])
        cycling = self.builder.record_index(records, 'cycling')
        self.assertIn('/records/entry/test-ride/', cycling)
        self.assertNotIn('/records/entry/older-swim/', cycling)
        detail = self.builder.record_page(entry)
        self.assertIn('<time datetime="2026-11-03">', detail)
        self.assertIn('no=1" target="_blank" rel="noopener noreferrer"', detail)
        self.assertIn('원문 보기 · 원문 &amp; 출처', detail)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', detail)
        self.assertNotIn('<script>alert', detail)
        self.assertIn('검증용 &lt;제목&gt;', detail)
        self.assertIn('두 번째 문단', detail)

    def test_rejects_unsafe_or_ambiguous_records(self):
        for field,value in [('id','../../private'), ('category','private'), ('date','2026-02-30'), ('date','20261103'), ('title',''), ('body',[])]:
            entry = {**fixture(), field:value}
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate_records({'entries':[entry]})
        with self.assertRaises(ValueError):
            validate_records({'entries':[fixture(), fixture()]})
        for url in ['javascript:alert(1)', '//gall.dcinside.com/board/', 'http://blog.naver.com/a',
                    'https://gall.dcinside.com.evil.example/', 'https://gall.dcinside.com@evil.example/',
                    'https://evil.example@gall.dcinside.com/', 'https://gall.dcinside.com:443/',
                    'https://gall.dcinside.com/\nsecret', 'https://gall.dcinside.com\\@evil.example/']:
            with self.subTest(url=url):
                self.assertFalse(is_record_source(url))
                entry = fixture()
                entry['source']['url'] = url
                with self.assertRaises(ValueError):
                    validate_records({'entries':[entry]})
        for url in ['https://blog.naver.com/example/1', 'https://m.blog.naver.com/example/1',
                    'https://example.tistory.com/1', 'https://m.dcinside.com/board/cycle/1']:
            self.assertTrue(is_record_source(url))

    def test_record_routes_sitemap_and_sources_are_packaged_in_manifest(self):
        # Use a separate build root: fixtures never enter the actual public dist.
        with tempfile.TemporaryDirectory(prefix='record-build-') as temp:
            root = Path(temp)
            (root / 'content').mkdir()
            shutil.copyfile(ROOT / 'content/public.json', root / 'content/public.json')
            shutil.copytree(ROOT / 'assets', root / 'assets')
            (root / 'content/records.json').write_text(json.dumps({'entries':[fixture()]}))
            with patch.object(self.builder, 'ROOT', root), patch.object(self.builder, 'DIST', root / 'dist'):
                manifest = self.builder.build()
            path = '/records/entry/test-ride/'
            self.assertIn(path, manifest['routes'])
            self.assertIn('https://yangarch.net'+path, (root / 'dist/sitemap.xml').read_text())
            self.assertEqual(manifest['record_sources'], [fixture()['source']['url']])
            self.assertTrue((root / 'dist' / manifest['routes'][path]).is_file())


if __name__ == '__main__':
    unittest.main()
