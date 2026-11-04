"""Exact public service destinations reviewed for this homepage."""
from urllib.parse import urlsplit
import re

SERVICE_URLS = frozenset({
    'https://report.yangarch.net/',
    'https://fingu.yangarch.net/',
    'https://poogiegram.poogie.one/',
    'https://archgrab.yangarch.net/',
    'http://njc.yangarch.net/',
    'https://cyberfondo.yangarch.net/',
    'https://cyber-topgoal.yangarch.net/',
})


def is_record_source(url):
    """Only HTTPS originals on the owner's existing blog/community platforms."""
    if not isinstance(url, str) or re.search(r'[\s\\\x00-\x1f\x7f]', url):
        return False
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ''
    except ValueError:
        return False
    known = host in {'blog.naver.com', 'm.blog.naver.com', 'gall.dcinside.com', 'm.dcinside.com'} or re.fullmatch(r'[a-z0-9-]+\.tistory\.com', host)
    return bool(known and parsed.scheme == 'https' and parsed.netloc == host)


def is_public_link(url):
    if url in SERVICE_URLS:
        return True
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    return parsed.scheme == 'https' and parsed.netloc in {'github.com', 'www.linkedin.com'}
