"""Local, attributed municipality imagery and independent HabitaMadrid identity."""
import base64
import json
from functools import lru_cache
from html import escape
from pathlib import Path


def brand_symbol():
    """Local HiDPI image: st.html's HTML-only sanitizer removes inline SVG."""
    path = Path(__file__).resolve().parents[1] / 'assets/brand-seven-stars.png'
    data = _image(str(path), path.stat().st_mtime_ns)
    return ('<span class="brand-symbol" aria-hidden="true">'
            f'<img src="data:image/png;base64,{data}" width="58" height="48" '
            'alt="" decoding="sync"/></span>')


@lru_cache(maxsize=4)
def _catalog(path, modified):
    return json.loads(Path(path).read_text(encoding='utf-8'))


@lru_cache(maxsize=160)
def _image(path, modified):
    return base64.b64encode(Path(path).read_bytes()).decode('ascii')


def municipality_photo(root, code, name):
    """Resolve only local INE assets; missing/invalid files get an honest fallback."""
    directory = Path(root) / 'data/reference/municipality_photos'
    registry = directory / 'catalog.json'
    item = {}
    try:
        item = _catalog(str(registry), registry.stat().st_mtime_ns).get(str(code), {})
        image = directory / (str(code) + '.webp')
        if (item.get('status') != 'available' or item.get('file') != image.name
                or not str(code).isdigit() or len(str(code)) != 5):
            raise ValueError('No verified photograph')
        source = item['source_url']
        if not source.startswith('https://commons.wikimedia.org/'):
            raise ValueError('Invalid source')
        data = _image(str(image), image.stat().st_mtime_ns)
        return (f'<figure class="municipal-photo"><img src="data:image/webp;base64,{data}" '
                f'alt="{escape(name)} · fotografía de Wikimedia Commons" loading="lazy" decoding="async" width="960" height="540">'
                f'<figcaption><a href="{escape(source, quote=True)}" target="_blank" rel="noopener noreferrer">'
                f'Foto: {escape(item["author"])} · {escape(item["license"])} · Wikimedia Commons</a>'
                '<span>Encuadre adaptado</span></figcaption></figure>')
    except (OSError, ValueError, KeyError, TypeError):
        return (f'<div class="municipal-photo photo-fallback"><span aria-hidden="true">⌖</span>'
                f'<strong>{escape(name)}</strong><small>Comunidad de Madrid</small>'
                '<small class="photo-pending">Sin fotografía verificada</small></div>')
