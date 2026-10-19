import unittest
from copy import deepcopy
import numpy as np
from src.transport_offer_model import score_transport_offer


def fixture():
    dates = ['2026-10-' + str(x) for x in range(19, 26)]
    rows = {}
    for i, code in enumerate(['28001', '28002', '28003']):
        rows[code] = {'frequency': float(i * 10), 'connectivity': float(i * 2),
                      'has_scheduled_data': True, 'covered_reference_days': 7,
                      'cobertura_presentes': ['test'],
                      **{k: {'frequency': float(i * 10), 'connectivity': float(i * 2)}
                         for k in ['weekdays', 'saturday', 'sunday']}}
    return {'dates': dates, 'municipalities': rows}


class TransportOfferModelTests(unittest.TestCase):
    def test_higher_components_have_higher_scores_without_mutating_inputs(self):
        data = fixture(); before = deepcopy(data)
        table, scores, refs = score_transport_offer(data, list(data['municipalities']), {})
        np.testing.assert_allclose(table.puntuacion_transporte, [0, 50, 100])
        self.assertEqual(data, before)
        self.assertEqual(refs['minmax']['frecuencia']['direction'], 'mayor_mejor')
        self.assertEqual(scores['minmax'].loc['28002', 'transporte'], 50)

    def test_known_coverage_gap_remains_missing_instead_of_zero(self):
        data = fixture()
        table, scores, refs = score_transport_offer(data, list(data['municipalities']),
                                                  {'28001': ['Falta horario de una línea oficial']})
        self.assertTrue(table.loc[0, 'puntuacion_transporte'] != table.loc[0, 'puntuacion_transporte'])
        self.assertEqual(table.loc[0, 'expediciones_dia'], 0)
        self.assertFalse(table.loc[0, 'puntuable'])
        self.assertEqual(refs['minmax']['frecuencia']['n_observaciones'], 2)

    def test_out_of_range_calendar_is_not_scored(self):
        data = fixture(); data['municipalities']['28003']['covered_reference_days'] = 6
        table, _, _ = score_transport_offer(data, list(data['municipalities']), {})
        self.assertTrue(np.isnan(table.loc[2, 'puntuacion_transporte']))


if __name__ == '__main__': unittest.main()
