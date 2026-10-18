"""Deployment integrity, portability and unchanged production IVP."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.app_data import load_app_data, snapshot_signature, AppDataError
from src.app_map import load_map_data
from src.app_services import load_service_tables
from src.app_housing_expansion import load_expansion
from src.app_living_data import load_living_data, timetable
from src.runtime_snapshot import MANIFEST, read_snapshot
from src.service_origins import load_urban_origins

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless((ROOT / MANIFEST).exists(), 'Deployment checkout only; research checks run separately')
class CloudSnapshotTests(unittest.TestCase):
    def test_all_runtime_features_load_with_unchanged_ivp(self):
        data = load_app_data(ROOT)
        self.assertEqual(len(data['municipios']), 155)
        maps = load_map_data(ROOT)
        self.assertEqual(len(maps['geojson']['features']), 179)
        self.assertEqual(maps['verificacion']['archivos_verificados'], 4)
        self.assertEqual(len(load_urban_origins(ROOT, maps)), 155)
        self.assertTrue(load_service_tables(ROOT))
        expansion = load_expansion(ROOT)
        self.assertEqual(expansion['metodologia']['estado'], 'propuesta_no_activada')
        self.assertEqual(len(expansion['contexto']), 155)
        original = json.loads((ROOT / 'data/processed/housing_expansion/manifest.json').read_text())
        for name, expected in original['original_sha256'].items():
            self.assertEqual(hashlib.sha256((ROOT / 'data/processed/ivp' / name).read_bytes()).hexdigest(), expected)
        living = load_living_data(ROOT)
        from datetime import date
        self.assertTrue(timetable(living, '28120', date(2026, 10, 18))['available'])
        self.assertTrue(snapshot_signature(ROOT))

    def test_manifest_rejects_traversal_missing_records_and_mismatched_metadata(self):
        original = json.loads((ROOT / MANIFEST).read_text())
        for kind in ('traversal', 'missing', 'mismatch'):
            value = copy.deepcopy(original)
            if kind == 'traversal':
                value['files']['../outside'] = next(iter(value['files'].values()))
            elif kind == 'missing':
                value['files'].pop('data/reference/living/timetable.sqlite')
            else:
                value['phases']['2']['archivos']['maestro_municipios.csv']['sha256'] = '0' * 64
            with patch('src.runtime_snapshot.json.loads', return_value=value):
                with self.assertRaises(AppDataError):
                    read_snapshot(ROOT)

    def test_corruption_fails_before_loading_tables(self):
        from src.app_data import _verify_snapshot
        phases, records = read_snapshot(ROOT)
        first = next(iter(records))
        records[first] = {**records[first], 'sha256': '0' * 64}
        with patch('src.app_data._snapshot_records', return_value=(phases, records)):
            with self.assertRaisesRegex(AppDataError, 'Huella distinta'):
                _verify_snapshot(ROOT)
