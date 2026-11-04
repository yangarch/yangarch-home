"""Bounded static-only publishing protocol shared by Mac and trusted receiver."""
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re

PROTOCOL = 1
HEADER_LIMIT = 1_000_000
TOTAL_LIMIT = 256_000_000
FILE_LIMIT = 2000
REQUIRED = {'index.html','assets/site.css','assets/site.js','assets/favicon.svg','robots.txt','sitemap.xml','records/index.html','records/cycling/index.html','records/swimming/index.html','records/making/index.html'}
SLUG = r'[a-z0-9]+(?:-[a-z0-9]+)*'
PUBLIC_FILE = re.compile(r'(?:index\.html|assets/(?:site\.css|site\.js|favicon\.svg)|robots\.txt|sitemap\.xml|projects/[a-z-]{1,64}/index\.html|records/(?:index\.html|(?:cycling|swimming|making)/index\.html|entry/'+SLUG+r'/index\.html)|media/records/'+SLUG+r'/[a-f0-9]{32}\.jpg)')
HASH = re.compile(r'[a-f0-9]{64}')


def canonical(data):
    return json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()


def identifier(manifest):
    return hashlib.sha256(canonical(manifest)).hexdigest()


def unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('Duplicate protocol key')
        result[key]=value
    return result


def decode(raw):
    return json.loads(raw,object_pairs_hook=unique_object)


def route_map(files):
    routes={}
    for name in files:
        if not isinstance(name,str) or len(name)>240 or not PUBLIC_FILE.fullmatch(name):
            raise ValueError('File outside the public path allowlist')
        if name=='index.html':routes.update({'/':name,'/index.html':name})
        elif name.endswith('/index.html'):routes['/'+name[:-10]]=name
        else:routes['/'+name]=name
    return routes


def validate_header(header):
    if not isinstance(header,dict) or type(header.get('protocol')) is not int or header['protocol']!=PROTOCOL:raise ValueError('Unsupported publishing protocol')
    if header.get('operation')=='status':
        if set(header)!={'protocol','operation'}:raise ValueError('Unexpected status arguments')
        return None
    if set(header)!={'protocol','operation','manifest','sizes'} or header['operation']!='publish':raise ValueError('Unexpected publishing arguments')
    manifest=header['manifest'];sizes=header['sizes']
    if not isinstance(manifest,dict) or set(manifest)!={'routes','files','record_sources'}:raise ValueError('Unexpected manifest fields')
    files=manifest['files']
    if not isinstance(files,dict) or not 1<=len(files)<=FILE_LIMIT or not REQUIRED<=set(files):raise ValueError('Missing or excessive public files')
    if not isinstance(sizes,dict) or set(sizes)!=set(files):raise ValueError('Size manifest differs')
    if manifest['routes']!=route_map(files):raise ValueError('Routes must be derived from public filenames')
    if not isinstance(manifest['record_sources'],list) or len(manifest['record_sources'])>500 or any(not isinstance(x,str) or len(x)>4096 for x in manifest['record_sources']):raise ValueError('Invalid record source metadata')
    total=0
    for name,digest in files.items():
        if not isinstance(digest,str) or not HASH.fullmatch(digest):raise ValueError('Invalid file digest')
        size=sizes[name];limit=16_000_000 if name.endswith('.jpg') else 2_000_000
        if type(size) is not int or not 0<size<=limit:raise ValueError('Invalid file size')
        total+=size
    if total>TOTAL_LIMIT:raise ValueError('Publication exceeds transfer limit')
    return manifest


class StaticHTML(HTMLParser):
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag in {'iframe','object','embed','base','style','form'}:raise ValueError('Unsupported active HTML element')
        if tag=='script' and (set(attrs)-{'src','defer'} or attrs.get('src')!='/assets/site.js'):raise ValueError('Unsupported script')
        if any(key.startswith('on') or key=='style' for key in attrs):raise ValueError('Inline executable HTML')
        for key in ('href','src'):
            value=attrs.get(key,'')
            if re.match(r'\s*(?:javascript|data|vbscript):',value,re.I):raise ValueError('Executable URL')


def validate_file(name,raw):
    # Uploaded bytes are data; none are imported, executed, or used as nginx syntax.
    if name.endswith('.jpg'):
        from record_media import clean_jpeg
        if not clean_jpeg(raw):raise ValueError('Unprocessed image')
    else:
        text=raw.decode('utf-8')
        if '\x00' in text:raise ValueError('Binary data in text file')
        if name.endswith('.html'):StaticHTML().feed(text)


def receive_files(stream,header,destination):
    manifest=validate_header(header)
    if manifest is None:raise ValueError('No publication payload')
    destination=Path(destination)
    for name in sorted(manifest['files']):
        length=header['sizes'][name]
        raw=stream.read(length)
        if len(raw)!=length or hashlib.sha256(raw).hexdigest()!=manifest['files'][name]:raise ValueError('Truncated or mismatched file')
        validate_file(name,raw)
        target=destination/name
        target.parent.mkdir(parents=True,exist_ok=True,mode=0o755)
        with target.open('xb') as handle:handle.write(raw)
        target.chmod(0o644)
    if stream.read(1):raise ValueError('Unexpected trailing payload')
    # The receiver starts with umask 077; nginx needs traversal on public dirs.
    destination.chmod(0o755)
    for path in destination.rglob('*'):
        if path.is_dir():path.chmod(0o755)
    return manifest


def nginx_routes(manifest):
    # All paths were derived by route_map and validated; never accept uploaded config.
    if manifest['routes']!=route_map(manifest['files']):raise ValueError('Invalid routes')
    blocks=[]
    for route,name in sorted(manifest['routes'].items()):
        cache='1h' if route.startswith('/assets/') else '-1'
        blocks.append(f'location = {route} {{\n    try_files /{name} =404;\n    expires {cache};\n}}')
    blocks.append('''location = /portfolio/giseok-chung-data-portfolio.pdf {
    root /var/www/yangarch.net;
    try_files $uri =404;
    include /etc/nginx/snippets/yangarch-home-headers.conf;
    add_header Content-Disposition 'inline; filename="giseok-chung-data-portfolio.pdf"';
    add_header X-Robots-Tag "noindex, nofollow, noarchive" always;
}''')
    blocks.append('location / { return 404; }')
    return '\n\n'.join(blocks)+'\n'
