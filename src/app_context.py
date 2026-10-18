"""Local derived descriptions and verified transport links, keyed by INE."""
from functools import lru_cache
import json
from pathlib import Path
from urllib.parse import urlsplit


@lru_cache(maxsize=8)
def _read(path, modified):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def load_context(root, filename):
    path = Path(root) / 'data/reference/municipal_context' / filename
    try:
        return _read(str(path), path.stat().st_mtime_ns)
    except (OSError, ValueError):
        return {}


def description(root, code):
    return load_context(root, 'descriptions.json').get('municipalities', {}).get(code, {})


def transport_lines(root, code):
    data = load_context(root, 'transport_lines.json')
    return data, data.get('municipalities', {}).get(code, {})


def official_url(url):
    try:
        p = urlsplit(url)
        return (p.scheme == 'https' and p.hostname in ('crtm.es', 'www.crtm.es',
                'renfe.com', 'www.renfe.com') and not p.username and not p.password)
    except ValueError:
        return False
