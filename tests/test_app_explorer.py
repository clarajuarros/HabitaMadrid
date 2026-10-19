"""Fixed-reference exploration: weights, missing data, filters and honest ranks."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from src.app_data import load_app_data
from src.app_explorer import (
    FULL_SCOPE, PARTIAL_SCOPE, build_explorer_frame, filter_explorer, ranking_view,
)
from src.integration_common import IntegrationError
from src.ivp_calculation import DIMENSIONS, calculate_index


ROOT = Path(__file__).resolve().parents[1]


class ExplorerRealDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # These regression cases describe the sealed phase-six edition. The
        # active transport revision has its own real-data integration tests.
        cls.data = load_app_data(ROOT, include_transport_revision=False)
        cls.frames = {profile: build_explorer_frame(cls.data, profile) for profile in ("VC", "VU")}

    def test_complete_eligible_universe_and_schema(self):
        wanted = {"codigo_ine", "municipio", "poblacion", "perfil_vivienda", "ivp_general",
                  "posicion_general", "ivp_personalizado", "posicion_personalizada",
                  "estado_personalizado", "alcance_personalizado", "dimensiones_faltantes_json",
                  "dimensiones_bloqueadas_json", "bloqueadas", "pesos_json", "alquiler_m2_mes",
                  "distancia_sanidad_m", "distancia_educacion_m", "distancia_transporte_m",
                  *(f"puntuacion_{dimension}" for dimension in DIMENSIONS)}
        for profile, frame in self.frames.items():
            with self.subTest(profile=profile):
                self.assertEqual(len(frame), 155)
                self.assertTrue(frame.codigo_ine.is_unique)
                self.assertTrue(frame.codigo_ine.str.fullmatch(r"28\d{3}").all())
                self.assertEqual(set(frame.codigo_ine), set(self.data["municipios"].codigo_ine))
                self.assertTrue(frame.poblacion.le(50000).all())
                self.assertTrue(frame.perfil_vivienda.eq(profile).all())
                self.assertTrue(wanted.issubset(frame.columns))
                self.assertTrue(frame.attrs["anclas_fijas"])
                self.assertEqual(frame.attrs["universo_municipios"], 155)
                self.assertEqual(frame.attrs["normalizacion"], "minmax")

    def test_equal_weights_reproduce_phase_six_scores_missingness_and_ranks(self):
        for profile, frame in self.frames.items():
            with self.subTest(profile=profile):
                source = self.data["ivp_general"].loc[self.data["ivp_general"].perfil_vivienda.eq(profile)].set_index("codigo_ine")
                observed = frame.set_index("codigo_ine").reindex(source.index)
                np.testing.assert_allclose(observed.ivp_personalizado.to_numpy(dtype=float),
                                           source.valor_indice.to_numpy(dtype=float), rtol=0, atol=1e-12, equal_nan=True)
                pd.testing.assert_series_equal(observed.posicion_personalizada, source.posicion.astype("Int64"), check_names=False)
                self.assertEqual(observed.estado_personalizado.tolist(), source.estado_indice.tolist())
                self.assertEqual(observed.ivp_personalizado.notna().sum(), 113 if profile == "VC" else 139)
                self.assertTrue(observed.alcance_personalizado.eq(FULL_SCOPE).all())

    def test_explicit_zero_housing_makes_declared_partial_index_for_all_municipalities(self):
        weights = {"sanidad": 10, "educacion": 20, "transporte": 30, "vivienda": 0}
        for profile in ("VC", "VU"):
            with self.subTest(profile=profile):
                frame = build_explorer_frame(self.data, profile, weights)
                self.assertEqual(frame.ivp_personalizado.notna().sum(), 155)
                self.assertTrue(frame.alcance_personalizado.eq(PARTIAL_SCOPE).all())
                self.assertTrue(frame.dimensiones_faltantes_json.eq("[]").all())
                self.assertTrue(frame.estado_personalizado.eq("disponible").all())
                self.assertGreater(frame.alquiler_m2_mes.isna().sum(), 0)
                self.assertEqual(frame.attrs["pesos_normalizados"]["vivienda"], 0)
                self.assertEqual(weights, {"sanidad": 10, "educacion": 20, "transporte": 30, "vivienda": 0})

    def test_positive_missing_weight_never_redistributes_locally(self):
        frame = build_explorer_frame(self.data, "VU", {dimension: 1 for dimension in DIMENSIONS})
        missing = frame.loc[frame.alquiler_m2_mes.isna()]
        self.assertEqual(len(missing), 16)
        self.assertTrue(missing.ivp_personalizado.isna().all())
        self.assertTrue(missing.posicion_personalizada.isna().all())
        self.assertTrue(missing.estado_personalizado.eq("dimensiones_faltantes").all())
        self.assertTrue(missing.dimensiones_faltantes_json.map(json.loads).map(lambda value: value == ["vivienda"]).all())
        self.assertTrue(frame.pesos_json.map(json.loads).map(lambda weights: weights == {dimension: .25 for dimension in DIMENSIONS}).all())

    def test_zero_in_all_dimensions_is_rejected(self):
        with self.assertRaisesRegex(IntegrationError, "peso positivo"):
            build_explorer_frame(self.data, "VU", {dimension: 0 for dimension in DIMENSIONS})

    def test_invalid_weight_inputs_are_rejected(self):
        examples = [None, -1, np.nan, np.inf, True, "25", 1j]
        for invalid in examples:
            weights = {dimension: 25 for dimension in DIMENSIONS}
            weights["sanidad"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(IntegrationError):
                build_explorer_frame(self.data, "VU", weights)
        with self.assertRaises(IntegrationError):
            build_explorer_frame(self.data, "VU", {"sanidad": 1})

    def test_saved_dimensions_are_not_normalized_again(self):
        with patch("src.ivp_calculation.fit_normalization", side_effect=AssertionError("No new anchors")):
            frame = build_explorer_frame(self.data, "VC", {"sanidad": 4, "educacion": 3, "transporte": 2, "vivienda": 1})
        matrix = self.data["dimensiones"].loc[self.data["dimensiones"].perfil_vivienda.eq("VC")].set_index("codigo_ine")
        expected = calculate_index(matrix[list(DIMENSIONS)], {"sanidad": 4, "educacion": 3, "transporte": 2, "vivienda": 1})
        actual = frame.set_index("codigo_ine").reindex(matrix.index)
        np.testing.assert_allclose(actual.ivp_personalizado, expected.valor_indice, equal_nan=True)
        for dimension in DIMENSIONS:
            pd.testing.assert_series_equal(actual[f"puntuacion_{dimension}"], matrix[dimension], check_names=False)

    def test_blocked_dimension_applies_to_entire_personalized_scenario(self):
        changed = {**self.data, "configuracion": deepcopy(self.data["configuracion"])}
        changed["configuracion"]["bloqueos_dimensiones"] = ["sanidad"]
        blocked = build_explorer_frame(changed, "VU")
        self.assertTrue(blocked.ivp_personalizado.isna().all())
        self.assertTrue(blocked.dimensiones_bloqueadas_json.map(json.loads).map(lambda value: value == ["sanidad"]).all())
        pd.testing.assert_series_equal(blocked.ivp_general, self.frames["VU"].ivp_general)
        allowed = build_explorer_frame(changed, "VU", {"sanidad": 0, "educacion": 1, "transporte": 1, "vivienda": 1})
        self.assertEqual(allowed.ivp_personalizado.notna().sum(), 139)
        self.assertTrue(allowed.bloqueadas.eq("[]").all())
        self.assertTrue(allowed.alcance_personalizado.eq(PARTIAL_SCOPE).all())

    def test_observations_are_exact_and_profiles_do_not_substitute_housing(self):
        for profile, frame in self.frames.items():
            config = self.data["configuracion"]
            identifiers = {**config["indicadores_comunes"], "vivienda": config["perfiles"][profile]["indicador"]}
            columns = {"sanidad": "distancia_sanidad_m", "educacion": "distancia_educacion_m",
                       "transporte": "distancia_transporte_m", "vivienda": "alquiler_m2_mes"}
            for dimension, column in columns.items():
                observations = self.data["observaciones"]
                expected = observations.loc[observations.indicador_id.eq(identifiers[dimension])].set_index("codigo_ine").valor
                actual = frame.set_index("codigo_ine")[column].reindex(expected.index)
                pd.testing.assert_series_equal(actual, expected.astype(float), check_names=False)
        vc = self.frames["VC"].set_index("codigo_ine")
        vu = self.frames["VU"].set_index("codigo_ine")
        self.assertGreater((vc.alquiler_m2_mes.isna() & vu.alquiler_m2_mes.notna()).sum(), 0)
        self.assertTrue(vc.loc[vc.alquiler_m2_mes.isna() & vu.alquiler_m2_mes.notna(), "ivp_general"].isna().all())

    def test_build_does_not_mutate_snapshot_tables_or_configuration(self):
        before_tables = {name: value.copy(deep=True) for name, value in self.data.items() if isinstance(value, pd.DataFrame)}
        before_configuration = deepcopy(self.data["configuracion"])
        build_explorer_frame(self.data, "VU", {"sanidad": 0, "educacion": 3, "transporte": 1, "vivienda": 0})
        self.assertEqual(self.data["configuracion"], before_configuration)
        for name, original in before_tables.items():
            pd.testing.assert_frame_equal(self.data[name], original)

    def test_invalid_profile_or_incomplete_universe_is_rejected(self):
        with self.assertRaises(IntegrationError):
            build_explorer_frame(self.data, "vivienda")
        changed = {**self.data, "municipios": self.data["municipios"].iloc[:-1].copy()}
        with self.assertRaises(IntegrationError):
            build_explorer_frame(changed, "VU")
        changed = {**self.data, "dimensiones": self.data["dimensiones"].iloc[1:].copy()}
        with self.assertRaises(IntegrationError):
            build_explorer_frame(changed, "VC")

    def test_missing_raw_observation_is_not_silently_filled(self):
        changed = {**self.data, "observaciones": self.data["observaciones"].iloc[1:].copy()}
        with self.assertRaisesRegex(IntegrationError, "Observaciones incompletas"):
            build_explorer_frame(changed, "VC")

    def test_filters_preserve_scores_and_full_universe_ranks(self):
        frame = self.frames["VU"]
        before = frame.copy(deep=True)
        result = filter_explorer(frame, population_range=(0, 5000), max_rent=10,
                                 max_health_distance_m=1000, mode="personalizado")
        self.assertGreater(len(result), 0)
        self.assertLess(len(result), len(frame))
        columns = ["codigo_ine", "ivp_general", "posicion_general", "ivp_personalizado", "posicion_personalizada"]
        pd.testing.assert_frame_equal(result[columns], frame.loc[result.index, columns])
        self.assertEqual(result.attrs["universo_municipios"], 155)
        self.assertEqual(result.attrs["pesos_normalizados"], frame.attrs["pesos_normalizados"])
        pd.testing.assert_frame_equal(frame, before)

    def test_unlimited_filter_retains_all_missing_rents_and_indices(self):
        frame = self.frames["VU"]
        result = filter_explorer(frame)
        self.assertEqual(len(result), 155)
        self.assertEqual(result.alquiler_m2_mes.isna().sum(), frame.alquiler_m2_mes.isna().sum())
        self.assertEqual(result.ivp_general.isna().sum(), 16)

    def test_active_rent_filter_requires_observed_rent_even_for_partial_index(self):
        frame = build_explorer_frame(self.data, "VC", {"sanidad": 1, "educacion": 1, "transporte": 1, "vivienda": 0})
        result = filter_explorer(frame, max_rent=float(frame.alquiler_m2_mes.max()), mode="personalizado")
        self.assertEqual(len(result), 113)
        self.assertTrue(result.alquiler_m2_mes.notna().all())
        self.assertTrue(result.ivp_personalizado.notna().all())

    def test_accent_insensitive_case_insensitive_name_and_code_search(self):
        frame = self.frames["VU"]
        for query in ("rascafria", "RÁSCAFRÍA", "  RaScAfRiA  ", "28120"):
            with self.subTest(query=query):
                result = filter_explorer(frame, query=query)
                self.assertEqual(result.codigo_ine.tolist(), ["28120"])
        self.assertEqual(filter_explorer(frame, query="chinchon").municipio.tolist(), ["Chinchón"])
        self.assertEqual(len(filter_explorer(frame, query=".*")), 0)

    def test_no_results_keep_schema_and_metadata(self):
        empty = filter_explorer(self.frames["VU"], query="Municipio inexistente 123xyz", mode="personalizado")
        self.assertEqual(len(empty), 0)
        self.assertIn("posicion_filtrada", empty)
        self.assertEqual(empty.attrs["universo_municipios"], 155)
        ranking = ranking_view(empty, mode="personalizado")
        self.assertEqual(len(ranking), 0)
        self.assertTrue({"valor_activo", "posicion_global", "estado_activo", "fortaleza", "debilidad"}.issubset(ranking))

    def test_only_index_uses_the_active_mode(self):
        frame = build_explorer_frame(self.data, "VU", {"sanidad": 1, "educacion": 1, "transporte": 1, "vivienda": 0})
        self.assertEqual(len(filter_explorer(frame, only_with_index=True, mode="general")), 139)
        self.assertEqual(len(filter_explorer(frame, only_with_index=True, mode="personalizado")), 155)
        self.assertEqual(len(filter_explorer(frame, only_with_index=True, mode="personal")), 155)

    def test_ranking_keeps_general_and_personal_modes_distinct(self):
        frame = build_explorer_frame(self.data, "VU", {"sanidad": 1, "educacion": 0, "transporte": 0, "vivienda": 0})
        general = ranking_view(filter_explorer(frame), mode="general")
        personal = ranking_view(filter_explorer(frame), mode="personalizado")
        self.assertEqual(general.valor_activo.notna().sum(), 139)
        self.assertEqual(personal.valor_activo.notna().sum(), 155)
        self.assertTrue(personal.fortaleza.eq("Sanidad").all())
        self.assertTrue(personal.debilidad.eq("Sanidad").all())
        self.assertNotEqual(general.codigo_ine.iloc[0], personal.codigo_ine.iloc[0])
        self.assertTrue(general.valor_activo.dropna().is_monotonic_decreasing)
        self.assertTrue(personal.valor_activo.is_monotonic_decreasing)


def ranking_fixture():
    frame = pd.DataFrame({
        "codigo_ine": ["28003", "28002", "28001", "28004"],
        "municipio": ["Rascafría", "Rascafria", "Álamo", "Sin dato"],
        "poblacion": [10, 20, 30, 40], "alquiler_m2_mes": [5., 6., 7., np.nan],
        "distancia_sanidad_m": [0., 10., 20., np.nan],
        "distancia_educacion_m": [30., 40., 50., np.nan],
        "distancia_transporte_m": [60., 70., 80., np.nan],
        "ivp_general": [90., 90., 89.999999999, np.nan],
        "ivp_personalizado": [50., 100., 50., np.nan],
        "posicion_general": pd.array([1, 1, 3, None], dtype="Int64"),
        "posicion_personalizada": pd.array([2, 1, 2, None], dtype="Int64"),
        "estado_general": ["disponible"] * 3 + ["dimensiones_faltantes"],
        "estado_personalizado": ["disponible"] * 3 + ["dimensiones_faltantes"],
        "puntuacion_sanidad": [100., 0., 75., np.nan],
        "puntuacion_educacion": [100., 50., 75., np.nan],
        "puntuacion_transporte": [50., 100., 75., np.nan],
        "puntuacion_vivienda": [np.nan, 75., 75., np.nan],
    })
    frame.attrs = {"pesos_normalizados": {"sanidad": .5, "educacion": .5, "transporte": 0., "vivienda": 0.},
                   "universo_municipios": 4, "anclas_fijas": True}
    return frame


class ExplorerFilterRankingTests(unittest.TestCase):
    def test_rank_minimum_ties_and_unrounded_differences(self):
        general = ranking_view(filter_explorer(ranking_fixture()))
        self.assertEqual(general.codigo_ine.tolist(), ["28002", "28003", "28001", "28004"])
        self.assertEqual(general.posicion_filtrada.iloc[:3].tolist(), [1, 1, 3])
        self.assertTrue(pd.isna(general.posicion_filtrada.iloc[3]))
        personal = ranking_view(filter_explorer(ranking_fixture()), mode="personalizado")
        self.assertEqual(personal.posicion_filtrada.iloc[:3].tolist(), [1, 2, 2])

    def test_filtered_rank_and_global_rank_are_separate(self):
        result = ranking_view(filter_explorer(ranking_fixture(), population_range=(30, 30)))
        self.assertEqual(result.posicion_global.tolist(), [3])
        self.assertEqual(result.posicion_filtrada.tolist(), [1])
        self.assertEqual(result.valor_activo.tolist(), [89.999999999])

    def test_inclusive_ranges_and_maxima_are_applied_in_raw_units(self):
        frame = ranking_fixture()
        self.assertEqual(filter_explorer(frame, population_range=(10, 20)).codigo_ine.tolist(), ["28003", "28002"])
        self.assertEqual(filter_explorer(frame, max_rent=6).codigo_ine.tolist(), ["28003", "28002"])
        self.assertEqual(filter_explorer(frame, max_health_distance_m=0).codigo_ine.tolist(), ["28003"])
        self.assertEqual(filter_explorer(frame, max_education_distance_m=40).codigo_ine.tolist(), ["28003", "28002"])
        self.assertEqual(filter_explorer(frame, max_transport_distance_m=70).codigo_ine.tolist(), ["28003", "28002"])

    def test_each_active_distance_filter_excludes_its_missing_observation(self):
        frame = ranking_fixture()
        for key in ("max_health_distance_m", "max_education_distance_m", "max_transport_distance_m"):
            with self.subTest(key=key):
                self.assertEqual(len(filter_explorer(frame, **{key: 100000})), 3)
        self.assertEqual(len(filter_explorer(frame, population_range=(0, 50000))), 4)

    def test_strengths_and_weaknesses_preserve_ties_and_missingness(self):
        result = ranking_view(filter_explorer(ranking_fixture())).set_index("codigo_ine")
        self.assertEqual(result.loc["28003", "fortaleza"], "Sanidad, Educación")
        self.assertEqual(result.loc["28003", "debilidad"], "Transporte")
        self.assertEqual(result.loc["28001", "fortaleza"], "Sanidad, Educación, Transporte, Vivienda")
        self.assertEqual(result.loc["28004", "fortaleza"], "Sin dato")
        self.assertEqual(result.loc["28004", "debilidad"], "Sin dato")

    def test_personalized_strengths_ignore_dimensions_with_zero_weight(self):
        result = ranking_view(filter_explorer(ranking_fixture()), mode="personalizado").set_index("codigo_ine")
        self.assertEqual(result.loc["28002", "fortaleza"], "Educación")
        self.assertEqual(result.loc["28002", "debilidad"], "Sanidad")
        self.assertNotIn("Transporte", result.loc["28001", "fortaleza"])
        self.assertNotIn("Vivienda", result.loc["28001", "debilidad"])

    def test_filters_and_ranking_leave_input_frame_and_attrs_intact(self):
        frame = ranking_fixture()
        before = frame.copy(deep=True)
        before_attrs = deepcopy(frame.attrs)
        ranking_view(filter_explorer(frame, max_rent=6), mode="personalizado")
        pd.testing.assert_frame_equal(frame, before)
        self.assertEqual(frame.attrs, before_attrs)

    def test_filter_criteria_are_complete_strict_json_and_reproducible(self):
        frame = ranking_fixture()
        result = filter_explorer(frame, population_range=(np.int64(10), np.int64(30)),
                                 max_rent=np.float64(7), max_health_distance_m=np.int64(20),
                                 max_education_distance_m=50, max_transport_distance_m=80,
                                 query="  álamo  ", only_with_index=np.bool_(True), mode="personal")
        criteria = json.loads(json.dumps(result.attrs["filtros"], allow_nan=False, ensure_ascii=False))
        self.assertEqual(criteria, {
            "population_range": [10., 30.], "max_rent": 7., "max_health_distance_m": 20.,
            "max_education_distance_m": 50., "max_transport_distance_m": 80.,
            "query": "álamo", "only_with_index": True, "mode": "personalizado",
        })
        pd.testing.assert_frame_equal(filter_explorer(frame, **criteria), result)
        ranking = ranking_view(result, mode="personalizado")
        self.assertEqual(ranking.attrs["filtros"], criteria)
        self.assertNotIn("filtros", frame.attrs)

    def test_invalid_filter_numbers_are_rejected(self):
        for key in ("max_rent", "max_health_distance_m", "max_education_distance_m", "max_transport_distance_m"):
            for value in (-1, np.inf, np.nan, True, "5", 1j, 10 ** 10000):
                with self.subTest(key=key, value_type=type(value).__name__), self.assertRaises(IntegrationError):
                    filter_explorer(ranking_fixture(), **{key: value})

    def test_invalid_population_ranges_are_rejected(self):
        for value in ([20, 10], [-1, 10], [True, 10], [0, np.inf], [0, 10, 20], "0,10", 10):
            with self.subTest(value=value), self.assertRaises(IntegrationError):
                filter_explorer(ranking_fixture(), population_range=value)

    def test_invalid_modes_search_and_boolean_are_rejected(self):
        with self.assertRaises(IntegrationError):
            filter_explorer(ranking_fixture(), mode="unknown")
        with self.assertRaises(IntegrationError):
            ranking_view(ranking_fixture(), mode="unknown")
        with self.assertRaises(IntegrationError):
            filter_explorer(ranking_fixture(), query=123)
        with self.assertRaises(IntegrationError):
            filter_explorer(ranking_fixture(), only_with_index="yes")


if __name__ == "__main__":
    unittest.main()
