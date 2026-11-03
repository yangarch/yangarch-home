"""Read selected public DCinside/Naver posts without executing remote HTML."""
from datetime import date
from html.parser import HTMLParser
import http.client
import ipaddress
import re
import socket
import ssl
import threading
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

MAX_HTML = 5_000_000
MAX_IMAGE = 20_000_000
MAX_TOTAL = 100_000_000
VOID = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
DROP = {'script','style','noscript','template'}


class ImportProblem(ValueError):
    pass


def checked_url(url):
    if not isinstance(url,str) or len(url)>4096 or re.search(r'[\s\\\x00-\x1f\x7f]|%(?:00|0a|0d)',url,re.I):
        raise ImportProblem('올바른 HTTPS 글 주소를 입력해 주세요.')
    try:
        parsed = urlsplit(url)
        if parsed.scheme!='https' or not parsed.hostname or parsed.netloc!=parsed.hostname or parsed.username or parsed.password:
            raise ValueError()
    except ValueError:
        raise ImportProblem('포트나 로그인 정보가 없는 HTTPS 주소를 입력해 주세요.') from None
    return parsed


def normalize_source(url):
    parsed = checked_url(url.strip() if isinstance(url,str) else url)
    query = parse_qs(parsed.query)
    if parsed.hostname=='gall.dcinside.com' and parsed.path in {'/board/view/','/mgallery/board/view/','/mini/board/view/'}:
        gallery,number = query.get('id',[''])[0],query.get('no',[''])[0]
        if not re.fullmatch(r'[A-Za-z0-9_]{1,60}',gallery) or not re.fullmatch(r'\d{1,20}',number):
            raise ImportProblem('디시 글 주소의 갤러리와 글 번호를 확인해 주세요.')
        return 'https://gall.dcinside.com'+parsed.path+'?'+urlencode({'id':gallery,'no':number})
    if parsed.hostname in {'blog.naver.com','m.blog.naver.com'}:
        match = re.fullmatch(r'/([A-Za-z0-9_-]{1,60})/(\d{1,20})/?',parsed.path)
        if match:
            blog,number = match.groups()
        elif parsed.path in {'/PostView.naver','/PostView.nhn'}:
            blog,number = query.get('blogId',[''])[0],query.get('logNo',[''])[0]
        else:
            blog,number = '',''
        if re.fullmatch(r'[A-Za-z0-9_-]{1,60}',blog) and re.fullmatch(r'\d{1,20}',number):
            return f'https://blog.naver.com/{blog}/{number}'
    raise ImportProblem('디시 PC 게시글 주소 또는 네이버 블로그 글 주소를 입력해 주세요.')


def image_host(host, source):
    if urlsplit(source).hostname=='gall.dcinside.com':
        return bool(re.fullmatch(r'dcimg\d*\.dcinside\.(?:co\.kr|com)',host))
    return host in {'postfiles.pstatic.net','blogfiles.pstatic.net','mblogthumb-phinf.pstatic.net'}


def public_addresses(host):
    addresses = list(dict.fromkeys(info[4][0] for info in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)))
    if not addresses or any(not ipaddress.ip_address(address).is_global or ipaddress.ip_address(address).is_multicast for address in addresses):
        raise ImportProblem('내부 네트워크 주소에는 접근할 수 없습니다.')
    return sorted(addresses,key=lambda address:':' in address)


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout, context):
        super().__init__(host,timeout=timeout,context=context)
        self.address = address

    def connect(self):
        raw = socket.create_connection((self.address,443),self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw,server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


class Fetcher:
    """Pin validated public IPs; never send local cookies or follow arbitrary redirects."""
    def __init__(self, source):
        self.source = normalize_source(source)
        self.deadline = time.monotonic()+150
        self.total = 0
        self.lock = threading.Lock()
        self.context = ssl.create_default_context()

    def validate(self, url, kind):
        parsed = checked_url(url)
        if kind=='image':
            if not image_host(parsed.hostname,self.source):
                raise ImportProblem('허용하지 않은 사진 주소가 포함되어 있습니다.')
        elif normalize_source(url)!=self.source:
            raise ImportProblem('원문과 다른 글이나 외부 사이트로 이동할 수 없습니다.')
        return parsed

    def read(self, url, kind='html'):
        limit = MAX_IMAGE if kind=='image' else MAX_HTML
        try:
            for redirect in range(4):
                parsed = self.validate(url,kind)
                remaining = self.deadline-time.monotonic()
                if remaining<=0:
                    raise ImportProblem('가져오는 시간이 너무 길어졌습니다. 잠시 뒤 다시 시도해 주세요.')
                addresses = public_addresses(parsed.hostname)
                connection = PinnedHTTPS(parsed.hostname,addresses[0],min(15,remaining),self.context)
                try:
                    target = parsed.path or '/'
                    if parsed.query:target+='?'+parsed.query
                    connection.request('GET',target,headers={'User-Agent':'Mozilla/5.0 (compatible; LocalRecordImporter/1.0)','Accept':'image/*' if kind=='image' else 'text/html','Accept-Encoding':'identity','Referer':self.source})
                    response = connection.getresponse()
                    if response.status in {301,302,303,307,308}:
                        location = response.getheader('Location')
                        if not location or redirect==3:
                            raise ImportProblem('원문에서 이동하는 주소를 확인할 수 없습니다.')
                        url = urljoin(url,location)
                        continue
                    if response.status!=200:
                        raise ImportProblem(f'원문 또는 사진을 읽지 못했습니다(HTTP {response.status}). 로그인 없이 열리는 글인지 확인해 주세요.')
                    content_length = response.getheader('Content-Length')
                    if content_length and int(content_length)>limit:
                        raise ImportProblem('원문 또는 사진 파일이 너무 큽니다.')
                    if response.getheader('Content-Encoding','identity').lower() not in {'','identity'}:
                        raise ImportProblem('이 페이지의 전송 형식은 지원하지 않습니다.')
                    if kind!='image' and response.getheader('Content-Type','').split(';')[0].lower() not in {'text/html','application/xhtml+xml'}:
                        raise ImportProblem('본문 HTML을 읽을 수 없습니다.')
                    chunks,size = [],0
                    while True:
                        if time.monotonic()>self.deadline:
                            raise ImportProblem('가져오기 시간이 초과되었습니다.')
                        chunk = response.read(min(65536,limit-size+1))
                        if not chunk:break
                        size+=len(chunk)
                        with self.lock:
                            self.total+=len(chunk)
                            if self.total>MAX_TOTAL:raise ImportProblem('한 번에 가져올 수 있는 전체 용량을 넘었습니다(100MB).')
                        if size>limit:raise ImportProblem('원문 또는 사진 파일이 너무 큽니다.')
                        chunks.append(chunk)
                    raw = b''.join(chunks)
                    if kind=='image':return raw
                    charset = response.headers.get_content_charset() or 'utf-8'
                    if charset.lower() not in {'utf-8','utf8','euc-kr','cp949'}:
                        raise ImportProblem('지원하지 않는 본문 문자 형식입니다.')
                    return raw.decode(charset)
                finally:
                    connection.close()
        except ImportProblem:
            raise
        except (OSError,ValueError,http.client.HTTPException,UnicodeError):
            raise ImportProblem('원문 또는 사진에 연결하지 못했습니다. 잠시 뒤 다시 시도해 주세요.') from None


class Node:
    def __init__(self, tag='', attrs=()):
        self.tag,self.attrs,self.children = tag,dict(attrs),[]

    def has(self, cls):
        return cls in self.attrs.get('class','').split()

    def walk(self):
        if self.tag in DROP:return
        yield self
        for child in self.children:
            if isinstance(child,Node):yield from child.walk()

    def text(self):
        if self.tag in DROP:return ''
        return ''.join(child if isinstance(child,str) else child.text() for child in self.children)


class Tree(HTMLParser):
    def __init__(self, markup):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.count = 0
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        self.count+=1
        if self.count>100_000 or len(self.stack)>150:
            raise ImportProblem('본문의 구조가 너무 복잡합니다.')
        node = Node(tag,attrs)
        self.stack[-1].children.append(node)
        if tag not in VOID:self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack)-1,0,-1):
            if self.stack[index].tag==tag:
                self.stack=self.stack[:index]
                break

    def handle_data(self, text):
        self.stack[-1].children.append(text)


def selected(nodes, cls):
    return next((node for node in nodes if node.has(cls)),None)


def clean_text(text):
    return text.replace('\u200b','').replace('\ufeff','').replace('\xa0',' ')


def body_blocks(root, source):
    blocks,buffer = [],[]
    def boundary():
        if buffer and not buffer[-1].endswith('\n'):buffer.append('\n')
    def flush():
        text = clean_text(''.join(buffer)).strip()
        buffer.clear()
        if text:blocks.append(text)
    def visit(node):
        if isinstance(node,str):
            if node.isspace() and '\n' in node:return
            buffer.append(node)
            return
        if node.tag in DROP:return
        if node.tag in {'iframe','video','audio','object','embed','table','svg','canvas'}:
            raise ImportProblem('동영상·표 등 자동 보관을 지원하지 않는 내용이 있습니다. 이 글은 직접 옮겨 주세요.')
        if node.tag=='img':
            flush()
            url = node.attrs.get('data-original') or node.attrs.get('data-lazy-src') or node.attrs.get('src')
            if not url:raise ImportProblem('사진 주소를 찾지 못했습니다.')
            url = urljoin(source,url)
            parsed = checked_url(url)
            if not image_host(parsed.hostname,source):
                raise ImportProblem('지원하지 않는 사진 주소가 있습니다. 누락을 막기 위해 가져오기를 중단했습니다.')
            blocks.append({'type':'remote-image','url':url})
            return
        if node.tag=='br':buffer.append('\n');return
        is_block = node.tag in {'div','p','li','blockquote','h1','h2','h3','h4','section'}
        if is_block:boundary()
        for child in node.children:visit(child)
        if is_block:boundary()
    visit(root)
    flush()
    return blocks


def parse_article(markup, source):
    nodes = list(Tree(markup).root.walk())
    if urlsplit(source).hostname=='gall.dcinside.com':
        title_node,body = selected(nodes,'title_subject'),selected(nodes,'write_div')
        stamp = selected(nodes,'gall_date')
        stamp = (stamp.attrs.get('title') or stamp.text()) if stamp else ''
        label = '디시인사이드'
    else:
        title_node,body = selected(nodes,'se-title-text'),selected(nodes,'se-main-container')
        stamp = selected(nodes,'se_publishDate')
        stamp = stamp.text() if stamp else ''
        label = '네이버 블로그'
        if body:
            supported = {'se-text','se-image','se-imageGroup','se-quotation','se-sectionTitle','se-horizontalLine'}
            for node in body.walk():
                if node.has('se-component') and not supported.intersection(node.attrs.get('class','').split()):
                    raise ImportProblem('지원하지 않는 네이버 본문 항목이 있습니다. 누락을 막기 위해 가져오기를 중단했습니다.')
    if title_node is None or body is None:
        raise ImportProblem('제목이나 본문을 찾지 못했습니다. 비공개·삭제된 글이거나 지원하지 않는 글 형식일 수 있습니다.')
    numbers = re.match(r'\s*(\d{4})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})',stamp)
    if not numbers:raise ImportProblem('원문 작성일을 확인하지 못했습니다.')
    try:written = date(*(int(value) for value in numbers.groups())).isoformat()
    except ValueError:raise ImportProblem('원문 작성일이 올바르지 않습니다.') from None
    blocks = body_blocks(body,source)
    images = sum(isinstance(block,dict) for block in blocks)
    if not blocks or len(blocks)>300 or images>100:
        raise ImportProblem('본문이 비어 있거나 가져올 수 있는 분량을 넘었습니다(사진 100장).')
    title = clean_text(title_node.text()).strip()
    if not title or len(title)>200:raise ImportProblem('글 제목을 확인해 주세요.')
    return {'title':title,'date':written,'source':{'label':label,'url':source},'body':blocks}


def read_article(url, fetcher=None):
    source = normalize_source(url)
    fetcher = fetcher or Fetcher(source)
    markup = fetcher.read(source)
    if urlsplit(source).hostname=='blog.naver.com':
        frame = next((node for node in Tree(markup).root.walk() if node.tag=='iframe' and node.attrs.get('id')=='mainFrame'),None)
        if frame:
            frame_url = urljoin(source,frame.attrs.get('src',''))
            if normalize_source(frame_url)!=source:raise ImportProblem('원문과 다른 본문 프레임에는 접근할 수 없습니다.')
            markup = fetcher.read(frame_url)
    return parse_article(markup,source),fetcher
