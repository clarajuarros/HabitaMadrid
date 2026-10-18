"""Portable deployment snapshot; research snapshots retain full ancestry checks.

The exporter verifies the research chain before emitting these records. Runtime
hashes detect changed or missing deployment files; they are not a signature or
an independent proof of source authenticity.
"""
import json
from pathlib import Path
import re

MANIFEST = 'data/runtime_snapshot.json'
SERVICE_FILES = (
 'data/processed/geografia/origenes_municipales.csv',
 'data/processed/geografia/centros_educativos_geografia.csv',
 'data/processed/geografia/paradas_geolocalizadas.csv',
 'data/processed/geografia/catalogo_servicios_geograficos.csv',
 'data/processed/integracion/transporte_rutas.csv',
 'data/processed/integracion/transporte_rutas_paradas.csv',
 'data/processed/ivp/sanidad_catalogo_revisado.csv',
)
MAP_FILES = ('data/processed/municipios/maestro_municipios.csv',
 'data/processed/geografia/limites_municipales.geojson',
 'data/processed/geografia/jurisdicciones_compartidas.geojson',
 'data/processed/geografia/origenes_municipales.geojson')
REFERENCE_FILES = ('data/processed/geografia/proximidad_servicios.csv',
 'data/reference/living/connectivity.json',
 'data/reference/living/transport.json', 'data/reference/living/timetable.sqlite',
 'data/reference/municipal_context/descriptions.json',
 'data/reference/municipal_context/transport_lines.json',
 'data/reference/municipality_photos/catalog.json',
 'data/reference/urban_centres/centros_urbanos.csv',
 'data/reference/urban_centres/manifest.json',
 'data/reference/urban_centres/ign_capitales_seleccionadas.geojson',
 'data/processed/housing_expansion/manifest.json',
 'data/reference/housing_expansion/sources.json',
 'data/processed/ine_municipal/manifest.json',
 'data/processed/ine_municipal/contexto_municipal.csv',
 'data/processed/ine_municipal/padron_historico_ine.csv',
 'data/raw/ine_municipal/sources.json',
 'data/raw/ine_municipal/ine_padron_2881.csv.gz',
 'data/raw/ine_municipal/ine_edades_68540.json.gz',
 'data/raw/ine_municipal/ine_hogares_59543.csv.gz',
 'data/raw/ine_municipal/ine_viviendas_59525.csv.gz',
 'data/processed/data_quality/housing_source_verification.json',
 'data/processed/data_quality/primary_health_verification.json')


def read_snapshot(root):
    from src.app_data import AppDataError, MANIFESTS, TABLES, JSON_FILES
    root = Path(root).resolve()
    try:
        value = json.loads((root / MANIFEST).read_text(encoding='utf-8'))
        if value.get('schema') != 1 or value.get('ivp_version') != '1.0.0':
            raise ValueError('Versión no reconocida')
        required = set(SERVICE_FILES + MAP_FILES + REFERENCE_FILES)
        required.update(str(Path(MANIFESTS[phase]).parent / name)
                        for phase, name in (*TABLES.values(), *JSON_FILES.values()))
        if not isinstance(value['files'], dict) or not required.issubset(value['files']):
            raise ValueError('Snapshot incompleto')
        photos = json.loads((root / 'data/reference/municipality_photos/catalog.json').read_text())
        for photo in photos.values():
            if photo.get('status') == 'available':
                filename = photo['file']
                if Path(filename).name != filename:
                    raise ValueError('Nombre de fotografía inválido')
                required.add('data/reference/municipality_photos/' + filename)
        housing = json.loads((root / 'data/processed/housing_expansion/manifest.json').read_text())
        for filename in housing['archivos']:
            if Path(filename).name != filename:
                raise ValueError('Nombre de vivienda inválido')
            required.add('data/processed/housing_expansion/' + filename)
        if not required.issubset(value['files']):
            raise ValueError('Faltan fotografías o archivos de vivienda')
        records = {}
        for name, record in value['files'].items():
            relative = Path(name)
            path = (root / relative).resolve()
            if relative.is_absolute() or not path.is_relative_to(root) or '..' in relative.parts:
                raise ValueError('Ruta fuera del proyecto')
            if not isinstance(record, dict) or not re.fullmatch(r'[0-9a-f]{64}', record.get('sha256', '')):
                raise ValueError('Huella inválida')
            if type(record.get('bytes')) is not int or record['bytes'] < 0:
                raise ValueError('Tamaño inválido')
            records[path] = record
        phases = {int(phase): item for phase, item in value['phases'].items()}
        if set(phases) != set(MANIFESTS) or any(item.get('fase') != phase for phase, item in phases.items()):
            raise ValueError('Metadatos de fases incompletos')
        for phase, name in (*TABLES.values(), *JSON_FILES.values()):
            path = (root / Path(MANIFESTS[phase]).parent / name).resolve()
            if phases[phase]['archivos'].get(name) != records[path]:
                raise ValueError('Metadatos incompatibles')
        return phases, records
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise AppDataError('Snapshot de despliegue inválido: ' + str(exc)) from exc
