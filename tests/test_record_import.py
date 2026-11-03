"""Parser fidelity, outbound network boundaries and transactional draft import."""
import copy
import importlib.util
import io
import json
import ssl
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from record_import import Fetcher, ImportProblem, MAX_HTML, PinnedHTTPS, normalize_source, parse_article, public_addresses, read_article
HAS_PILLOW=importlib.util.find_spec('PIL') is not None
if HAS_PILLOW:
    from PIL import Image
    from editor import Store
    from record_media import clean_jpeg

DC='https://gall.dcinside.com/mgallery/board/view/?id=cycle&no=123'
NAVER='https://blog.naver.com/test_writer/123456'
DC_IMAGE='https://dcimg4.dcinside.co.kr/viewimage.php?id=test&no=photo'
NAVER_IMAGE='https://postfiles.pstatic.net/example/photo.jpg?type=w773'
DC_HTML=f'''<span class="title_subject">제목 &amp; 원문</span><span class="gall_date" title="2026-10-21 11:35:47">작성일</span>
<div class="write_div"><img src="{DC_IMAGE}"><div><span>원문 </span><b>그대로</b><br>다음 줄</div><script>alert('ignore')</script><img src="https://nstatic.dcinside.com/loading.gif" data-original="{DC_IMAGE}"><p>마지막 문단</p></div>
<aside>댓글과 광고<img src="https://evil.example/ad.jpg"></aside>'''
NAVER_HTML=f'''<div class="se-title-text">네이버 제목</div><span class="se_publishDate">2026. 9. 14. 16:46</span>
<div class="se-main-container"><div class="se-component se-image"><img src="https://postfiles.pstatic.net/example/photo.jpg?type=w80_blur" data-lazy-src="{NAVER_IMAGE}"></div>
<div class="se-component se-text"><p class="se-text-paragraph"><span>첫 줄</span><br>둘째 줄</p><p class="se-text-paragraph">세 번째 줄</p></div></div>'''


class FakeFetcher:
    def __init__(self, markup=DC_HTML, broken=False):
        self.markup,self.broken,self.calls=markup,broken,[]

    def read(self,url,kind='html'):
        self.calls.append((url,kind))
        if kind!='image':return self.markup
        if self.broken:return b'not an image'
        stream=io.BytesIO()
        exif=Image.Exif();exif[271]='PRIVATE_TEST_METADATA'
        Image.new('RGB',(80,60),'#294d3b').save(stream,'JPEG',exif=exif)
        return stream.getvalue()


class ParserTest(unittest.TestCase):
    def test_normalizes_only_supported_post_urls(self):
        self.assertEqual(normalize_source(DC+'&page=3&s_keyword=name#bottom'),DC)
        self.assertEqual(normalize_source('https://m.blog.naver.com/test_writer/123456?tracking=1'),NAVER)
        self.assertEqual(normalize_source('https://blog.naver.com/PostView.naver?blogId=test_writer&logNo=123456&widgetTypeCall=true'),NAVER)
        for url in ['http://blog.naver.com/test_writer/123456','https://blog.naver.com@evil.example/test_writer/123456',
                    'https://evil.example@blog.naver.com/test_writer/123456','https://blog.naver.com:443/test_writer/123456',
                    'https://blog.naver.com.evil.example/test_writer/123456','https://127.0.0.1/test_writer/123456',
                    'file:///etc/passwd','https://blog.naver.com/','https://gall.dcinside.com/mgallery/board/lists/?id=cycle',
                    'https://blog.naver.com/test_writer/123456%0a','https://blog.naver.com\\@evil.example/test_writer/123456']:
            with self.subTest(url=url),self.assertRaises(ImportProblem):normalize_source(url)

    def test_dc_order_linebreaks_and_lazy_photos_exclude_other_content(self):
        entry=parse_article(DC_HTML,DC)
        self.assertEqual(entry['title'],'제목 & 원문')
        self.assertEqual(entry['date'],'2026-10-21')
        self.assertEqual([isinstance(block,dict) for block in entry['body']],[True,False,True,False])
        self.assertEqual(entry['body'][1],'원문 그대로\n다음 줄')
        self.assertEqual(entry['body'][2]['url'],DC_IMAGE)
        self.assertEqual(entry['body'][3],'마지막 문단')
        self.assertNotIn('댓글',str(entry))
        self.assertNotIn('alert',str(entry))

    def test_naver_follows_the_observed_frame_and_uses_full_display_images(self):
        frame='/PostView.naver?blogId=test_writer&logNo=123456'
        class FrameFetcher:
            def __init__(self):self.calls=[]
            def read(self,url,kind='html'):
                self.calls.append(url)
                return '<iframe id="mainFrame" src="'+frame+'"></iframe>' if len(self.calls)==1 else NAVER_HTML
        fetcher=FrameFetcher()
        entry,_=read_article(NAVER,fetcher)
        self.assertEqual(fetcher.calls,[NAVER,'https://blog.naver.com'+frame])
        self.assertEqual(entry['date'],'2026-09-14')
        self.assertEqual(entry['body'][0]['url'],NAVER_IMAGE)
        self.assertEqual(entry['body'][1],'첫 줄\n둘째 줄\n세 번째 줄')

    def test_wrong_frames_missing_dates_and_unsupported_media_fail_explicitly(self):
        bad=FakeFetcher('<iframe id="mainFrame" src="https://127.0.0.1/private"></iframe>')
        with self.assertRaises(ImportProblem):read_article(NAVER,bad)
        self.assertEqual(len(bad.calls),1)
        for markup,source in [(DC_HTML.replace('2026-10-21','2026-02-30'),DC),
                              (DC_HTML.replace('<p>마지막 문단</p>','<video src="video.mp4"></video>'),DC),
                              (NAVER_HTML.replace('se-component se-text','se-component se-video'),NAVER),
                              ('<h1>로그인해 주세요</h1>',NAVER),
                              (DC_HTML.replace(DC_IMAGE,'https://127.0.0.1/private'),DC)]:
            with self.subTest(source=source),self.assertRaises(ImportProblem):parse_article(markup,source)

    def test_dns_rejects_private_mixed_and_multicast_addresses(self):
        for addresses in [['127.0.0.1'],['169.254.169.254'],['10.0.0.1'],['::1'],['::ffff:127.0.0.1'],['224.0.0.1'],['8.8.8.8','192.168.0.1']]:
            values=[(2,1,6,'',(address,443)) for address in addresses]
            with patch('record_import.socket.getaddrinfo',return_value=values),self.assertRaises(ImportProblem):public_addresses('blog.naver.com')
        with patch('record_import.socket.getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443))]):
            self.assertEqual(public_addresses('blog.naver.com'),['8.8.8.8'])

    def test_redirects_and_payload_limits_are_checked_before_following(self):
        class Response:
            status=302
            def getheader(self,name,default=None):return 'https://127.0.0.1/private' if name=='Location' else default
        class Connection:
            def __init__(self,*args):self.headers=None
            def request(self,*args,**kwargs):self.headers=kwargs['headers']
            def getresponse(self):return Response()
            def close(self):pass
        with patch('record_import.public_addresses',return_value=['8.8.8.8']),patch('record_import.PinnedHTTPS',side_effect=Connection) as connections:
            with self.assertRaises(ImportProblem):Fetcher(DC).read(DC)
            self.assertEqual(connections.call_count,1)
        class LargeResponse(Response):
            status=200
            def getheader(self,name,default=None):return str(MAX_HTML+1) if name=='Content-Length' else default
        with patch('record_import.public_addresses',return_value=['8.8.8.8']),patch.object(Connection,'getresponse',return_value=LargeResponse()),patch('record_import.PinnedHTTPS',side_effect=Connection):
            with self.assertRaises(ImportProblem):Fetcher(DC).read(DC)

    def test_tls_uses_verified_hostname_and_the_validated_ip(self):
        context=ssl.create_default_context()
        raw,tls=Mock(),Mock()
        with patch('record_import.socket.create_connection',return_value=raw) as connect,patch.object(context,'wrap_socket',return_value=tls) as wrap:
            connection=PinnedHTTPS('postfiles.pstatic.net','8.8.8.8',5,context)
            connection.connect()
            connect.assert_called_once_with(('8.8.8.8',443),5)
            wrap.assert_called_once_with(raw,server_hostname='postfiles.pstatic.net')
            self.assertTrue(context.check_hostname)
            self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)


@unittest.skipUnless(HAS_PILLOW,'Run in the editor virtual environment')
class ImportStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='import-store-')
        self.root=Path(self.temp.name).resolve()
        (self.root/'content').mkdir()
        (self.root/'content/records.json').write_text('{"entries":[]}')
        (self.root/'dist').mkdir();(self.root/'dist/index.html').write_text('unchanged')
        self.store=Store(self.root)

    def tearDown(self):self.temp.cleanup()

    def test_import_creates_private_complete_draft_and_keeps_public_files(self):
        fetcher=FakeFetcher()
        result=self.store.import_url(DC,'cycling',fetcher)
        draft=result['draft'];entry=draft['entry']
        self.assertFalse(result['duplicate'])
        self.assertEqual(entry['title'],'제목 & 원문')
        self.assertEqual(entry['body'][1],'원문 그대로\n다음 줄')
        self.assertEqual(entry['source']['url'],DC)
        self.assertEqual(draft['import_info']['image_count'],2)
        self.assertEqual(len(list((self.store.post_dir(entry['id'])/'originals').glob('*'))),2)
        for block in entry['body']:
            if isinstance(block,dict):
                raw=self.store.media_file(block['src']).read_bytes()
                self.assertTrue(clean_jpeg(raw));self.assertNotIn(b'PRIVATE_TEST_METADATA',raw)
        self.assertEqual(self.store.published(),[])
        self.assertEqual((self.root/'dist/index.html').read_text(),'unchanged')
        self.assertEqual((self.store.post_dir(entry['id'])/'draft.json').stat().st_mode & 0o777,0o600)

    def test_duplicate_import_preserves_manual_edits_and_import_metadata(self):
        draft=self.store.import_url(DC,'cycling',FakeFetcher())['draft']
        entry=copy.deepcopy(draft['entry']);entry['title']='수정한 제목'
        self.store.save(entry['id'],entry,draft['revision'])
        unused=FakeFetcher()
        result=self.store.import_url(DC+'&page=99','making',unused)
        self.assertTrue(result['duplicate'])
        self.assertEqual(unused.calls,[])
        self.assertEqual(result['draft']['entry']['title'],'수정한 제목')
        self.assertEqual(len(self.store.listing()),1)

    def test_broken_photo_leaves_no_partial_draft_and_preserves_existing_work(self):
        existing=self.store.create()
        with self.assertRaises(ImportProblem):self.store.import_url(DC,'cycling',FakeFetcher(broken=True))
        self.assertEqual(self.store.listing()[0]['id'],existing['entry']['id'])
        self.assertEqual(len(self.store.listing()),1)
        self.assertEqual(list((self.store.state/'imports').iterdir()),[])
        self.assertEqual(self.store.published(),[])

    def test_naver_source_and_text_are_kept_without_blur_images(self):
        draft=self.store.import_url(NAVER,'making',FakeFetcher(NAVER_HTML))['draft']
        self.assertEqual(draft['entry']['date'],'2026-09-14')
        self.assertEqual(draft['entry']['source']['label'],'네이버 블로그')
        self.assertEqual(draft['entry']['body'][1],'첫 줄\n둘째 줄\n세 번째 줄')
        self.assertEqual(draft['import_info']['image_count'],1)


if __name__=='__main__':unittest.main()
