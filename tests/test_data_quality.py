"""Outliers are review signals; verified data and IVP stay unchanged."""
import unittest,hashlib
from pathlib import Path
import pandas as pd
from src.data_quality import audit,extreme_mask
ROOT=Path(__file__).resolve().parents[1]

class DataQualityTests(unittest.TestCase):
    def test_outliers_preserve_the_values_and_do_not_turn_missing_into_zero(self):
        values=pd.Series([1.,2.,3.,4.,100.,float('nan')]);original=values.copy(deep=True)
        flagged=extreme_mask(values)
        self.assertEqual(flagged.tolist(),[False,False,False,False,True,False])
        pd.testing.assert_series_equal(values,original)

    def test_complete_offline_audit_does_not_change_ivp(self):
        path=ROOT/'data/processed/ivp/ivp_general.csv';before=hashlib.sha256(path.read_bytes()).hexdigest()
        result=audit(ROOT)
        self.assertEqual(result['errores_confirmados'],0)
        self.assertEqual(result['municipios'],179)
        self.assertEqual(result['elegibles'],155)
        self.assertGreater(len(result['checks']),70)
        self.assertEqual(before,hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertTrue(any('50.120' in c['regla'] for c in result['checks']))
        self.assertFalse(any(a['estado']=='error_confirmado' for a in result['anomalies']))
