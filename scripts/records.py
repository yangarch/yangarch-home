"""Validate explicitly selected public records; never import community/private files."""
from datetime import date
import re
from public_links import is_record_source
from record_media import image_path

CATEGORIES = {
    'cycling': ('자전거', '라이딩 후기와 자전거를 타며 남긴 기록입니다.'),
    'swimming': ('수영', '수영 일기와 물에서 보낸 시간의 기록입니다.'),
    'making': ('만드는 일', '개발하며 남긴 생각과 작업 기록입니다.'),
}


def validate_records(data, draft=False):
    if not isinstance(data, dict) or set(data) != {'entries'} or not isinstance(data['entries'], list):
        raise ValueError('Records must contain an entries list')
    seen = set()
    for entry in data['entries']:
        required = {'id', 'category', 'title', 'date', 'body'}
        if not isinstance(entry, dict) or not required <= entry.keys() or entry.keys() - required - {'source'}:
            raise ValueError('Invalid record fields')
        slug = entry['id']
        if not isinstance(slug, str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', slug) or slug in seen:
            raise ValueError('Record id must be a unique safe slug')
        seen.add(slug)
        if not isinstance(entry['category'], str) or entry['category'] not in CATEGORIES:
            raise ValueError('Unknown record category')
        if not isinstance(entry['title'], str) or len(entry['title'])>200 or not draft and not entry['title'].strip():
            raise ValueError('Record needs a title')
        if not isinstance(entry['date'], str) or date.fromisoformat(entry['date']).isoformat() != entry['date']:
            raise ValueError('Record date must be YYYY-MM-DD')
        if not isinstance(entry['body'], list) or len(entry['body'])>300 or not draft and not entry['body']:
            raise ValueError('Record needs a body')
        content = False
        for block in entry['body']:
            if isinstance(block, str):
                if len(block)>200_000 or not draft and not block.strip():
                    raise ValueError('Record paragraph is empty or too long')
                content = content or bool(block.strip())
            elif isinstance(block, dict) and block.get('type')=='image' and set(block)<={'type','src','alt','caption'}:
                image_path(block.get('src'), slug)
                for field in ('alt','caption'):
                    if not isinstance(block.get(field,''),str) or len(block.get(field,''))>2000:
                        raise ValueError('Invalid image description')
                content = True
            else:
                raise ValueError('Body supports text paragraphs and local images only')
        if not draft and not content:
            raise ValueError('Record needs a body')
        if 'source' in entry:
            source = entry['source']
            if not isinstance(source, dict) or set(source) != {'label', 'url'}:
                raise ValueError('Record source needs a label and URL')
            if not isinstance(source['label'], str) or not source['label'].strip() or not is_record_source(source['url']):
                raise ValueError('Unreviewed record source')
    return sorted(data['entries'], key=lambda entry: (entry['date'], entry['id']), reverse=True)
