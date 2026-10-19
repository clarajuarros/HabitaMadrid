#!/usr/bin/env python3
"""Prepare a local, auditable transport revision. Does not publish anything.

The baseline remains byte-for-byte unchanged. Source transcription and coverage
decisions are external inputs and their hashes are recorded. --activate-local
updates only the supplied checkout's runtime manifest after the derived files
have been generated and validated together.
"""
from pathlib import Path
import argparse
from copy import deepcopy
import hashlib
import json
import sys
import shutil
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.app_data import load_app_data
from src.ivp_calculation import DIMENSIONS, calculate_index
from src.ivp_sensitivity import analyze_sensitivity
from src.transport_offer_model import score_transport_offer, VERSION, INDICATOR, METHODS
from src.transport_edition import FOLDER, MANIFEST, FILE_MAP


def metadata(path):
    return {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'bytes': path.stat().st_size}


def prepare(root, offer_path, coverage_path, activate_local=False, evidence_paths=(), publication_status='candidate_not_published'):
    root = Path(root).resolve()
    base = load_app_data(root, include_transport_revision=False)
    offer = json.loads(Path(offer_path).read_text(encoding='utf-8'))
    gaps = json.loads(Path(coverage_path).read_text(encoding='utf-8'))
    codes = sorted(base['municipios'].codigo_ine.tolist())
    metrics, scores, references = score_transport_offer(offer, codes, gaps)
    folder = root / FOLDER; folder.mkdir(parents=True, exist_ok=True)
    metrics.to_csv(folder / 'oferta_municipal.csv', index=False)
    source_dims = pd.read_csv(root / 'data/processed/ivp/dimensiones_ivp.csv', dtype={'codigo_ine': str})
    all_dims, generals, sensitivities, scenarios = [], [], [], []
    for profile in ('VC', 'VU'):
        matrices = {}
        for method in METHODS:
            part = source_dims.loc[(source_dims.perfil_vivienda == profile) & (source_dims.metodo == method)].copy()
            part = part.set_index('codigo_ine').reindex(codes)
            part['transporte'] = scores[method].transporte.reindex(codes)
            matrices[method] = part[list(DIMENSIONS)].copy()
            all_dims.append(part.reset_index())
        calculated = calculate_index(matrices['minmax'], {d: .25 for d in DIMENSIONS})
        original = base['ivp_general'].loc[base['ivp_general'].perfil_vivienda.eq(profile)].set_index('codigo_ine').reindex(codes).copy()
        for col in calculated: original[col] = calculated[col]
        for dimension in DIMENSIONS: original['puntuacion_' + dimension] = matrices['minmax'][dimension]
        original['metodologia_version'] = VERSION
        original['municipios_con_indice'] = int(calculated.valor_indice.notna().sum())
        generals.append(original.reset_index())
        sensitivity = analyze_sensitivity(matrices)
        summary = sensitivity['summary']; summary['perfil_vivienda'] = profile
        long = sensitivity['scores']; long['perfil_vivienda'] = profile
        sensitivities.append(summary); scenarios.append(long)
    output = {'dimensiones': pd.concat(all_dims, ignore_index=True),
              'ivp_general': pd.concat(generals, ignore_index=True),
              'sensibilidad': pd.concat(sensitivities, ignore_index=True),
              'escenarios': pd.concat(scenarios, ignore_index=True)}
    config = deepcopy(base['configuracion'])
    old_indicator = config['indicadores_comunes']['transporte']
    config.update({'metodologia_version': VERSION,
                   'publicacion': 'Índice orientativo propio: proximidad sanitaria y educativa, oferta programada de transporte y alquiler declarado.',
                   'transporte': {'indicador': INDICATOR, 'peso_frecuencia': .5, 'peso_conectividad': .5,
                                 'semana_inicio': offer['dates'][0], 'semana_fin': offer['dates'][-1],
                                 'alcance': 'Oferta publicada y destinos municipales directos sin transbordo. Sin tiempos reales ni garantía de exhaustividad.'}})
    config['indicadores_comunes']['transporte'] = INDICATOR
    observations = base['observaciones'].copy()
    mask = observations.indicador_id.eq(old_indicator)
    observations.loc[mask, 'indicador_id'] = INDICATOR
    values = metrics.set_index('codigo_ine').puntuacion_transporte
    observations.loc[mask, 'valor'] = observations.loc[mask, 'codigo_ine'].map(values)
    observations.loc[mask, 'estado'] = np.where(observations.loc[mask, 'valor'].notna(), 'disponible', 'sin_cobertura_horaria_verificada')
    observations.loc[mask, 'periodo'] = 'consulta_2026-10-19'
    def source_ids(keys):
        result = []
        if any(k in {'interurbanos', 'urbanos', 'metro_ligero', 'crtm_topology_verified'} for k in keys): result.append('crtm_oferta_gtfs')
        if 'crtm_topology_verified' in keys: result.append('crtm_itinerarios_paradas')
        if 'cercanias_renfe' in keys: result.append('renfe_horarios_cercanias')
        if 'renfe_verified_prefixes' in keys: result.append('renfe_horarios_regionales')
        if any(k.startswith('crtm_pdf_') for k in keys): result.append('crtm_horarios_pdf')
        return json.dumps(result)
    observation_sources = metrics.set_index('codigo_ine').fuentes_json.map(lambda s: source_ids(json.loads(s)))
    observations.loc[mask, 'fuentes_json'] = observations.loc[mask, 'codigo_ine'].map(observation_sources)
    observations.loc[mask, 'nota'] = 'Media de las puntuaciones de expediciones intermunicipales y destinos directos por día. Semana 19–25/10/2026. Horarios publicados, no tiempo real.'
    if 'detalle_json' in observations:
        details = metrics.set_index('codigo_ine').apply(lambda r: json.dumps({
            'expediciones_dia': r.expediciones_dia, 'destinos_directos_dia': r.destinos_directos_dia,
            'estado_cobertura': r.estado_cobertura, 'nota_cobertura': r.nota_cobertura,
            'inicio_semana': r.inicio_semana, 'fin_semana': r.fin_semana}, ensure_ascii=False), axis=1)
        observations.loc[mask, 'detalle_json'] = observations.loc[mask, 'codigo_ine'].map(details)
    selection = base['seleccion'].copy(); mask = selection.dimension.eq('transporte')
    for col, value in {'indicador_id': INDICATOR, 'nombre': 'Oferta y conectividad directa programada',
                       'unidad': 'puntos/100', 'sentido': 'mayor_mejor',
                       'periodos_json': json.dumps(['consulta_2026-10-19']),
                       'motivo': 'Sustituye la proximidad a una parada por expediciones y destinos directos de horarios oficiales.',
                       'limitaciones': 'Semana fija, oferta publicada de los modos cubiertos, sin transbordos ni puntualidad observada. Carencias verificadas conservan ausencia de puntuación.'}.items():
        selection.loc[mask, col] = value
    output.update({'observaciones': observations, 'seleccion': selection})
    for key, table in output.items(): table.to_csv(folder / FILE_MAP[key], index=False)
    normalizations = deepcopy(base['normalizacion'])
    for method in METHODS:
        normalizations[method].pop(old_indicator, None)
        normalizations[method][INDICATOR] = {'method': 'media_componentes', 'direction': 'mayor_mejor',
                                           'weights': {'frecuencia': .5, 'conectividad': .5},
                                           'references': references[method]}
    (folder / 'configuracion_ivp.json').write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    (folder / 'normalizacion.json').write_text(json.dumps(normalizations, ensure_ascii=False, indent=2), encoding='utf-8')
    (folder / 'offer_daily.json').write_text(json.dumps(offer, ensure_ascii=False, indent=2), encoding='utf-8')
    (folder / 'coverage_decisions.json').write_text(json.dumps(gaps, ensure_ascii=False, indent=2), encoding='utf-8')
    sources = [
        {'fuente': 'crtm_oferta_gtfs', 'archivo': 'data/processed/transporte_oferta/offer_daily.json',
         'url': 'https://datos.crtm.es/', 'fecha_descarga_utc': '2026-10-19',
         'licencia': 'https://www.crtm.es/licencia-de-uso', 'nota': 'Oferta programada CRTM, feeds vigentes de autobús y Metro Ligero. Se excluye Metro caducado.'},
        {'fuente': 'renfe_horarios_cercanias', 'archivo': 'data/processed/transporte_oferta/offer_daily.json',
         'url': 'https://data.renfe.com/dataset/horarios-cercanias', 'fecha_descarga_utc': '2026-10-19',
         'licencia': 'https://creativecommons.org/licenses/by/4.0/', 'nota': 'Renfe, Horarios cercanías, CC BY 4.0. Descarga 19/10/2026; transformación en expediciones y destinos municipales. Los 31 viajes de Madrid sin secuencia se contrastan individualmente con el GTFS Regional, conservando el extremo declarado de la ruta y excluyendo la continuación ya contabilizada. Sin respaldo oficial de Renfe a HabitaMadrid.'},
        {'fuente': 'renfe_horarios_regionales', 'archivo': 'data/processed/transporte_oferta/renfe_prefix_repairs.json',
         'url': 'https://data.renfe.com/dataset/horarios-de-alta-velocidad-larga-distancia-y-media-distancia', 'fecha_descarga_utc': '2026-10-19',
         'licencia': 'https://creativecommons.org/licenses/by/4.0/', 'nota': 'Solo los tramos publicados que faltan en Cercanías, hasta Cercedilla/Fuenlabrada. No se añaden dos veces las continuaciones del mismo servicio.'},
        {'fuente': 'crtm_itinerarios_paradas', 'archivo': 'data/processed/transporte_oferta/topology_repairs.json',
         'url': 'https://services5.arcgis.com/UxADft6QPcvFyDU1/arcgis/rest/services/M8_Red/FeatureServer', 'fecha_descarga_utc': '2026-10-19',
         'licencia': 'https://www.crtm.es/licencia-de-uso', 'nota': 'Secuencias oficiales de paradas por itinerario y municipio. Se conserva la variante exacta; no se inventan horas para las paradas sin horario.'},
        {'fuente': 'crtm_horarios_pdf', 'archivo': 'data/processed/transporte_oferta/offer_daily.json',
         'url': 'https://www.crtm.es/tu-transporte-publico/', 'fecha_descarga_utc': '2026-10-19',
         'licencia': 'https://www.crtm.es/licencia-de-uso', 'nota': 'Horarios PDF fechados para la semana 19–25/10/2026, con variantes, lectivos y noches contrastados. Sustituyen líneas enteras del GTFS. La 337 prioriza el horario de viajeros coincidente en CRTM y La Veloz; se conserva la discrepancia del GTFS. No son todos los PDF de la red.'},
        {'fuente': 'metro_horarios_publicados', 'archivo': 'data/processed/transporte_oferta/municipal_service_audit.json',
         'url': 'https://www.metromadrid.es/es/linea/linea-7#line-timetable', 'fecha_descarga_utc': '2026-10-19',
         'licencia': '', 'nota': 'MetroEste: servicio y rangos confirmados en la web y carteles 26.A. Los rangos variables diurnos laborables no se convierten en expediciones exactas ni permiten puntuar San Fernando de Henares.'},
    ]
    baseline = {name: metadata(root / 'data/processed/ivp' / name)['sha256'] for name in
                ('ivp_general.csv', 'dimensiones_ivp.csv', 'observaciones_seleccionadas.csv', 'configuracion_ivp.json')}
    filenames = set(FILE_MAP.values()) | {'configuracion_ivp.json', 'normalizacion.json', 'offer_daily.json', 'coverage_decisions.json'}
    for evidence_path in evidence_paths:
        evidence_path = Path(evidence_path)
        if evidence_path.name in filenames or evidence_path.name == 'edition.json':
            raise ValueError('Duplicate evidence filename')
        shutil.copyfile(evidence_path, folder / evidence_path.name)
        filenames.add(evidence_path.name)
    manifest = {'version': VERSION, 'data_revision': '2026-10-19-cobertura-completada', 'indicator': INDICATOR, 'baseline_sha256': baseline,
                'input_sha256': {'offer': metadata(Path(offer_path))['sha256'], 'coverage': metadata(Path(coverage_path))['sha256']},
                'files': {name: metadata(folder / name) for name in sorted(filenames)},
                'sources': sources, 'methodology': config['transporte'], 'publication_status': publication_status}
    (root / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    if activate_local:
        snapshot_path = root / 'data/runtime_snapshot.json'
        snapshot = json.loads(snapshot_path.read_text(encoding='utf-8'))
        for name in filenames | {'edition.json'}: snapshot['files'][FOLDER + '/' + name] = metadata(folder / name)
        snapshot['active_transport_edition'] = {'version': VERSION, 'manifest': MANIFEST}
        snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        checked = load_app_data(root)
        for profile in ('VC', 'VU'):
            print(profile, int(checked['ivp_general'].loc[checked['ivp_general'].perfil_vivienda.eq(profile), 'valor_indice'].notna().sum()), 'con índice')
    print('Prepared', VERSION, 'coverage', int(metrics.puntuacion_transporte.notna().sum()), '/', len(metrics), 'local activation', activate_local)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offer', type=Path, required=True)
    parser.add_argument('--coverage', type=Path, required=True)
    parser.add_argument('--activate-local', action='store_true')
    parser.add_argument('--evidence', type=Path, action='append', default=[])
    parser.add_argument('--publication-status', choices=['candidate_not_published','active'], default='candidate_not_published')
    args = parser.parse_args()
    prepare(ROOT, args.offer, args.coverage, args.activate_local, args.evidence, args.publication_status)
