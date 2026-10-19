"""Verified derived transport edition, separate from the immutable 1.0 baseline."""
from pathlib import Path
from datetime import date, timedelta
import hashlib
import json
import numpy as np
import pandas as pd

from src.integration_common import IntegrationError
from src.transport_offer_model import VERSION, INDICATOR, METHODS, score_transport_offer

FOLDER = 'data/processed/transporte_oferta'
MANIFEST = FOLDER + '/edition.json'
FILE_MAP = {
    'ivp_general': 'ivp_general.csv', 'dimensiones': 'dimensiones_ivp.csv',
    'observaciones': 'observaciones_seleccionadas.csv',
    'seleccion': 'seleccion_indicadores.csv', 'sensibilidad': 'sensibilidad_municipios.csv',
    'escenarios': 'sensibilidad_escenarios.csv', 'transporte_oferta': 'oferta_municipal.csv',
}


def active_transport_metadata(root):
    snapshot = Path(root) / 'data/runtime_snapshot.json'
    if not snapshot.exists(): return None
    return json.loads(snapshot.read_text(encoding='utf-8')).get('active_transport_edition')


def apply_transport_edition(data, root, read_csv):
    """Only load a revision explicitly declared in the verified runtime snapshot."""
    root = Path(root)
    active = active_transport_metadata(root)
    if active is None: return data
    if active != {'version': VERSION, 'manifest': MANIFEST}:
        raise IntegrationError('Edición de transporte activa desconocida.')
    manifest = json.loads((root / MANIFEST).read_text(encoding='utf-8'))
    if manifest.get('version') != VERSION or manifest.get('indicator') != INDICATOR:
        raise IntegrationError('La edición de transporte no coincide con la declarada.')
    for name, expected in manifest['baseline_sha256'].items():
        if Path(name).name != name:
            raise IntegrationError('Referencia de base no válida.')
        actual = hashlib.sha256((root / 'data/processed/ivp' / name).read_bytes()).hexdigest()
        if actual != expected:
            raise IntegrationError('La referencia IVP 1.0 ha cambiado.')
    folder = root / FOLDER
    expected_files = set(FILE_MAP.values()) | {'configuracion_ivp.json', 'normalizacion.json'}
    if not expected_files.issubset(manifest['files']):
        raise IntegrationError('La revisión de transporte está incompleta.')
    for name, expected in manifest['files'].items():
        if Path(name).name != name:
            raise IntegrationError('Archivo de revisión no válido.')
        f = folder / name
        if f.stat().st_size != expected['bytes'] or hashlib.sha256(f.read_bytes()).hexdigest() != expected['sha256']:
            raise IntegrationError('Archivo de transporte alterado: ' + name)
    for key, name in FILE_MAP.items(): data[key] = read_csv(folder / name)
    data['dimensiones'] = data['dimensiones'].loc[data['dimensiones'].metodo.eq('minmax')].reset_index(drop=True)
    for key, name in [('configuracion', 'configuracion_ivp.json'), ('normalizacion', 'normalizacion.json')]:
        data[key] = json.loads((folder / name).read_text(encoding='utf-8'))
    metrics = data['transporte_oferta']
    wanted = set(data['municipios'].codigo_ine)
    if metrics.codigo_ine.duplicated().any() or set(metrics.codigo_ine) != wanted:
        raise IntegrationError('La oferta de transporte no conserva los 155 municipios.')
    score = metrics.set_index('codigo_ine').puntuacion_transporte
    expected = (metrics.puntuacion_frecuencia + metrics.puntuacion_conectividad) / 2
    if not np.allclose(metrics.puntuacion_transporte, expected, equal_nan=True, atol=1e-10):
        raise IntegrationError('La puntuación de transporte no coincide con sus componentes.')
    invalid = metrics.estado_cobertura.ne('publicado_con_contraste_de_lineas')
    if metrics.loc[invalid, 'puntuacion_transporte'].notna().any():
        raise IntegrationError('Una carencia de horarios se ha convertido en puntuación.')
    obs = data['observaciones'].loc[data['observaciones'].indicador_id.eq(INDICATOR)].set_index('codigo_ine').valor
    if not np.allclose(obs.reindex(score.index), score, equal_nan=True, atol=1e-10):
        raise IntegrationError('La observación de transporte no coincide con la edición.')
    for profile, dimensions in data['dimensiones'].groupby('perfil_vivienda'):
        if not np.allclose(dimensions.set_index('codigo_ine').transporte.reindex(score.index), score, equal_nan=True, atol=1e-10):
            raise IntegrationError('El IVP conserva una puntuación de transporte distinta de la revisión.')
    verify_transport_calculation(data, folder)
    data['catalogo_seleccionado'] = data['seleccion'].copy()
    data['transporte_metodologia'] = manifest['methodology']
    data['fuentes'] = pd.concat([data['fuentes'], pd.DataFrame(manifest['sources'])], ignore_index=True)
    data['integridad']['metodologia_activa'] = VERSION
    return data


def verify_transport_calculation(data, folder):
    """Check daily evidence, normalized components, all IVP rows and ranks."""
    offer = json.loads((folder / 'offer_daily.json').read_text(encoding='utf-8'))
    gaps = json.loads((folder / 'coverage_decisions.json').read_text(encoding='utf-8'))
    dates = [date.fromisoformat(d) for d in offer['dates']]
    if len(dates) != 7 or dates[0].weekday() != 0 or dates != [dates[0] + timedelta(days=i) for i in range(7)]:
        raise IntegrationError('La semana de transporte no es consecutiva de lunes a domingo.')
    codes = sorted(data['municipios'].codigo_ine)
    if set(offer['daily']) != set(codes):
        raise IntegrationError('La evidencia diaria no conserva el universo municipal.')
    for code in codes:
        daily = offer['daily'][code]
        if set(daily) != set(offer['dates']):
            raise IntegrationError('Faltan fechas de transporte en ' + code)
        for r in daily.values():
            if (type(r['frequency']) is not int or r['frequency'] < 0
                    or len(r['destinations']) != len(set(r['destinations']))
                    or code in r['destinations'] or r['connectivity'] != len(r['destinations'])):
                raise IntegrationError('Recuento diario de transporte inválido en ' + code)
        for group, days in [(None, dates), ('weekdays', dates[:5]), ('saturday', dates[5:6]), ('sunday', dates[6:])]:
            summary = offer['municipalities'][code] if group is None else offer['municipalities'][code][group]
            for field in ('frequency', 'connectivity'):
                expected = sum(daily[d.isoformat()][field] for d in days) / len(days)
                if not np.isclose(expected, summary[field], atol=1e-10):
                    raise IntegrationError('La media de transporte no coincide con sus días en ' + code)
    recomputed, matrices, refs = score_transport_offer(offer, codes, gaps)
    actual = data['transporte_oferta'].set_index('codigo_ine').reindex(codes)
    recomputed = recomputed.set_index('codigo_ine').reindex(codes)
    for field in ('expediciones_dia', 'destinos_directos_dia', 'puntuacion_frecuencia', 'puntuacion_conectividad', 'puntuacion_transporte'):
        if not np.allclose(actual[field], recomputed[field], equal_nan=True, atol=1e-10):
            raise IntegrationError('El agregado de transporte no reproduce la evidencia diaria: ' + field)
    for method in METHODS:
        if data['normalizacion'][method][INDICATOR]['references'] != refs[method]:
            raise IntegrationError('Referencias de transporte incompatibles con la evidencia.')
    all_dims = pd.read_csv(folder / FILE_MAP['dimensiones'], dtype={'codigo_ine': str})
    for (profile, method), part in all_dims.groupby(['perfil_vivienda', 'metodo']):
        if not np.allclose(part.set_index('codigo_ine').transporte.reindex(codes), matrices[method].transporte, equal_nan=True, atol=1e-10):
            raise IntegrationError('Las puntuaciones de sensibilidad no reproducen el transporte.')
    for profile, part in data['ivp_general'].groupby('perfil_vivienda'):
        # Independent equal-weight arithmetic; no implicit skip-NA average.
        expected = part[['puntuacion_' + d for d in ('sanidad', 'educacion', 'transporte', 'vivienda')]].sum(axis=1, min_count=4) / 4
        rank = expected.rank(method='min', ascending=False)
        if (not np.allclose(part.valor_indice, expected, equal_nan=True, atol=1e-10)
                or not np.allclose(part.posicion, rank, equal_nan=True, atol=1e-10)):
            raise IntegrationError('El IVP o sus puestos no reproducen las cuatro dimensiones: ' + profile)
