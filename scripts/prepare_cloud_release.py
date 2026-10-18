#!/usr/bin/env python3
"""Export a review-only runtime checkout from the verified research project.

Never pushes or publishes. Refuses existing destinations. Run with the full
research environment because the original ancestry must verify before export.
"""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.app_data import MANIFESTS, TABLES, JSON_FILES, _verify_snapshot, _snapshot_records
from src.runtime_snapshot import SERVICE_FILES, MAP_FILES, REFERENCE_FILES, MANIFEST


def metadata(path):
    with path.open('rb') as handle:
        return {'sha256': hashlib.file_digest(handle, 'sha256').hexdigest(), 'bytes': path.stat().st_size}


def export(root, destination):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    if destination.exists() or destination.is_relative_to(root):
        raise ValueError('Use a new destination outside the research project')
    if (root / MANIFEST).exists():
        raise ValueError('Export requires the original research snapshot')
    phases, verification = _verify_snapshot(root)
    _, ancestors = _snapshot_records(root)
    _, all_outputs = _snapshot_records(root, include_all_outputs=True)
    for path, expected in all_outputs.items():
        actual = metadata(path)
        if actual['sha256'] != expected['sha256'] or ('bytes' in expected and actual['bytes'] != expected['bytes']):
            raise ValueError('Changed research output: ' + str(path.relative_to(root)))
    selected = set(SERVICE_FILES + MAP_FILES + REFERENCE_FILES)
    selected.update(str(Path(MANIFESTS[phase]).parent / name)
                    for phase, name in (*TABLES.values(), *JSON_FILES.values()))
    selected.add('data/reference/urban_centres/ign_capitales_seleccionadas.geojson')
    for directory, patterns in {
        'data/processed/housing_expansion': ('*',),
        'data/processed/ine_municipal': ('*',),
        'data/raw/ine_municipal': ('*',),
        'data/processed/data_quality': ('*',),
        'data/reference/municipality_photos': ('*.webp',),
        'data/reference/housing_expansion': ('*_metadata.json',),
    }.items():
        for pattern in patterns:
            selected.update(str(path.relative_to(root)) for path in (root / directory).glob(pattern) if path.is_file())
    destination.mkdir(parents=True)
    try:
        transformations = []
        for name in sorted(selected):
            source, target = root / name, destination / name
            if source.is_symlink() or not source.is_file():
                raise ValueError('Missing file or symlink: ' + name)
            target.parent.mkdir(parents=True, exist_ok=True)
            content = source.read_bytes()
            # These strings identify one developer's home directory. They are
            # not municipal observations. Preserve CSV quoting, BOM and values.
            if name == 'data/processed/indicadores/fuentes_indicadores.csv':
                text = content.decode('utf-8-sig')
                import re
                text, count = re.subn(r'/Users/[^,\n"\r]*/HabitaMadrid/', '', text)
                if count:
                    content = b'\xef\xbb\xbf' + text.encode('utf-8')
                    transformations.append({'path': name, 'operation': 'personal_path_prefix_to_relative',
                                            'replacements': count, 'original': metadata(source)})
            target.write_bytes(content)
        files = {name: metadata(destination / name) for name in sorted(selected)}
        phase_metadata = {}
        for phase, original in phases.items():
            phase_metadata[phase] = {key: original[key] for key in ('fase', 'generado_utc', 'sanidad') if key in original}
            parent = Path(MANIFESTS[phase]).parent
            phase_metadata[phase]['archivos'] = {Path(name).name: record for name, record in files.items()
                                                if Path(name).parent == parent}
        provenance = {'schema': 1, 'research_verification': verification,
                      'original_phase6_manifest': metadata(root / MANIFESTS[6]),
                      'ancestry': {str(path.relative_to(root)): {'sha256': record['sha256'],
                                   **({'bytes': record['bytes']} if 'bytes' in record else {})}
                                   for path, record in sorted(ancestors.items())},
                      'metadata_transformations': transformations}
        proof = destination / 'data/runtime_provenance.json'
        proof.write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        files['data/runtime_provenance.json'] = metadata(proof)
        snapshot = {'schema': 1, 'ivp_version': '1.0.0', 'phases': phase_metadata, 'files': files}
        (destination / MANIFEST).write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        for directory in ('src', 'assets'):
            shutil.copytree(root / directory, destination / directory,
                            ignore=shutil.ignore_patterns('__pycache__', '.DS_Store', '*.pyc'))
        for name in ('app.py', 'requirements.txt', 'requirements-dev.txt', '.gitignore', '.streamlit/config.toml', 'constraints.txt', '.github/workflows/ci.yml',
                     'scripts/prepare_cloud_release.py', 'scripts/prepare_housing_expansion.py',
                     'scripts/download_ine_municipal.py', 'scripts/prepare_ine_municipal.py',
                     'scripts/audit_data_quality.py', 'scripts/verify_housing_source.py', 'scripts/verify_sector_sources.py'):
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / name, target)
        shutil.copytree(root / 'deployment', destination / 'deployment')
        shutil.copyfile(root / 'deployment/README.md', destination / 'README.md')
        (destination / 'docs').mkdir()
        for name in ('DESPLIEGUE.md', 'FUENTES_Y_LICENCIAS.md'):
            shutil.copyfile(root / 'deployment' / name, destination / 'docs' / name)
        shutil.copyfile(root / 'docs/AUDITORIA_2026-10-18.md', destination / 'docs/AUDITORIA_2026-10-18.md')
        # Runtime tests do not require excluded research archives or pipelines.
        (destination / 'tests').mkdir()
        for name in ('test_cloud_snapshot.py', 'test_ine_municipal.py', 'test_data_quality.py', 'test_navigation.py', 'test_map_selection.py', 'test_view_navigation.py', 'test_living_features.py',
                     'test_living_ui.py', 'test_app_identity.py', 'test_housing_expansion.py',
                     'test_app_explorer.py', 'test_app_comparison.py', 'test_app_service_map.py',
                     'test_app_presenter.py', 'test_service_routes.py', 'test_ivp_calculation.py'):
            shutil.copyfile(root / 'tests' / name, destination / 'tests' / name)
        return {'data_files': len(files), 'data_bytes': sum(x['bytes'] for x in files.values()),
                'research_files_verified': verification['archivos_verificados']}
    except Exception:
        shutil.rmtree(destination)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    print(json.dumps(export(ROOT, args.destination), indent=2))
