#!/usr/bin/env python3
"""Verify artifact boundaries, HTML, links, and CSP-compatible source."""
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
import hashlib,json,re,sys
ROOT=Path(__file__).resolve().parents[1]
DIST=ROOT/'dist'
class Page(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.links=[];self.ids=[];self.issues=[];self.in_title=False;self.title='';self.h1=0
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='title':self.in_title=True
        if tag=='h1':self.h1+=1
        if 'id' in attrs:self.ids.append(attrs['id'])
        if tag=='script' and not attrs.get('src'):self.issues.append('inline script')
        if tag=='style':self.issues.append('inline style element')
        for key,value in attrs.items():
            if key.startswith('on') or key=='style':self.issues.append('inline executable/style attribute')
            if key in {'href','src'}:self.links.append(value)
        if attrs.get('target')=='_blank' and not {'noopener','noreferrer'}<=set(attrs.get('rel','').split()):self.issues.append('unsafe new tab')
    def handle_endtag(self,tag):
        if tag=='title':self.in_title=False
    def handle_data(self,text):
        if self.in_title:self.title+=text

def check():
    sys.path.insert(0,str(ROOT/'scripts'))
    from public_links import is_public_link, is_record_source
    from nginx_config import routes, production
    from record_media import clean_jpeg, image_path
    from content_policy import blocked_text, release_policy
    policy=release_policy(ROOT)
    manifest=json.loads((ROOT/'artifacts/manifest.json').read_text())
    record_sources=set(manifest.get('record_sources',[]))
    assert all(is_record_source(url) for url in record_sources), 'Unexpected record source'
    assert (ROOT/'deploy/routes.conf').read_text()==routes(), 'nginx routes differ from the checked manifest'
    assert (ROOT/'deploy/yangarch.net.conf').read_text()==production(), 'Unexpected production nginx configuration'
    paths={str(p.relative_to(DIST)) for p in DIST.rglob('*') if p.is_file()}
    assert paths==set(manifest['files']),'Unexpected public file or missing artifact'
    pages={}
    denied=re.compile(r'BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY|(?:AKIA|ASIA)[A-Z0-9]{16}|sk-[A-Za-z0-9_-]{25,}|/home/data|192\.168\.|010-\d{3,4}-\d{4}|\.env|\.map(?:["\s]|$)')
    for rel in paths:
        p=DIST/rel
        assert not p.is_symlink() and not any(x.startswith('.') for x in p.relative_to(DIST).parts),'Hidden file or symlink'
        assert hashlib.sha256(p.read_bytes()).hexdigest()==manifest['files'][rel],'Artifact checksum mismatch'
        if p.suffix=='.jpg':
            image_path('/'+rel)
            assert clean_jpeg(p.read_bytes()), 'Unprocessed image or image metadata in public file: '+rel
            continue
        assert not denied.search(p.read_text()),'Private material/credential pattern in public file: '+rel
        assert not blocked_text(p.read_text(),policy), 'Excluded private content in public file: '+rel
        if p.suffix=='.html':
            page=Page();page.feed(p.read_text());pages[rel]=page
            assert page.h1==1 and page.title,'Page needs one h1 and title'
            assert len(page.ids)==len(set(page.ids)),'Duplicate DOM id'
            assert not page.issues,(rel,page.issues)
    for rel,page in pages.items():
        for link in page.links:
            parsed=urlsplit(link)
            if parsed.scheme or parsed.netloc:
                canonical = parsed.scheme=='https' and parsed.netloc=='yangarch.net' and parsed.path in manifest['routes'] and not parsed.query and not parsed.fragment
                assert is_public_link(link) or link in record_sources or canonical, 'Unexpected external link: '+link
                continue
            target=parsed.path or ('/'+rel)
            if not parsed.path:target=next((route for route,file in manifest['routes'].items() if file==rel),None)
            assert target in manifest['routes'],(rel,'broken link',link)
            file=manifest['routes'][target]
            if parsed.fragment:assert parsed.fragment in pages[file].ids,(rel,'broken anchor',link)
    for src in (ROOT/'assets').glob('*.js'):
        assert not re.search(r'innerHTML|outerHTML|eval\s*\(|new Function|document\.write|fetch\s*\(',src.read_text()),'Unsafe or unexpected network/DOM operation'
    print(f'PASS: {len(paths)} public files, {len(pages)} pages, local links, anchors, content boundary, hashes, no inline scripts/styles or secret patterns.')
if __name__=='__main__':check()
