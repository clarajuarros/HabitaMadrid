"""Real candidate edition: independent arithmetic, lineage and missing coverage."""
from copy import deepcopy
from pathlib import Path
import json
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
from src.app_data import load_app_data, municipal_view, AppDataError, _read_csv
from src.app_explorer import build_explorer_frame, filter_explorer
from src.app_housing_expansion import build_proposal_frame, load_expansion
from src.transport_edition import verify_transport_calculation, FOLDER
from src.integration_common import IntegrationError
from src.runtime_snapshot import read_snapshot

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless((ROOT / FOLDER / 'edition.json').exists(), 'Transport candidate only')
class TransportEditionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_app_data(ROOT)
        cls.base = load_app_data(ROOT, include_transport_revision=False)

    def test_active_revision_reproduces_four_dimension_mean_ranks_and_missingness(self):
        self.assertEqual(self.base['configuracion']['metodologia_version'], 'ivp-1.0.0')
        self.assertEqual(self.data['configuracion']['metodologia_version'], 'ivp-2.0.0-transporte-programado')
        self.assertEqual(self.data['transporte_oferta'].puntuacion_transporte.notna().sum(), 154)
        for profile in ('VC', 'VU'):
            frame = build_explorer_frame(self.data, profile)
            cols = ['puntuacion_' + d for d in ('sanidad', 'educacion', 'transporte', 'vivienda')]
            expected = frame[cols].sum(axis=1, min_count=4)/4
            np.testing.assert_allclose(frame.ivp_general, expected, equal_nan=True)
            np.testing.assert_allclose(frame.ivp_personalizado, expected, equal_nan=True)
            np.testing.assert_allclose(frame.posicion_general, expected.rank(method='min', ascending=False), equal_nan=True)
            for dimension in ('sanidad', 'educacion', 'vivienda'):
                before = build_explorer_frame(self.base, profile).set_index('codigo_ine')
                after = frame.set_index('codigo_ine').reindex(before.index)
                np.testing.assert_allclose(before['puntuacion_'+dimension], after['puntuacion_'+dimension], equal_nan=True)

    def test_partial_counts_never_fall_back_to_stop_distance_or_redistribute_weights(self):
        frame = build_explorer_frame(self.data, 'VU')
        missing = frame.puntuacion_transporte.isna()
        self.assertEqual(missing.sum(), 1)
        self.assertEqual(frame.loc[missing, 'codigo_ine'].tolist(), ['28130'])
        self.assertTrue(frame.loc[missing, 'ivp_general'].isna().all())
        self.assertTrue(frame.loc[missing, 'ivp_personalizado'].isna().all())
        self.assertTrue(frame.loc[missing, 'distancia_transporte_m'].notna().all())
        without = build_explorer_frame(self.data, 'VU', {'sanidad':1, 'educacion':1, 'transporte':0, 'vivienda':0})
        self.assertTrue(without.ivp_personalizado.notna().all())
        self.assertTrue(without.alcance_personalizado.str.startswith('parcial').all())

    def test_humanes_c5_is_scored_only_after_all_orphans_have_source_backed_routes(self):
        row = self.data['transporte_oferta'].set_index('codigo_ine').loc['28073']
        self.assertTrue(pd.notna(row.puntuacion_transporte))
        audit = json.loads((ROOT / FOLDER / 'renfe_prefix_repairs.json').read_text())
        self.assertEqual(audit['repaired_original_trips'], 31)
        self.assertEqual(audit['dated_repairs'], 31)
        c5 = [r for r in audit['records'] if r['train_number'] == '17707']
        self.assertEqual(len(c5), 5)
        for record in c5:
            self.assertEqual(record['declared_terminus'], 'Fuenlabrada')
            self.assertNotIn('Humanes', [s['name'] for s in record['prefix_stations']])
            self.assertGreater(record['onward_stations_excluded_to_avoid_duplicate'], 0)
        for profile in ('VC', 'VU'):
            ficha = build_explorer_frame(self.data, profile).set_index('codigo_ine').loc['28073']
            self.assertTrue(pd.notna(ficha.ivp_general))

    def test_filters_leave_normalization_and_scores_unchanged(self):
        full = build_explorer_frame(self.data, 'VC')
        subset = filter_explorer(full, population_range=(1000, 10000), only_with_index=True)
        original = full.set_index('codigo_ine').reindex(subset.codigo_ine)
        np.testing.assert_allclose(original.puntuacion_transporte, subset.puntuacion_transporte, equal_nan=True)
        np.testing.assert_allclose(original.ivp_general, subset.ivp_general, equal_nan=True)

    def test_sources_are_per_municipality_and_sheet_exposes_offer(self):
        observations = self.data['observaciones'].loc[self.data['observaciones'].indicador_id.eq('oferta_conectividad_programada')].set_index('codigo_ine')
        for row in self.data['transporte_oferta'].itertuples():
            feeds = json.loads(row.fuentes_json)
            sources = json.loads(observations.loc[row.codigo_ine, 'fuentes_json'])
            self.assertEqual('renfe_horarios_cercanias' in sources, 'cercanias_renfe' in feeds)
            self.assertEqual('crtm_horarios_pdf' in sources, any(f.startswith('crtm_pdf_') for f in feeds))
        self.assertIsNotNone(municipal_view(self.data, '28120', 'VU')['transporte_oferta'])
        with self.assertRaisesRegex(IntegrationError, 'histórica'):
            build_proposal_frame(self.data, load_expansion(ROOT), 'VU')

    def test_daily_inconsistency_and_wrong_scores_fail_even_with_readable_tables(self):
        data = deepcopy(self.data)
        data['transporte_oferta'].loc[0, 'expediciones_dia'] += 1
        with self.assertRaisesRegex(IntegrationError, 'evidencia diaria'):
            verify_transport_calculation(data, ROOT / FOLDER)

    def test_active_manifest_requires_every_derived_file(self):
        original = json.loads((ROOT / 'data/runtime_snapshot.json').read_text())
        altered = deepcopy(original)
        altered['files'].pop(FOLDER + '/offer_daily.json')
        loads = json.loads
        original_text = (ROOT / 'data/runtime_snapshot.json').read_text()
        with patch('src.runtime_snapshot.json.loads', side_effect=lambda text, *a, **kw: altered if text == original_text else loads(text, *a, **kw)):
            with self.assertRaisesRegex(AppDataError, 'incompleto'):
                read_snapshot(ROOT)


if __name__ == '__main__': unittest.main()
