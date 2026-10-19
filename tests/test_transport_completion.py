"""Published-source regressions for the 38-municipality completion."""
from collections import Counter
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest

from src.app_context import transport_lines, transport_verification
from src.integration_common import IntegrationError
from src.transport_completion import pdf_contributions, verify_completion_evidence

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'data/processed/transporte_oferta'


class TransportCompletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.evidence = json.loads((FOLDER / 'dated_pdf_journeys.json').read_text())
        cls.offer = json.loads((FOLDER / 'offer_daily.json').read_text())
        cls.rows = cls.evidence['journeys']
        cls.pdf = pdf_contributions(cls.evidence, cls.offer['dates'], cls.offer['daily'])

    def test_published_weekday_weekend_and_friday_counts(self):
        counts = Counter((r['line'], r['direction'], r['date']) for r in self.rows)
        # Independent poster totals; 721 extensions are subsets, not extras.
        for day in ('2026-10-19', '2026-10-20', '2026-10-21', '2026-10-22'):
            self.assertEqual(counts['535', '1', day], 16)
            self.assertEqual(counts['635', '1', day], 18)
            self.assertEqual(counts['721', '1', day], 4)
            self.assertEqual(counts['721', '2', day], 6)
            self.assertEqual(counts['728', '1', day], 2)
            self.assertEqual(counts['728', '2', day], 3)
        self.assertEqual(counts['721', '1', '2026-10-23'], 10)
        self.assertEqual(counts['721', '2', '2026-10-23'], 13)
        for day in ('2026-10-24', '2026-10-25'):
            self.assertEqual(counts['535', '1', day], 8)
            self.assertEqual(counts['FS1', '1', day], 15)
            self.assertEqual(counts['728', '1', day], 0)
            self.assertEqual(counts['727', '1', day], 0)

    def test_337_villaconejos_annotation_and_whole_line_replacement(self):
        branch = [r for r in self.rows if r['source_row'] == '8337H2.pdf#section1:service14']
        self.assertEqual(len(branch), 5)
        for row in branch:
            self.assertEqual(row['visits'][0]['codigo_ine'], '28170')
            self.assertEqual(row['visits'][0]['departure_seconds'], 8 * 3600 + 45 * 60)
        self.assertIn('8__337___', self.offer['feeds']['interurbanos']['excluded_route_ids'])

    def test_cobena_circular_vehicle_is_not_duplicated_by_two_pdf_sheets(self):
        n103 = [r for r in self.rows if r['line'] == 'N103' and r['date'] == '2026-10-19']
        self.assertEqual(len(n103), 4)  # One circular, two outward, one return.
        circular = [r for r in n103 if any(v['codigo_ine'] == '28041' for v in r['visits'])]
        self.assertEqual(len(circular), 1)
        contribution = pdf_contributions({**self.evidence, 'journeys': n103}, self.offer['dates'], ['28041'])
        self.assertEqual(contribution['28041']['2026-10-19']['frequency'], 1)
        self.assertEqual(contribution['28041']['2026-10-19']['destinations'], {'28134', '28006', '28079'})

    def test_n908_has_canada_and_enforces_madrid_las_rozas_restrictions(self):
        rows = [r for r in self.rows if r['line'] == 'N908' and r['date'] == '2026-10-19']
        for row in rows:
            self.assertIn('28176', [v['codigo_ine'] for v in row['visits']])
            out = pdf_contributions({**self.evidence, 'journeys': [row]}, self.offer['dates'], ['28079', '28127'])
            self.assertNotIn('28127', out['28079']['2026-10-19']['destinations'])
            self.assertNotIn('28079', out['28127']['2026-10-19']['destinations'])
        saturday = [r for r in self.rows if r['line'] == 'N908' and r['date'] == '2026-10-24' and 's' in r['notes']]
        self.assertEqual(len(saturday), 1)
        self.assertEqual([v['codigo_ine'] for v in saturday[0]['visits']][-2:], ['28095', '28042'])

    def test_unpublished_passage_times_stay_missing_and_635_school_trips_start_locally(self):
        school = [r for r in self.rows if r['line'] == '635' and 'school_days_only' in r['restrictions']]
        self.assertEqual(len(school), 20)
        for row in school:
            self.assertNotIn('28079', [v['codigo_ine'] for v in row['visits']])
        n908 = next(r for r in self.rows if r['line'] == 'N908')
        canada = next(v for v in n908['visits'] if v['codigo_ine'] == '28176')
        self.assertIsNone(canada['departure_seconds'])

    def test_boundary_station_is_corrected_by_exact_official_itinerary(self):
        repairs = json.loads((FOLDER / 'topology_repairs.json').read_text())['records']
        inbound = [r for r in repairs if r['line'] in ('640', '665') and '28183' in r['verified_municipal_sequence']]
        self.assertGreater(len(inbound), 0)
        self.assertTrue(all(r['shape_id'] and r['official_itinerary_id'] for r in inbound))
        self.assertTrue(any(r['line'] == '691' and '28038' in r['verified_municipal_sequence'] for r in repairs))

    def test_nearby_lines_are_not_shown_as_local_and_demand_rule_is_consistent(self):
        for code, excluded in [('28004', {'528'}), ('28130', {'287', '289'})]:
            _, row = transport_lines(ROOT, code)
            self.assertTrue(excluded.isdisjoint({r['line'] for r in row['lines']}))
            self.assertEqual(set(row['nearby_lines']), excluded)
        for code in ('28027', '28114', '28088', '28117', '28078', '28070', '28071', '28069', '28118'):
            self.assertEqual(transport_verification(ROOT, code)['demand_responsive']['line'], 'SD1')

    def test_total_daily_evidence_reproduces_independent_components_and_rejects_tamper(self):
        verify_completion_evidence(FOLDER, self.offer)
        altered = deepcopy(self.offer)
        altered['daily']['28041']['2026-10-19']['frequency'] += 1
        with self.assertRaisesRegex(IntegrationError, 'total diario'):
            verify_completion_evidence(FOLDER, altered)
        duplicate = deepcopy(self.evidence)
        duplicate['journeys'].append(duplicate['journeys'][0])
        with self.assertRaisesRegex(IntegrationError, 'repetida'):
            pdf_contributions(duplicate, self.offer['dates'], self.offer['daily'])


if __name__ == '__main__':
    unittest.main()
