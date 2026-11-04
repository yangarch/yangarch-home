#!/usr/bin/env python3
"""Build the approved personal homepage from curated public content only."""
import hashlib
import html
import json
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit
from public_links import SERVICE_URLS
from records import CATEGORIES, validate_records
from record_media import clean_jpeg, first_text, image_path, media_paths
from content_policy import source_policy
ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / 'dist'
ASSETS = ('site.css', 'site.js', 'favicon.svg')
ORIGIN = 'https://yangarch.net'

def esc(value):
    return html.escape(str(value), quote=True)

def external(url, label, cls='text-link'):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.netloc not in {'github.com', 'www.linkedin.com'}:
        raise ValueError('External URL is outside the public-link allowlist')
    return f'<a class="{cls}" href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(label)} <span aria-hidden="true">↗</span></a>'

def tags(items):
    return '<ul class="tags" aria-label="사용 기술">' + ''.join(f'<li>{esc(x)}</li>' for x in items) + '</ul>'

def arrow(direction='external'):
    path = 'M7 17 17 7M7 7h10v10' if direction == 'external' else 'M12 5v14m-6-6 6 6 6-6'
    return f'<svg class="arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="{path}"/></svg>'

def profile(url, label):
    return external(url, label)

def service_links(project, show_addresses=False):
    links = []
    for service in project.get('services', []):
        url, label = service['url'], service['label']
        if url not in SERVICE_URLS or not label.strip():
            raise ValueError('Service URL or label is outside the reviewed public destinations')
        accessible = f"{project['name']} {label}" + ('' if '접속' in label else ' 접속하기')
        link = f'<a class="service-link" href="{esc(url)}" target="_blank" rel="noopener noreferrer" aria-label="{esc(accessible)}">{esc(label)} {arrow()}</a>'
        if show_addresses:
            link = f'<div class="service-destination">{link}<span class="service-address">{esc(urlsplit(url).netloc)}</span></div>'
        links.append(link)
    return ''.join(links)

def service_access(project):
    links = service_links(project, show_addresses=True)
    if not links:
        return ''
    note = f'<p class="service-note">{esc(project["service_note"])}</p>' if project.get('service_note') else ''
    return f'<section class="service-access" aria-label="서비스 접속"><h2>서비스 접속</h2><div class="service-destinations">{links}</div>{note}</section>'

def document(title, description, body, path='/', detail=False):
    canonical = ORIGIN + path
    return f'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"><title>{esc(title)}</title><meta name="description" content="{esc(description)}"><link rel="canonical" href="{esc(canonical)}"><meta property="og:type" content="website"><meta property="og:locale" content="ko_KR"><meta property="og:title" content="{esc(title)}"><meta property="og:description" content="{esc(description)}"><meta property="og:url" content="{esc(canonical)}"><link rel="icon" type="image/svg+xml" href="/assets/favicon.svg"><link rel="stylesheet" href="/assets/site.css"><script src="/assets/site.js" defer></script></head>
<body><a class="skip-link" href="#main">본문으로 바로가기</a><header class="site-header wrap"><a class="wordmark" href="/" aria-label="yangarch 홈">yangarch<span aria-hidden="true">.</span></a><nav aria-label="주 메뉴"><a href="/#work">프로젝트</a><a href="/#about">소개</a><a href="/#records">기록</a><a href="/#contact">연락</a></nav></header><main id="main" tabindex="-1">{body}</main><footer class="site-footer wrap"><a href="/" class="footer-name">yangarch.</a><div><span>© 2026 정기석</span><a href="#main">위로 ↑</a></div></footer></body></html>'''

def home(data):
    projects = data['projects']
    entries = []
    for p in projects:
        category = '운동' if p['id']=='training' else '가족' if p['id']=='poogiegram' else '도구'
        training = '''<div class="project-parts" aria-label="함께 관리하는 프로젝트"><span>training-companion</span><span>trisplit</span><span>fingu</span></div>''' if p['id']=='training' else ''
        entries.append(f'''<article class="project-entry {'training-entry' if p['id']=='training' else ''}" data-category="{category}"><div class="project-name"><h3><a href="/projects/{esc(p['id'])}/">{esc(p['name'])} {arrow()}</a></h3><span class="project-kind">{esc(p['category'])}</span></div><div class="project-content"><p>{esc(p['description'])}</p>{training}{tags(p['stack'])}</div><div class="project-action"><span class="status">{esc(p['status'])}</span><div class="project-links">{service_links(p)}<a class="intro-link" href="/projects/{esc(p['id'])}/" aria-label="{esc(p['name'])} 소개 보기">소개 보기 {arrow()}</a></div></div></article>''')
    topics = ''.join(f'<a href="/records/{slug}/">{esc(label)} {arrow()}</a>' for slug,(label,_) in CATEGORIES.items())
    return document('정기석 · yangarch — 작업과 기록', '정기석의 개인 프로젝트와 기록.', f'''
<section class="hero wrap"><div class="hero-intro"><h1>정기석</h1><div><p>데이터 파이프라인과 백엔드, 업무 도구들을 개발합니다.<br>개인 프로젝트들과 기록들을 모아 두었습니다.</p><div class="hero-links"><a class="primary-link" href="#work">프로젝트 보기 {arrow('down')}</a>{profile(data['github'],'GitHub')}{profile(data['linkedin'],'LinkedIn')}</div></div></div></section>
<section id="work" class="work-section wrap" aria-labelledby="work-title"><div class="folder-tabs project-filters" role="group" aria-label="프로젝트 분류"><button type="button" aria-pressed="true" data-filter="all">전체</button><button type="button" aria-pressed="false" data-filter="운동">운동</button><button type="button" aria-pressed="false" data-filter="가족">가족</button><button type="button" aria-pressed="false" data-filter="도구">도구와 자동화</button></div><div class="folder-page"><div class="section-heading"><h2 id="work-title">프로젝트</h2><span id="filter-result" role="status">{len(projects)}개의 프로젝트</span></div><div class="project-index">{''.join(entries)}</div></div></section>
<section id="about" class="about-section wrap" aria-labelledby="about-title"><div class="about-sheet"><h2 id="about-title">소개</h2><div><p class="about-copy">만든 서비스들이 늘어나 도메인이 헷갈리기 시작했습니다.<br>모아두는 페이지가 필요해 만들었어요.</p><p class="signature">정기석 <span>Giseok Chung</span></p><div class="about-links">{profile(data['github'],'GitHub에서 작업 보기')}{profile(data['linkedin'],'LinkedIn에서 경력 보기')}</div></div></div></section>
<section id="records" class="records-section wrap" aria-labelledby="records-title"><div><h2 id="records-title">기록</h2><p>자전거를 타고, 수영을 하고, 그날의 경험을 남깁니다.<br>커뮤니티에 남긴 후기와 개인적인 기록도 오래 간직할 수 있도록 조금씩 모아 가려 합니다.</p><a class="text-link" href="/records/">전체 기록 보기 {arrow()}</a></div><div class="record-topics">{topics}</div></section>
<section id="contact" class="contact-section wrap" aria-labelledby="contact-title"><div class="contact-sheet"><h2 id="contact-title">연락</h2><div><p>프로젝트 이야기나 함께할 일에 대한 연락은<br>LinkedIn으로 이어 주세요.</p><div class="contact-links">{profile(data['linkedin'],'LinkedIn으로 연락하기')}{profile(data['github'],'GitHub')}</div></div></div></section>''')

def project_page(p, data):
    sections = ''.join(f'<section class="story-section"><h2>{esc(title)}</h2><p>{esc(text)}</p></section>' for title,text in p['sections'])
    source = profile(p.get('github', data['github']), '공개 코드 살펴보기' if p.get('github') else '다른 공개 작업 보기')
    return document(p['name']+' — 정기석 · yangarch', p['description'], f'''<article class="detail-page wrap"><a class="breadcrumb" href="/#work">← 프로젝트 목록</a><header class="detail-header"><h1>{esc(p['name'])}</h1><div class="detail-meta"><span>{esc(p['category'])}</span><span>{esc(p['status'])}</span></div><p class="detail-subtitle">{esc(p['subtitle'])}</p><p>{esc(p['description'])}</p>{tags(p['stack'])}</header>{service_access(p)}<div class="project-story">{sections}</div><div class="detail-end">{source}<a href="/#contact">이 작업에 대해 이야기하기 {arrow()}</a></div></article>''', '/projects/'+p['id']+'/', True)

def record_path(entry):
    return '/records/entry/'+entry['id']+'/'

def record_navigation(entries, selected=None):
    items = [(None, '전체', '/records/')] + [(slug, label, '/records/'+slug+'/') for slug,(label,_) in CATEGORIES.items()]
    links = []
    for slug,label,path in items:
        count = sum(1 for entry in entries if slug is None or entry['category']==slug)
        current = ' aria-current="page"' if slug==selected else ''
        links.append(f'<a href="{path}"{current}>{label}<span class="record-count">{count}</span></a>')
    return '<nav class="record-navigation" aria-label="기록 분류">'+''.join(links)+'</nav>'

def record_index(entries, category=None):
    label,description = CATEGORIES[category] if category else ('기록', '자전거, 수영, 그리고 만드는 일에 대한 기록입니다.')
    selected = [entry for entry in entries if category is None or entry['category']==category]
    rows = []
    for entry in selected:
        category_label = CATEGORIES[entry['category']][0]
        excerpt = first_text(entry)
        excerpt = excerpt[:160].rstrip()+'…' if len(excerpt)>160 else excerpt
        rows.append(f'''<li><article class="record-entry"><div class="record-entry-meta"><time datetime="{entry['date']}">{entry['date'].replace('-','. ')}</time><span>{category_label}</span></div><h2><a href="{record_path(entry)}">{esc(entry['title'])} {arrow()}</a></h2><p>{esc(excerpt)}</p></article></li>''')
    listing = '<ol class="record-list">'+''.join(rows)+'</ol>' if rows else '<div class="records-empty"><p>아직 등록한 기록이 없습니다.</p></div>'
    path = '/records/'+category+'/' if category else '/records/'
    title = label+' 기록' if category else label
    return document(title+' — 정기석 · yangarch', description, f'''<div class="records-page wrap"><a class="breadcrumb" href="/#records">← 홈의 기록</a><header class="records-header"><h1>{title}</h1><p>{description}</p></header>{record_navigation(entries,category)}<section class="record-results" aria-label="{title} 목록"><p class="record-total">{len(selected)}개의 기록</p>{listing}</section></div>''', path)

def record_page(entry):
    label = CATEGORIES[entry['category']][0]
    source = ''
    if 'source' in entry:
        original = entry['source']
        source = f'<a class="text-link record-source" href="{esc(original["url"])}" target="_blank" rel="noopener noreferrer">원문 보기 · {esc(original["label"])} {arrow()}</a>'
    parts = []
    for block in entry['body']:
        if isinstance(block,str):
            parts.append('<p>'+esc(block).replace('\n','<br>')+'</p>')
        else:
            caption = '<figcaption>'+esc(block['caption'])+'</figcaption>' if block.get('caption') else ''
            parts.append(f'<figure class="record-photo"><img src="{esc(block["src"])}" alt="{esc(block.get("alt",""))}" loading="lazy" decoding="async">{caption}</figure>')
    paragraphs = ''.join(parts)
    excerpt = first_text(entry)[:160] or entry['title']
    return document(entry['title']+' — 정기석 · yangarch', excerpt, f'''<article class="detail-page record-detail wrap"><a class="breadcrumb" href="/records/{entry['category']}/">← {label} 기록</a><header class="detail-header"><div class="record-entry-meta"><a href="/records/{entry['category']}/">{label}</a><span>작성일 <time datetime="{entry['date']}">{entry['date'].replace('-','. ')}</time></span></div><h1>{esc(entry['title'])}</h1>{source}</header><div class="record-body">{paragraphs}</div><div class="detail-end"><a href="/records/{entry['category']}/">← {label} 기록 목록</a><a href="/records/">전체 기록 보기 {arrow()}</a></div></article>''', record_path(entry))

def build():
    data=json.loads((ROOT/'content/public.json').read_text())
    records=validate_records(json.loads((ROOT/'content/records.json').read_text()))
    policy=source_policy(ROOT)
    images = {}
    for url in media_paths(records):
        source = ROOT/'content/record-media'/image_path(url)
        if source.is_symlink() or not source.is_file() or source.resolve() != source.absolute() or not clean_jpeg(source.read_bytes()):
            raise ValueError('Missing or unsafe public image: '+url)
        images[url] = source
    if DIST.exists():
        if DIST.is_symlink():raise ValueError('dist must not be a symlink')
        shutil.rmtree(DIST)
    DIST.mkdir()
    (DIST/'assets').mkdir()
    for name in ASSETS:
        src=ROOT/'assets'/name
        if src.is_symlink():raise ValueError('asset symlinks are forbidden')
        shutil.copyfile(src,DIST/'assets'/name)
    routes={'/':'index.html','/index.html':'index.html'}
    (DIST/'index.html').write_text(home(data))
    for project in data['projects']:
        if not re.fullmatch('[a-z-]+',project['id']):raise ValueError('invalid project id')
        target=DIST/'projects'/project['id']/'index.html'
        target.parent.mkdir(parents=True)
        target.write_text(project_page(project,data))
        routes['/projects/'+project['id']+'/']=str(target.relative_to(DIST))
    record_pages = {'/records/':record_index(records)}
    record_pages.update({'/records/'+slug+'/':record_index(records,slug) for slug in CATEGORIES})
    record_pages.update({record_path(entry):record_page(entry) for entry in records})
    for path,markup in record_pages.items():
        rel=path.lstrip('/')+'index.html'
        target=DIST/rel
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(markup)
        routes[path]=rel
    for name in ASSETS:routes['/assets/'+name]='assets/'+name
    for url,source in images.items():
        rel = url.lstrip('/')
        target = DIST/rel
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,target)
        routes[url] = rel
    (DIST/'robots.txt').write_text('User-agent: *\nAllow: /\nDisallow: /portfolio/\nSitemap: https://yangarch.net/sitemap.xml\n')
    urls=[ORIGIN+path for path in routes if path=='/' or path.startswith(('/projects/','/records/'))]
    (DIST/'sitemap.xml').write_text('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'+''.join('<url><loc>'+esc(url)+'</loc></url>' for url in urls)+'</urlset>\n')
    routes['/robots.txt']='robots.txt';routes['/sitemap.xml']='sitemap.xml'
    manifest={'routes':routes,'record_sources':sorted({entry['source']['url'] for entry in records if 'source' in entry}),'files':{str(p.relative_to(DIST)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(DIST.rglob('*')) if p.is_file()}}
    (ROOT/'artifacts').mkdir(exist_ok=True)
    (ROOT/'artifacts/content-policy.json').write_text(json.dumps(policy,ensure_ascii=False,indent=2)+'\n')
    (ROOT/'artifacts/manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print(f'Built {len(data["projects"])+1+len(record_pages)} pages; {len(manifest["files"])} explicitly selected public files.')
    return manifest

if __name__=='__main__': build()
