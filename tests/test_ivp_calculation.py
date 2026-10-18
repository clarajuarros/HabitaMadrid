"""Contratos matemáticos y ausencias del motor del IVP de la fase 6."""
import copy
import json
import unittest

import numpy as np
import pandas as pd

from src.integration_common import IntegrationError
from src.ivp_calculation import (DIMENSIONS, calculate_index, fit_normalization,
                                 normalize_weights, transform_normalization)


def series(values, codes=None):
    return pd.Series(values, index=codes, name="indicador", dtype=object)


def score_frame(rows, codes=None):
    return pd.DataFrame(rows, columns=DIMENSIONS,
                        index=pd.Index(codes or [f"28{i+1:03d}" for i in range(len(rows))],
                                       name="codigo_ine"))


class NormalizationTests(unittest.TestCase):
    def test_minmax_direction_zero_and_missing(self):
        values = series([0, 5, 10, None, np.nan, pd.NA], list("abcdef"))
        reference = fit_normalization(values)
        result = transform_normalization(values, reference)
        self.assertEqual(result.index.tolist(), list("abcdef"))
        self.assertEqual(result.name, "indicador")
        self.assertEqual(result.iloc[:3].tolist(), [100, 50, 0])
        self.assertTrue(result.iloc[3:].isna().all())
        other = transform_normalization(values, fit_normalization(values, direction="mayor_mejor"))
        self.assertEqual(other.iloc[:3].tolist(), [0, 50, 100])

    def test_positive_affine_change_of_units_preserves_scores(self):
        values = series([0, 1.5, 10, 20, None])
        for method in ["minmax", "winsor_p05_p95", "percentil"]:
            with self.subTest(method=method):
                original = transform_normalization(values, fit_normalization(values, method=method))
                converted = series([None if value is None else value * 1000 + 7 for value in values])
                transformed = transform_normalization(converted, fit_normalization(converted, method=method))
                np.testing.assert_allclose(original, transformed, equal_nan=True, rtol=1e-13)

    def test_filtered_rows_keep_complete_population_reference(self):
        population = series([0, 10, 20], ["28001", "28002", "28003"])
        reference = fit_normalization(population)
        snapshot = copy.deepcopy(reference)
        subset = population.loc[["28003", "28002"]]
        result = transform_normalization(subset, reference)
        self.assertEqual(result.tolist(), [0, 50])
        self.assertEqual(result.index.tolist(), ["28003", "28002"])
        self.assertEqual(reference, snapshot)
        self.assertEqual(transform_normalization(subset, fit_normalization(subset)).tolist(), [0, 100])

    def test_outside_values_are_bounded_without_refitting(self):
        reference = fit_normalization(series([0, 10, 20]))
        self.assertEqual(transform_normalization(series([-100, 0, 20, 100]), reference).tolist(),
                         [100, 100, 0, 0])
        self.assertEqual(reference["maximo_observado"], 20)

    def test_no_observations_produces_no_scores_even_for_new_data(self):
        for method in ["minmax", "winsor_p05_p95", "percentil"]:
            reference = fit_normalization(series([None, np.nan]), method=method)
            self.assertEqual(reference["estado"], "sin_observaciones")
            self.assertEqual(reference["n_observaciones"], 0)
            self.assertTrue(transform_normalization(series([1, 2, None]), reference).isna().all())
            json.dumps(reference, allow_nan=False)

    def test_constant_reference_explicitly_assigns_neutral_fifty(self):
        for method in ["minmax", "winsor_p05_p95", "percentil"]:
            for direction in ["menor_mejor", "mayor_mejor"]:
                reference = fit_normalization(series([7, 7, None]), method=method, direction=direction)
                self.assertEqual(reference["estado"], "constante")
                result = transform_normalization(series([-3, 7, 99, None]), reference)
                self.assertEqual(result.iloc[:3].tolist(), [50, 50, 50])
                self.assertTrue(pd.isna(result.iloc[3]))

    def test_winsor_uses_linear_quantiles_and_preserves_original_values(self):
        values = series([*range(100), 1000])
        original = values.copy(deep=True)
        reference = fit_normalization(values, method="winsor_p05_p95")
        self.assertEqual(reference["limite_inferior"], 5)
        self.assertEqual(reference["limite_superior"], 95)
        self.assertEqual(reference["maximo_observado"], 1000)
        result = transform_normalization(values, reference)
        self.assertTrue(result.iloc[:6].eq(100).all())
        self.assertTrue(result.iloc[95:].eq(0).all())
        self.assertEqual(result.iloc[50], 50)
        pd.testing.assert_series_equal(original, values)

    def test_percentile_ties_match_average_ranks_of_reference_population(self):
        values = series([3, 1, 1, 2, None], list("edcba"))
        reference = fit_normalization(values, method="percentil")
        expected = 100 * (values.dropna().astype(float).rank(method="average") - 1) / 3
        higher = transform_normalization(values,
                                         fit_normalization(values, method="percentil", direction="mayor_mejor"))
        np.testing.assert_allclose(higher.loc[expected.index], expected)
        lower = transform_normalization(values, reference)
        np.testing.assert_allclose(lower.loc[expected.index], 100 - expected)
        self.assertEqual(lower.loc["d"], lower.loc["c"])
        self.assertTrue(pd.isna(lower.loc["a"]))
        self.assertEqual(reference["valores_ordenados"], [1, 1, 2, 3])

    def test_percentile_fixed_reference_new_extremes_and_insertions(self):
        reference = fit_normalization(series([0, 10, 20]), method="percentil", direction="mayor_mejor")
        result = transform_normalization(series([-1, 0, 5, 10, 15, 20, 21]), reference)
        self.assertEqual(result.tolist(), [0, 0, 25, 50, 75, 100, 100])
        self.assertEqual(transform_normalization(series([10]), reference).tolist(), [50])

    def test_methods_and_directions_reject_unknown_options(self):
        for kwargs in [{"method": "zscore"}, {"direction": "neutro"}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(IntegrationError):
                fit_normalization(series([0, 1]), **kwargs)

    def test_bad_values_are_never_silently_coerced(self):
        valid = fit_normalization(series([1, 2]))
        for bad in ["1", "", True, False, np.bool_(True), float("inf"), float("-inf"), 1j, pd.NaT]:
            with self.subTest(bad=repr(bad)), self.assertRaises(IntegrationError):
                fit_normalization(series([0, bad]))
            with self.subTest(transform=repr(bad)), self.assertRaises(IntegrationError):
                transform_normalization(series([bad]), valid)
        with self.assertRaises(IntegrationError):
            fit_normalization([1, 2])

    def test_extreme_finite_values_do_not_overflow(self):
        values = series([-1.7e308, 0, 1.7e308])
        for method in ["minmax", "winsor_p05_p95", "percentil"]:
            reference = fit_normalization(values, method=method)
            result = transform_normalization(values, reference)
            self.assertEqual(result.tolist(), [100, 50, 0])
            json.dumps(reference, allow_nan=False)

    def test_reference_roundtrip_is_strict_json_and_rejects_corruption(self):
        for method in ["minmax", "winsor_p05_p95", "percentil"]:
            values = series([0, 1, 2, None])
            reference = fit_normalization(values, method=method)
            copied = json.loads(json.dumps(reference, allow_nan=False))
            pd.testing.assert_series_equal(transform_normalization(values, reference),
                                           transform_normalization(values, copied))
        reference = fit_normalization(series([0, 10]), method="percentil")
        mutations = [{"limite_superior": float("inf")}, {"limite_inferior": True},
                     {"valores_ordenados": [10, 0]}, {"n_observaciones": 3},
                     {"estado": "constante"}, {"direction": "desconocido"},
                     {"version": True}]
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(IntegrationError):
                transform_normalization(series([0]), {**reference, **mutation})


class WeightTests(unittest.TestCase):
    def test_weights_normalize_without_mutating_input(self):
        weights = dict(zip(DIMENSIONS, [10, 20, 30, 40]))
        snapshot = weights.copy()
        self.assertEqual(normalize_weights(weights), dict(zip(DIMENSIONS, [.1, .2, .3, .4])))
        self.assertEqual(weights, snapshot)
        self.assertEqual(normalize_weights(dict.fromkeys(DIMENSIONS, 1e308)),
                         dict.fromkeys(DIMENSIONS, .25))

    def test_weights_require_exact_keys_real_nonnegative_finite_values_and_positive_total(self):
        good = dict.fromkeys(DIMENSIONS, 1)
        cases = [{}, {**good, "otra": 1}, {key: 1 for key in DIMENSIONS[:-1]},
                 dict.fromkeys(DIMENSIONS, 0)]
        for value in [True, np.bool_(False), -1, float("nan"), float("inf"), "1", None, 1j]:
            cases.append({**good, "sanidad": value})
        for weights in cases:
            with self.subTest(weights=weights), self.assertRaises(IntegrationError):
                normalize_weights(weights)


class IndexCalculationTests(unittest.TestCase):
    def test_weighted_sum_and_zero_values_are_observed(self):
        scores = score_frame([[0, 100, 50, 100], [100, 0, 100, 0]])
        result = calculate_index(scores, dict(zip(DIMENSIONS, [1, 2, 3, 4])))
        np.testing.assert_allclose(result.valor_indice, [75, 40])
        self.assertEqual(result.estado_indice.tolist(), ["disponible", "disponible"])
        np.testing.assert_allclose(result.peso_disponible, [1, 1])
        self.assertEqual(result.posicion.tolist(), [1, 2])
        pd.testing.assert_index_equal(result.index, scores.index)

    def test_missing_housing_blocks_instead_of_redistributing_its_weight(self):
        scores = score_frame([[100, 100, 100, np.nan], [0, 0, 0, 0]])
        result = calculate_index(scores, dict.fromkeys(DIMENSIONS, 1))
        self.assertTrue(pd.isna(result.valor_indice.iloc[0]))
        self.assertTrue(pd.isna(result.posicion.iloc[0]))
        self.assertEqual(result.peso_disponible.iloc[0], .75)
        self.assertEqual(json.loads(result.dimensiones_faltantes_json.iloc[0]), ["vivienda"])
        self.assertEqual(result.estado_indice.iloc[0], "dimensiones_faltantes")
        self.assertEqual(result.valor_indice.iloc[1], 0)
        self.assertEqual(result.posicion.iloc[1], 1)

    def test_global_health_block_applies_even_with_complete_observations(self):
        scores = score_frame([[100, 100, 100, 100], [50, 50, 50, 50]])
        result = calculate_index(scores, dict.fromkeys(DIMENSIONS, 1), blocked_dimensions=["sanidad"])
        self.assertTrue(result.valor_indice.isna().all())
        self.assertTrue(result.posicion.isna().all())
        self.assertTrue(result.peso_disponible.eq(.75).all())
        self.assertTrue(result.estado_indice.eq("dimensiones_bloqueadas").all())
        self.assertTrue(result.dimensiones_bloqueadas_json.map(json.loads).map(lambda value: value == ["sanidad"]).all())

    def test_zero_weight_ignores_missing_and_blocked_health(self):
        scores = score_frame([[np.nan, 30, 60, 90]])
        weights = dict(zip(DIMENSIONS, [0, 1, 1, 1]))
        result = calculate_index(scores, weights, blocked_dimensions=["sanidad"])
        self.assertEqual(result.valor_indice.iloc[0], 60)
        self.assertEqual(result.estado_indice.iloc[0], "disponible")
        self.assertEqual(result.peso_disponible.iloc[0], 1)
        self.assertEqual(json.loads(result.dimensiones_faltantes_json.iloc[0]), [])
        self.assertEqual(json.loads(result.dimensiones_bloqueadas_json.iloc[0]), [])

    def test_combined_status_and_json_order_follow_dimensions(self):
        scores = score_frame([[np.nan, 10, np.nan, np.nan]])
        result = calculate_index(scores, dict.fromkeys(DIMENSIONS, 1),
                                 blocked_dimensions=["vivienda", "sanidad"])
        self.assertEqual(result.estado_indice.iloc[0], "dimensiones_faltantes_y_bloqueadas")
        self.assertEqual(json.loads(result.dimensiones_faltantes_json.iloc[0]),
                         ["sanidad", "transporte", "vivienda"])
        self.assertEqual(json.loads(result.dimensiones_bloqueadas_json.iloc[0]), ["sanidad", "vivienda"])
        self.assertEqual(result.peso_disponible.iloc[0], .25)

    def test_ranking_preserves_ties_and_full_precision(self):
        scores = score_frame([[80] * 4, [90] * 4, [90] * 4, [90.0000000001] * 4],
                             ["28009", "28008", "28007", "28006"])
        result = calculate_index(scores, dict.fromkeys(DIMENSIONS, 1))
        self.assertEqual(result.posicion.tolist(), [4, 2, 2, 1])
        self.assertEqual(str(result.posicion.dtype), "Int64")
        self.assertGreater(result.valor_indice.iloc[3], result.valor_indice.iloc[2])
        self.assertEqual(result.index.tolist(), scores.index.tolist())

    def test_row_and_column_permutations_do_not_change_scores_or_positions(self):
        scores = score_frame([[10, 30, 50, 20], [90, 80, 70, 40], [50, 10, 0, np.nan]])
        weights = dict(zip(DIMENSIONS, [1, 2, 4, 3]))
        original = scores.copy(deep=True)
        result = calculate_index(scores, weights)
        permuted = scores.iloc[[2, 0, 1], ::-1]
        other = calculate_index(permuted, weights)
        pd.testing.assert_frame_equal(result, other.reindex(scores.index))
        pd.testing.assert_frame_equal(scores, original)

    def test_bad_frames_scores_and_blocked_dimensions_are_rejected(self):
        good = score_frame([[50] * 4])
        for frame in [good.drop(columns="vivienda"), good.assign(extra=0),
                      pd.concat([good, good]), pd.concat([good, good[["sanidad"]]], axis=1)]:
            with self.subTest(frame=frame.shape), self.assertRaises(IntegrationError):
                calculate_index(frame, dict.fromkeys(DIMENSIONS, 1))
        for bad in [-.1, 100.0001, float("inf"), "50", True]:
            frame = good.astype(object)
            frame.iloc[0, 0] = bad
            with self.subTest(bad=repr(bad)), self.assertRaises(IntegrationError):
                calculate_index(frame, dict.fromkeys(DIMENSIONS, 1))
        for blocked in ["sanidad", ["desconocida"], None]:
            with self.subTest(blocked=blocked), self.assertRaises(IntegrationError):
                calculate_index(good, dict.fromkeys(DIMENSIONS, 1), blocked_dimensions=blocked)

    def test_empty_population_keeps_schema_and_nullable_positions(self):
        result = calculate_index(score_frame([]), dict.fromkeys(DIMENSIONS, 1))
        self.assertTrue(result.empty)
        self.assertEqual(str(result.posicion.dtype), "Int64")
        self.assertEqual(result.columns.tolist(), ["valor_indice", "estado_indice", "peso_disponible",
                                                 "dimensiones_faltantes_json", "dimensiones_bloqueadas_json",
                                                 "posicion"])


if __name__ == "__main__":
    unittest.main()
