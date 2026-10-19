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
    row = data.get('municipalities', {}).get(code, {})
    audit = transport_verification(root, code)
    excluded = audit.get('nearby_line_exclusions', [])
    if excluded:
        row = {**row, 'lines': [line for line in row.get('lines', []) if line['line'] not in excluded],
               'nearby_lines': excluded}
    return data, row


def transport_verification(root, code):
    """Read the active edition's individual audit, retaining older snapshots."""
    path = Path(root) / 'data/processed/transporte_oferta/municipal_service_audit.json'
    try:
        data = _read(str(path), path.stat().st_mtime_ns)
    except (OSError, ValueError):
        return {}
    result = dict(data.get('municipalities', {}).get(code, {}))
    result['nearby_line_exclusions'] = data.get('nearby_line_exclusions', {}).get(code, [])
    demand = data.get('demand_responsive', {})
    if code in demand.get('municipalities', []):
        result['demand_responsive'] = demand
    return result


def official_url(url):
    try:
        p = urlsplit(url)
        return (p.scheme == 'https' and p.hostname in ('crtm.es', 'www.crtm.es',
                'renfe.com', 'www.renfe.com', 'horarioscercanias.renfe.com',
                'metromadrid.es', 'www.metromadrid.es', 'laveloz.es') and not p.username and not p.password)
    except ValueError:
        return False
