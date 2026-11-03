"""Check the public-link boundary and rendered destination coverage."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from public_links import SERVICE_URLS, is_public_link
from content_policy import blocked_text, release_policy


class PublicLinksTest(unittest.TestCase):
    def test_only_reviewed_service_destinations(self):
        for url in SERVICE_URLS:
            self.assertTrue(is_public_link(url), url)
        for url in [
            'javascript:alert(1)', '//archgrab.yangarch.net/',
            'https://archgrab.yangarch.net.evil.example/',
            'https://archgrab.yangarch.net@evil.example/',
            'https://evil.example@archgrab.yangarch.net/',
            'https://archgrab.yangarch.net:8443/',
            'https://archgrab.yangarch.net/?next=https://evil.example/',
            'https://unknown.yangarch.net/', 'https://github.com@evil.example/',
            'http://poogiegram.poogie.one/', 'http://njc.yangarch.net/admin',
            'https://unpublished-example-service.example/',
        ]:
            self.assertFalse(is_public_link(url), url)

    def test_services_reach_home_and_their_detail_page(self):
        home = (ROOT / 'dist/index.html').read_text()
        data = json.loads((ROOT / 'content/public.json').read_text())
        seen = set()
        for project in data['projects']:
            detail = (ROOT / 'dist/projects' / project['id'] / 'index.html').read_text()
            for service in project.get('services', []):
                url = service['url']
                self.assertIn(f'href="{url}"', home)
                self.assertIn(f'href="{url}"', detail)
                seen.add(url)
            if not project.get('services'):
                self.assertNotIn('class="service-link"', detail)
        self.assertEqual(seen, SERVICE_URLS)

    def test_unpublished_service_is_absent_from_public_output(self):
        policy=release_policy(ROOT)
        for path in (ROOT / 'dist').rglob('*'):
            if path.is_file():
                if path.suffix != '.jpg':
                    self.assertFalse(blocked_text(path.read_text(),policy),str(path))

    def test_builder_rejects_unreviewed_service(self):
        spec = importlib.util.spec_from_file_location('homepage_build', ROOT / 'scripts/build.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with self.assertRaises(ValueError):
            module.service_links({'name': 'Test', 'services': [{'label': '접속', 'url': 'https://unknown.yangarch.net/'}]})


if __name__ == '__main__':
    unittest.main()
