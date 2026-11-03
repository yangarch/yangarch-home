"""Public record image paths and a dependency-free JPEG metadata boundary."""
import re

IMAGE_PATH = re.compile(r'/media/records/([a-z0-9]+(?:-[a-z0-9]+)*)/([a-f0-9]{32})\.jpg')


def image_path(url, record_id=None):
    match = IMAGE_PATH.fullmatch(url) if isinstance(url, str) else None
    if not match or record_id is not None and match[1] != record_id:
        raise ValueError('Invalid record image path')
    return url.removeprefix('/media/records/')


def clean_jpeg(data):
    """Allow generated JPEGs without EXIF, XMP, ICC profiles or comments."""
    if not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9') or len(data) > 16_000_000:
        return False
    offset = 2
    while offset + 4 <= len(data):
        if data[offset] != 255:
            return False
        marker = data[offset + 1]
        length = int.from_bytes(data[offset + 2:offset + 4], 'big')
        if length < 2 or offset + 2 + length > len(data):
            return False
        if marker == 0xFE or 0xE1 <= marker <= 0xEF:
            return False
        if marker == 0xDA:
            return True
        offset += 2 + length
    return False


def media_paths(entries):
    return {block['src'] for entry in entries for block in entry['body'] if isinstance(block, dict)}


def first_text(entry):
    return next((block for block in entry['body'] if isinstance(block, str) and block.strip()), '')
