#!/usr/bin/env python3
"""Loopback-only record editor. Preparing a release never deploys it."""
import argparse
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
import hashlib
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError
from build import record_page
from record_media import clean_jpeg, image_path, media_paths
from records import CATEGORIES, validate_records
from record_import import ImportProblem, normalize_source, read_article
from publish import Publisher
from analytics_client import Statistics

ROOT = Path(__file__).resolve().parents[1]
MAX_UPLOAD = 20_000_000
Image.MAX_IMAGE_PIXELS = 36_000_000
SLUG = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')


def encoded(data):
    return (json.dumps(data, ensure_ascii=False, indent=2)+'\n').encode()


def private_dir(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or path.resolve() != path.absolute():
        raise ValueError('편집기 저장 경로에 심볼릭 링크를 사용할 수 없습니다.')
    path.chmod(0o700)


def atomic_write(path, data):
    private_dir(path.parent)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temp = Path(handle.name)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temp, path)
        path.chmod(0o600)
    finally:
        temp.unlink(missing_ok=True)


def digest(entry):
    return hashlib.sha256(encoded(entry)).hexdigest()


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, root=ROOT, state=None):
        self.root = root
        self.state = state or root/'.local/editor'
        private_dir(self.state)
        private_dir(self.state/'posts')
        # Existing selected records become editable copies; public data stays untouched.
        for entry in self.published():
            if not self.post_dir(entry['id']).exists():
                self.write_draft(entry)

    def published(self):
        return validate_records(json.loads((self.root/'content/records.json').read_text()))

    def post_dir(self, slug):
        if not isinstance(slug,str) or not SLUG.fullmatch(slug):
            raise ValueError('잘못된 글 주소입니다.')
        return self.state/'posts'/slug

    def load(self, slug):
        path = self.post_dir(slug)/'draft.json'
        if not path.is_file() or path.is_symlink() or path.resolve()!=path.absolute():
            raise FileNotFoundError('글을 찾을 수 없습니다.')
        return json.loads(path.read_text())

    def write_draft(self, entry, import_info=None):
        draft = {'entry':entry, 'revision':secrets.token_hex(16), 'updated_at':datetime.now(timezone.utc).isoformat()}
        if import_info is None and (self.post_dir(entry['id'])/'draft.json').exists():
            import_info = self.load(entry['id']).get('import_info')
        if import_info:draft['import_info']=import_info
        atomic_write(self.post_dir(entry['id'])/'draft.json', encoded(draft))
        return draft

    def listing(self):
        selected = {entry['id']:entry for entry in self.published()}
        result = []
        for path in (self.state/'posts').glob('*/draft.json'):
            if path.is_symlink() or path.parent.is_symlink():
                continue
            draft = self.load(path.parent.name)
            entry = draft['entry']
            public = selected.get(entry['id'])
            status = '초안' if public is None else '공개본 준비됨' if digest(public)==digest(self.for_public(entry)) else '수정 중'
            result.append({'id':entry['id'], 'title':entry['title'] or '제목 없는 글', 'date':entry['date'], 'category':entry['category'], 'status':status, 'updated_at':draft['updated_at']})
        return sorted(result, key=lambda item:item['updated_at'], reverse=True)

    def create(self):
        return self.write_draft({'id':'record-'+secrets.token_hex(6), 'category':'cycling', 'title':'', 'date':date.today().isoformat(), 'body':['']})

    def save(self, slug, entry, revision):
        current = self.load(slug)
        if revision != current['revision']:
            raise Conflict('다른 창에서 저장한 내용이 있습니다. 글을 다시 열어 확인해 주세요.')
        if not isinstance(entry,dict) or entry.get('id')!=slug:
            raise ValueError('글 주소를 변경할 수 없습니다.')
        validate_records({'entries':[entry]}, draft=True)
        for url in media_paths([entry]):
            self.media_file(url)
        history = self.post_dir(slug)/'history'/(current['revision']+'.json')
        atomic_write(history, encoded(current))
        return self.write_draft(entry)

    @staticmethod
    def for_public(entry):
        entry = copy.deepcopy(entry)
        entry['body'] = [block for block in entry['body'] if not isinstance(block,str) or block.strip()]
        return entry

    def preview(self, slug):
        entry = self.for_public(self.load(slug)['entry'])
        entry['title'] = entry['title'].strip() or '제목 없는 글'
        entry['body'] = entry['body'] or ['본문을 작성하면 여기에 표시됩니다.']
        validate_records({'entries':[entry]})
        markup = record_page(entry).replace('<script src="/assets/site.js" defer></script>','').replace('<script src="/assets/analytics.js" defer></script>','')
        # The draft is a reading preview; links must not leave the preview frame.
        markup = re.sub(r'(<a\b[^>]*?) href="[^"]*"',r'\1',markup)
        return markup.encode()

    def media_file(self, url):
        rel = image_path(url)
        slug, filename = rel.split('/')
        candidates = [self.post_dir(slug)/'media'/filename, self.root/'content/record-media'/rel]
        for path in candidates:
            if path.is_file() and not path.is_symlink() and path.resolve()==path.absolute():
                return path
        raise FileNotFoundError('사진을 찾을 수 없습니다.')

    def upload(self, slug, raw):
        self.load(slug)
        return self.write_image(self.post_dir(slug),slug,raw)

    @staticmethod
    def write_image(folder, slug, raw):
        if not raw or len(raw)>MAX_UPLOAD:
            raise ValueError('사진은 한 장에 20MB까지 추가할 수 있습니다.')
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as probe:
                    if probe.format not in {'JPEG','PNG','WEBP'}:
                        raise ValueError('JPG, PNG, WebP 사진을 선택해 주세요.')
                    if getattr(probe,'is_animated',False):
                        raise ValueError('움직이는 사진은 아직 지원하지 않습니다. 첫 장만 저장하지 않고 가져오기를 중단했습니다.')
                    probe.verify()
                with Image.open(io.BytesIO(raw)) as original:
                    image = ImageOps.exif_transpose(original).convert('RGBA')
                    image.thumbnail((2048,2048), Image.Resampling.LANCZOS)
                    clean = Image.new('RGB', image.size, 'white')
                    clean.paste(image, mask=image.getchannel('A'))
                    output = io.BytesIO()
                    clean.save(output, format='JPEG', quality=88, optimize=True)
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
            raise ValueError('읽을 수 없거나 해상도가 너무 큰 사진입니다.') from error
        converted = output.getvalue()
        if not clean_jpeg(converted):
            raise ValueError('공개용 사진을 만들지 못했습니다.')
        photo_id = secrets.token_hex(16)
        atomic_write(folder/'originals'/(photo_id+'.original'), raw)
        atomic_write(folder/'media'/(photo_id+'.jpg'), converted)
        return {'type':'image', 'src':f'/media/records/{slug}/{photo_id}.jpg', 'alt':'', 'caption':''}

    def import_url(self, url, category, fetcher=None):
        source = normalize_source(url)
        if not isinstance(category,str) or category not in CATEGORIES:
            raise ImportProblem('기록 분류를 선택해 주세요.')
        for post in self.listing():
            existing = self.load(post['id'])
            if existing.get('import_info',{}).get('source_url')==source:
                return {'draft':existing,'duplicate':True}
        article,fetcher = read_article(source,fetcher)
        slug = 'record-'+secrets.token_hex(6)
        private_dir(self.state/'imports')
        with tempfile.TemporaryDirectory(prefix='post-',dir=self.state/'imports') as temp:
            folder = Path(temp)/slug
            private_dir(folder)
            photos = [block for block in article['body'] if isinstance(block,dict)]
            def download(pair):
                index,block = pair
                try:
                    raw = fetcher.read(block['url'],'image')
                    return self.write_image(folder,slug,raw)
                except (ValueError,OSError) as error:
                    raise ImportProblem(f'사진 {len(photos)}장 중 {index}번째 사진을 가져오지 못했습니다. '+str(error)+' 새 초안은 만들지 않았습니다.') from None
            pool = ThreadPoolExecutor(max_workers=2)
            try:
                images = iter(pool.map(download,enumerate(photos,1)))
                body = [next(images) if isinstance(block,dict) else block for block in article['body']]
            finally:
                pool.shutdown(wait=True,cancel_futures=True)
            entry = {'id':slug,'category':category,**article,'body':body}
            validate_records({'entries':[entry]})
            info = {'source_url':source,'image_count':len(photos),'fetched_at':datetime.now(timezone.utc).isoformat()}
            draft = {'entry':entry,'revision':secrets.token_hex(16),'updated_at':info['fetched_at'],'import_info':info}
            atomic_write(folder/'draft.json',encoded(draft))
            atomic_write(folder/'import-source.json',encoded({'source':source,'images':[{'source_url':original['url'],'local_src':local['src']} for original,local in zip(photos,[block for block in body if isinstance(block,dict)])]}))
            destination = self.post_dir(slug)
            if destination.exists():raise ImportProblem('새 초안의 주소가 겹쳤습니다. 다시 시도해 주세요.')
            os.replace(folder,destination)
        return {'draft':draft,'duplicate':False}

    def prepare(self, slug, revision):
        draft = self.load(slug)
        if revision != draft['revision']:
            raise Conflict('먼저 현재 내용을 초안으로 저장해 주세요.')
        entry = self.for_public(draft['entry'])
        validate_records({'entries':[entry]})
        entries = [old for old in self.published() if old['id']!=slug]+[entry]
        validate_records({'entries':entries})
        private_dir(self.state/'staging')
        with tempfile.TemporaryDirectory(prefix='release-', dir=self.state/'staging') as temp:
            stage = Path(temp)
            for folder in ('assets','scripts','deploy'):
                shutil.copytree(self.root/folder,stage/folder,ignore=shutil.ignore_patterns('__pycache__'))
            (stage/'tests').mkdir()
            shutil.copyfile(self.root/'tests/nginx_integration.py',stage/'tests/nginx_integration.py')
            (stage/'content').mkdir()
            shutil.copyfile(self.root/'content/public.json',stage/'content/public.json')
            for name in ('privacy.example.json','privacy.local.json'):
                source=self.root/'content'/name
                if source.exists():
                    if source.is_symlink():raise ValueError('Content policy cannot be a symlink')
                    shutil.copyfile(source,stage/'content'/name)
            (stage/'content/records.json').write_bytes(encoded({'entries':entries}))
            (stage/'content/record-media').mkdir()
            for url in media_paths(entries):
                target = stage/'content/record-media'/image_path(url)
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(self.media_file(url),target)
            for script in ('build.py','nginx_config.py','check.py','package.py'):
                result = subprocess.run([sys.executable,str(stage/'scripts'/script)],cwd=stage,capture_output=True,text=True,timeout=60)
                if result.returncode:
                    raise ValueError('공개본 검사에 실패했습니다. 글과 사진을 확인해 주세요. 초안과 기존 공개본은 보관됩니다.')
            self.commit(stage)
        return {'message':'공개본을 준비했습니다. 서버에는 아직 반영하지 않았습니다.', 'path':'/records/entry/'+slug+'/'}

    def commit(self, stage):
        targets = ['content/records.json','content/record-media','dist','artifacts/manifest.json','artifacts/yangarch-home.tar.gz','artifacts/release.json','deploy/routes.conf','deploy/yangarch.net.conf']
        backup = stage/'previous'
        backup.mkdir()
        moved,installed = [],[]
        try:
            for rel in targets:
                target = self.root/rel
                target.parent.mkdir(parents=True,exist_ok=True)
                old = backup/rel
                old.parent.mkdir(parents=True,exist_ok=True)
                if target.is_symlink():
                    raise ValueError('공개 파일 경로에 심볼릭 링크를 사용할 수 없습니다.')
                if target.exists():
                    os.replace(target,old)
                    moved.append((old,target))
                os.replace(stage/rel,target)
                installed.append(target)
        except Exception:
            for target in reversed(installed):
                shutil.rmtree(target) if target.is_dir() else target.unlink()
            for old,target in reversed(moved):
                os.replace(old,target)
            raise


class LocalServer(HTTPServer):
    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(15)
        return connection,address


class Handler(BaseHTTPRequestHandler):
    server_version = 'LocalEditor'
    def log_message(self, *_):
        pass

    def send(self, status, body, mime='application/json; charset=utf-8', preview=False, cookie=False):
        self.send_response(status)
        self.send_header('Content-Type',mime)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('X-Robots-Tag','noindex, nofollow')
        self.send_header('X-Frame-Options','SAMEORIGIN' if preview else 'DENY')
        execution = "'none'" if preview else "'self'"
        self.send_header('Content-Security-Policy',f"default-src 'none'; script-src {execution}; style-src 'self'; img-src 'self'; connect-src {execution}; frame-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors "+("'self'" if preview else "'none'"))
        if cookie:
            self.send_header('Set-Cookie',f'{self.server.cookie_name}={self.server.session}; HttpOnly; SameSite=Strict; Path=/')
        self.end_headers()
        self.wfile.write(body)

    def json(self, data, status=200):
        self.send(status,encoded(data))

    def allowed(self, mutate=False, bootstrap=False):
        if self.headers.get('Host')!=self.server.host:
            self.json({'error':'허용하지 않은 호스트입니다.'},403)
            return False
        origin = self.headers.get('Origin')
        if self.headers.get('Sec-Fetch-Site')=='cross-site' or origin not in (None,self.server.origin):
            self.json({'error':'다른 사이트에서 접근할 수 없습니다.'},403)
            return False
        if bootstrap:
            return True
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie',''))
        except Exception:
            pass
        value = cookie.get(self.server.cookie_name)
        if not value or not secrets.compare_digest(value.value,self.server.session):
            self.json({'error':'편집기 첫 화면을 다시 열어 주세요.'},403)
            return False
        if mutate and (origin!=self.server.origin or not secrets.compare_digest(self.headers.get('X-Editor-Token',''),self.server.token)):
            self.json({'error':'저장 요청을 확인할 수 없습니다. 화면을 다시 열어 주세요.'},403)
            return False
        return True

    def do_GET(self):
        path = urlsplit(self.path).path
        if not self.allowed(bootstrap=path in {'/','/editor.css','/editor.js'}):
            return
        try:
            if path in {'/','/editor.css','/editor.js'}:
                filename,mime = {'/':('index.html','text/html; charset=utf-8'),'/editor.css':('editor.css','text/css; charset=utf-8'),'/editor.js':('editor.js','application/javascript; charset=utf-8')}[path]
                self.send(200,(self.server.store.root/'editor'/filename).read_bytes(),mime,cookie=path=='/')
            elif path=='/api/session':
                self.json({'token':self.server.token,'categories':CATEGORIES})
            elif path=='/api/posts':
                self.json(self.server.store.listing())
            elif path=='/api/publish':
                self.json(self.server.publisher.summary())
            elif path=='/api/publish/job':
                self.json({'job':self.server.publisher.job})
            elif re.fullmatch(r'/api/posts/[a-z0-9-]+',path):
                self.json(self.server.store.load(path.rsplit('/',1)[1]))
            elif re.fullmatch(r'/preview/[a-z0-9-]+/',path):
                self.send(200,self.server.store.preview(path.split('/')[2]),'text/html; charset=utf-8',preview=True)
            elif path.startswith('/media/records/'):
                self.send(200,self.server.store.media_file(path).read_bytes(),'image/jpeg',preview=True)
            elif path=='/assets/site.css':
                self.send(200,(self.server.store.root/'assets/site.css').read_bytes(),'text/css; charset=utf-8',preview=True)
            elif path=='/assets/site.js':
                self.send(200,(self.server.store.root/'assets/site.js').read_bytes(),'application/javascript; charset=utf-8',preview=True)
            elif path=='/assets/favicon.svg':
                self.send(200,(self.server.store.root/'assets/favicon.svg').read_bytes(),'image/svg+xml',preview=True)
            else:
                self.json({'error':'페이지를 찾을 수 없습니다.'},404)
        except (ValueError,FileNotFoundError) as error:
            self.json({'error':str(error)},400 if isinstance(error,ValueError) else 404)

    def do_POST(self):
        if not self.allowed(mutate=True):
            return
        try:
            path = urlsplit(self.path).path
            image = bool(re.fullmatch(r'/api/posts/[a-z0-9-]+/images',path))
            limit = MAX_UPLOAD if image else 2_000_000
            length = int(self.headers.get('Content-Length','-1'))
            if self.headers.get('Transfer-Encoding') or not 0<=length<=limit:
                self.json({'error':'파일 또는 본문의 크기가 너무 큽니다.'},413)
                self.close_connection=True
                return
            raw = self.rfile.read(length)
            if len(raw)!=length:
                raise ValueError('요청을 모두 읽지 못했습니다.')
            if image:
                self.json(self.server.store.upload(path.split('/')[3],raw),201)
                return
            if self.headers.get('Content-Type')!='application/json':
                raise ValueError('잘못된 저장 형식입니다.')
            data = json.loads(raw)
            if path=='/api/posts':
                self.json(self.server.store.create(),201)
            elif path=='/api/import':
                self.json(self.server.store.import_url(data['url'],data['category']),201)
            elif path=='/api/statistics/report':
                if data!={}:raise ValueError('잘못된 통계 조회 요청입니다.')
                self.json(self.server.statistics.report())
            elif path=='/api/statistics/open':
                if data!={}:raise ValueError('잘못된 통계 연결 요청입니다.')
                self.json(self.server.statistics.open_admin())
            elif path=='/api/publish/check':
                if data!={}:raise ValueError('잘못된 연결 확인 요청입니다.')
                self.json(self.server.publisher.check_connection())
            elif path=='/api/publish':
                if not isinstance(data,dict) or set(data)!={'release_id'}:raise ValueError('배포할 공개본을 다시 확인해 주세요.')
                self.json(self.server.publisher.start(data['release_id']),202)
            elif re.fullmatch(r'/api/posts/[a-z0-9-]+/save',path):
                self.json(self.server.store.save(path.split('/')[3],data['entry'],data['revision']))
            elif re.fullmatch(r'/api/posts/[a-z0-9-]+/prepare',path):
                self.json(self.server.store.prepare(path.split('/')[3],data['revision']))
            else:
                self.json({'error':'기능을 찾을 수 없습니다.'},404)
        except Conflict as error:
            self.json({'error':str(error)},409)
        except (ValueError,KeyError,TypeError,FileNotFoundError) as error:
            self.json({'error':str(error)},400)
        except Exception:
            self.json({'error':'작업을 마치지 못했습니다. 초안과 기존 공개본은 보관됩니다.'},500)

    def do_OPTIONS(self):
        self.json({'error':'허용하지 않은 요청입니다.'},405)


def make_server(root=ROOT, port=4180, state=None):
    server = LocalServer(('127.0.0.1',port),Handler)
    server.store = Store(root,state)
    server.publisher = Publisher(root)
    server.statistics = Statistics(root)
    server.host = '127.0.0.1:'+str(server.server_port)
    server.origin = 'http://'+server.host
    server.cookie_name = 'record_editor_'+str(server.server_port)
    server.session = secrets.token_hex(32)
    server.token = secrets.token_hex(32)
    server.timeout = 15
    return server


if __name__=='__main__':
    parser = argparse.ArgumentParser(description='맥에서만 사용하는 기록 편집기')
    parser.add_argument('--port',type=int,default=4180)
    parser.add_argument('--open',action='store_true')
    args = parser.parse_args()
    server = make_server(port=args.port)
    print('기록 편집기: '+server.origin,flush=True)
    print('초안은 이 맥에 저장됩니다. 공개본 준비는 서버 배포와 별개입니다.',flush=True)
    if args.open:
        import webbrowser
        webbrowser.open(server.origin)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.statistics.close()
        server.server_close()
