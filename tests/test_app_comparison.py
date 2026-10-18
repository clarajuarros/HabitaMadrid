"""Comparaciones con fuentes reales y gráficos que distinguen cero de ausencia."""
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from streamlit.runtime.memory_media_file_storage import MemoryMediaFileStorage
from streamlit.testing.v1 import AppTest

from src.app_comparison import build_comparison, comparison_chart, contribution_chart
from src.app_data import DIMENSIONS, load_app_data
from src.app_services import load_service_tables


ROOT = Path(__file__).resolve().parents[1]
CODES = ["28001", "28120", "28052"]  # La Acebeda, Rascafría y Chinchón.


class ComparisonDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_app_data(ROOT)
        cls.services = load_service_tables(ROOT)

    def build(self, codes=CODES, profile="VU", explorer_frame=None):
        return build_comparison(self.data, self.services, codes, profile, explorer_frame)

    def test_two_and_three_rows_keep_user_order_and_master_population(self):
        for codes in (CODES[:2], CODES[::-1]):
            with self.subTest(codes=codes):
                frame = self.build(codes)
                self.assertEqual(frame["codigo_ine"].tolist(), codes)
                master = self.data["municipios"].set_index("codigo_ine")
                self.assertEqual(frame["municipio"].tolist(), master.loc[codes, "municipio"].tolist())
                self.assertEqual(frame["poblacion"].tolist(), master.loc[codes, "poblacion"].tolist())

    def test_invalid_sizes_duplicates_and_numeric_codes_are_rejected(self):
        for codes in ([], CODES[:1], CODES + ["28003"], [CODES[0]] * 2, [28001, 28120], "28001"):
            with self.subTest(codes=codes), self.assertRaisesRegex(ValueError, "2–3"):
                self.build(codes)

    def test_unknown_and_excluded_municipalities_are_rejected(self):
        for code in ("28079", "28999", "2801"):
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, "fuera del ámbito"):
                self.build([CODES[0], code])

    def test_profile_must_be_explicit_vc_or_vu(self):
        for profile in ("ambos", "total", "vc", None):
            with self.subTest(profile=profile), self.assertRaisesRegex(ValueError, "VC o VU"):
                self.build(profile=profile)

    def test_general_index_and_dimension_scores_match_published_rows(self):
        for profile in ("VC", "VU"):
            frame = self.build(profile=profile).set_index("codigo_ine")
            source = self.data["ivp_general"].loc[self.data["ivp_general"]["perfil_vivienda"].eq(profile)].set_index("codigo_ine")
            for target, original in [("ivp_general", "valor_indice")] + [(f"puntuacion_{d}", f"puntuacion_{d}") for d in DIMENSIONS]:
                np.testing.assert_allclose(frame[target], source.loc[CODES, original], equal_nan=True)

    def test_2024_housing_typologies_never_mix_or_fall_back(self):
        frames = {p: self.build(profile=p).set_index("codigo_ine") for p in ("VC", "VU")}
        for profile, frame in frames.items():
            original = self.data["vivienda_historico"]
            original = original.loc[original["tipo"].eq(profile) & original["anio"].eq(2024)].set_index("codigo_ine")
            for target, source in {"alquiler_m2_mes": "alquiler_m2_mes_mediana", "alquiler_mes": "alquiler_mes_mediana", "testigos_vivienda": "testigos"}.items():
                np.testing.assert_allclose(frame[target], original.loc[CODES, source], equal_nan=True)
            self.assertEqual(frame.attrs["perfil_vivienda"], profile)
        self.assertNotEqual(frames["VC"].loc["28120", "alquiler_m2_mes"], frames["VU"].loc["28120", "alquiler_m2_mes"])
        self.assertTrue(pd.isna(frames["VC"].loc["28001", "alquiler_m2_mes"]))
        self.assertEqual(frames["VC"].loc["28001", "testigos_vivienda"], 0)

    def test_housing_never_falls_back_to_an_earlier_year_when_2024_missing(self):
        data = dict(self.data)
        source = data["vivienda_historico"]
        data["vivienda_historico"] = source.loc[~(source["codigo_ine"].eq("28120") & source["tipo"].eq("VU") & source["anio"].eq(2024))].copy()
        frame = build_comparison(data, self.services, CODES[:2], "VU").set_index("codigo_ine")
        self.assertTrue(pd.isna(frame.loc["28120", "alquiler_m2_mes"]))
        self.assertTrue(pd.isna(frame.loc["28120", "testigos_vivienda"]))

    def test_local_counts_use_exact_municipality_including_education_without_coordinates(self):
        frame = self.build().set_index("codigo_ine")
        for kind, field in {"sanidad": "centros_salud_locales", "educacion": "centros_educativos_locales_alta", "transporte": "paradas_bus_locales"}.items():
            for code in CODES:
                self.assertEqual(frame.loc[code, field], self.services[kind]["codigo_ine"].eq(code).sum())
        # A local absence is a real zero, despite educational destinations nearby.
        self.assertEqual(frame.loc["28001", "centros_educativos_locales_alta"], 0)
        self.assertGreater(frame.loc["28001", "distancia_educacion_m"], 0)
        # A record without operative coordinates remains a valid local count.
        tables = dict(self.services)
        extra = tables["educacion"].iloc[[0]].copy()
        extra["codigo_ine"] = "28001"
        extra[["x_25830", "y_25830", "longitud", "latitud"]] = np.nan
        tables["educacion"] = pd.concat([tables["educacion"], extra], ignore_index=True)
        changed = build_comparison(self.data, tables, CODES[:2], "VU").set_index("codigo_ine")
        self.assertEqual(changed.loc["28001", "centros_educativos_locales_alta"], 1)

    def test_distances_are_ivp_selected_observations_not_generic_service_proximity(self):
        tables = dict(self.services)
        tables["educacion"] = tables["educacion"].assign(x_25830=0, y_25830=0)
        frame = build_comparison(self.data, tables, CODES, "VU").set_index("codigo_ine")
        for dimension in DIMENSIONS[:3]:
            indicator = self.data["configuracion"]["indicadores_comunes"][dimension]
            original = self.data["observaciones"]
            original = original.loc[original["indicador_id"].eq(indicator)].set_index("codigo_ine")["valor"]
            np.testing.assert_allclose(frame[f"distancia_{dimension}_m"], original.loc[CODES], equal_nan=True)

    def test_context_preserves_2020_2025_growth_and_2023_income(self):
        frame = self.build().set_index("codigo_ine")
        source = self.data["contexto_demografico"].set_index("codigo_ine")
        for field in ("crecimiento_2020_2025_pct", "renta_disponible_per_capita_2023"):
            np.testing.assert_allclose(frame[field], source.loc[CODES, field], equal_nan=True)

    def test_personalized_missing_and_zero_stay_distinct_without_general_substitution(self):
        explorer = pd.DataFrame({"codigo_ine": CODES[:2], "ivp_personalizado": [0., np.nan], "perfil_vivienda": "VU"})
        frame = self.build(explorer_frame=explorer).set_index("codigo_ine")
        self.assertEqual(frame.loc["28001", "ivp_personalizado"], 0)
        self.assertTrue(pd.isna(frame.loc["28120", "ivp_personalizado"]))
        self.assertTrue(pd.isna(frame.loc["28052", "ivp_personalizado"]))
        self.assertTrue(self.build()["ivp_personalizado"].isna().all())

    def test_personalized_wrong_profile_duplicate_codes_or_missing_column_rejected(self):
        for explorer in (
            pd.DataFrame({"codigo_ine": CODES[:2], "ivp_personalizado": [2, 3], "perfil_vivienda": ["VC", "VU"]}),
            pd.DataFrame({"codigo_ine": [CODES[0]] * 2, "ivp_personalizado": [2, 3]}),
            pd.DataFrame({"codigo_ine": CODES[:2]}),
        ):
            with self.subTest(columns=list(explorer)), self.assertRaises(ValueError):
                self.build(explorer_frame=explorer)

    def test_duplicate_housing_and_selected_distance_observations_are_rejected(self):
        for name in ("vivienda_historico", "observaciones"):
            data = dict(self.data)
            source = data[name]
            mask = source["codigo_ine"].eq("28120")
            if name == "vivienda_historico":
                mask &= source["anio"].eq(2024) & source["tipo"].eq("VU")
            extra = source.loc[mask].iloc[[0]]
            data[name] = pd.concat([source, extra], ignore_index=True)
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "duplicados"):
                build_comparison(data, self.services, CODES[:2], "VU")

    def test_metadata_explains_units_dates_typology_and_limitations(self):
        frame = self.build(profile="VC")
        self.assertEqual(set(frame.attrs["columnas"]), set(frame.columns))
        metadata = frame.attrs["columnas"]
        self.assertEqual(metadata["alquiler_m2_mes"]["unidad"], "€/m²/mes")
        self.assertEqual(metadata["alquiler_mes"]["periodo"], "2024")
        self.assertEqual(metadata["testigos_vivienda"]["perfil_vivienda"], "VC")
        self.assertIn("2026-10-15", metadata["distancia_sanidad_m"]["periodo"])
        self.assertEqual(metadata["renta_disponible_per_capita_2023"]["periodo"], "2023")
        notes = " ".join(frame.attrs["limitaciones"])
        for text in ("diez tipologías", "fuente parcial", "otros municipios", "oferta actual"):
            self.assertIn(text, notes)

    def test_adapter_does_not_mutate_the_cached_tables_or_explorer(self):
        originals = {name: self.data[name].copy(deep=True) for name in ("ivp_general", "observaciones", "vivienda_historico")}
        service_originals = {name: self.services[name].copy(deep=True) for name in DIMENSIONS[:3]}
        explorer = pd.DataFrame({"codigo_ine": CODES, "ivp_personalizado": [0, 1, np.nan]})
        explorer_before = explorer.copy(deep=True)
        self.build(explorer_frame=explorer)
        for name, original in originals.items():
            pd.testing.assert_frame_equal(original, self.data[name])
        for name, original in service_originals.items():
            pd.testing.assert_frame_equal(original, self.services[name])
        pd.testing.assert_frame_equal(explorer, explorer_before)


def chart_fixture():
    frame = pd.DataFrame({"codigo_ine": ["28001", "28002", "28003"], "municipio": ["Uno", "Dos", "Tres"],
                          "puntuacion_sanidad": [0, 80, 100], "puntuacion_educacion": [40, 60, 100],
                          "puntuacion_transporte": [60, 40, 100], "puntuacion_vivienda": [np.nan, 20, 100],
                          "ivp_personalizado": [np.nan, 50, 100]})
    return frame


class ComparisonChartTests(unittest.TestCase):
    def test_grouped_categories_scores_and_all_selected_municipalities(self):
        frame = chart_fixture()
        before = frame.copy(deep=True)
        figure = comparison_chart(frame)
        self.assertEqual(figure.layout.barmode, "group")
        self.assertEqual([trace.name for trace in figure.data], ["Uno", "Dos", "Tres"])
        self.assertEqual(list(figure.data[0].x), ["Sanidad", "Educación", "Transporte", "Vivienda"])
        self.assertEqual(list(figure.data[0].y), [0, 40, 60, None])
        self.assertEqual(list(figure.data[2].y), [100] * 4)
        self.assertEqual(list(figure.layout.yaxis.tickvals), [0, 25, 50, 75, 100])
        pd.testing.assert_frame_equal(frame, before)

    def test_missing_is_explained_and_real_zero_remains_a_score(self):
        figure = comparison_chart(chart_fixture())
        self.assertEqual(figure.data[0].y[0], 0)
        self.assertIsNone(figure.data[0].y[3])
        self.assertEqual(figure.data[0].text[0], "0.0")
        self.assertIn("Uno: Vivienda", figure.layout.annotations[0].text)

    def test_nonfinite_scores_are_never_displayed_as_actual_values(self):
        frame = chart_fixture()
        frame["puntuacion_sanidad"] = np.inf
        figure = comparison_chart(frame)
        self.assertTrue(all(trace.y[0] is None for trace in figure.data))
        self.assertIn("Sanidad", figure.layout.annotations[0].text)

    def test_two_rows_work_without_requiring_a_third(self):
        self.assertEqual(len(comparison_chart(chart_fixture().iloc[:2]).data), 2)

    def test_missing_columns_invalid_size_and_duplicate_codes_rejected(self):
        for frame in (chart_fixture().drop(columns="puntuacion_vivienda"), chart_fixture().iloc[:1], pd.concat([chart_fixture().iloc[:2]] * 2)):
            with self.subTest(size=len(frame)), self.assertRaises(ValueError):
                comparison_chart(frame)

    def test_labels_are_escaped_before_entering_plotly_html(self):
        frame = chart_fixture()
        frame.loc[0, "municipio"] = "<b>Villa</b>"
        figure = comparison_chart(frame)
        self.assertEqual(figure.data[0].name, "&lt;b&gt;Villa&lt;/b&gt;")


class ContributionChartTests(unittest.TestCase):
    def test_stacks_are_exact_global_weighted_scores_only_for_available_indices(self):
        frame = chart_fixture()
        weights = {"sanidad": 40, "educacion": 30, "transporte": 20, "vivienda": 10}
        figure = contribution_chart(frame, weights)
        self.assertEqual(figure.layout.barmode, "stack")
        self.assertEqual([trace.name for trace in figure.data], ["Sanidad", "Educación", "Transporte", "Vivienda"])
        self.assertTrue(all(list(trace.x) == ["Dos", "Tres"] for trace in figure.data))
        np.testing.assert_allclose([trace.y[0] for trace in figure.data], [32, 18, 8, 2])
        np.testing.assert_allclose([sum(trace.y[i] for trace in figure.data) for i in range(2)], [60, 100])
        self.assertIn("Sin índice por datos ausentes: Uno", figure.layout.annotations[0].text)

    def test_zero_weight_excludes_missing_axis_and_labels_partial_scope(self):
        weights = {"sanidad": 2, "educacion": 1, "transporte": 1, "vivienda": 0}
        figure = contribution_chart(chart_fixture(), weights)
        self.assertEqual(len(figure.data), 3)
        self.assertEqual(list(figure.data[0].x), ["Uno", "Dos", "Tres"])
        np.testing.assert_allclose([trace.y[0] for trace in figure.data], [0, 10, 15])
        self.assertIn("alcance parcial", figure.layout.title.text)
        self.assertIn("peso 0: Vivienda", figure.layout.annotations[0].text)

    def test_no_complete_municipality_gets_an_explanation_without_any_partial_stack(self):
        frame = chart_fixture().assign(puntuacion_vivienda=np.nan)
        figure = contribution_chart(frame, {d: 1 for d in DIMENSIONS})
        self.assertEqual(len(figure.data), 0)
        self.assertIn("Sin índice calculable", figure.layout.annotations[0].text)

    def test_invalid_weights_are_rejected_by_the_existing_engine(self):
        for weights in ({d: 0 for d in DIMENSIONS}, {d: -1 for d in DIMENSIONS}, {"vivienda": 1}):
            with self.subTest(weights=weights), self.assertRaises(ValueError):
                contribution_chart(chart_fixture(), weights)

    def test_contribution_chart_preserves_caller_weights_and_table(self):
        frame = chart_fixture()
        weights = {d: 1 for d in DIMENSIONS}
        before_frame, before_weights = frame.copy(deep=True), deepcopy(weights)
        contribution_chart(frame, weights)
        pd.testing.assert_frame_equal(frame, before_frame)
        self.assertEqual(weights, before_weights)


COMPARISON_APP = '''
from pathlib import Path
import streamlit as st
from src.app_data import load_app_data
from src.app_services import load_service_tables
from src.app_explorer import build_explorer_frame
from src.app_compare_ui import render_comparison

@st.cache_data(show_spinner=False)
def inputs():
    return load_app_data(Path.cwd()), load_service_tables(Path.cwd())

data, services = inputs()
profile = st.selectbox("Perfil de prueba", ["VU", "VC"], key="test_profile")
mode = st.radio("Modo de prueba", ["general", "personalizado"], key="test_mode")
code = st.selectbox("Ficha de prueba", ["28001", "28120", "28052"], key="test_sheet")
weights = {d: st.slider(d, 0, 100, 25, key="test_weight_" + d) for d in ("sanidad", "educacion", "transporte", "vivienda")}
frame = build_explorer_frame(data, profile, weights)
render_comparison(data, services, frame, profile, code, mode)
'''


class ComparisonStreamlitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.initial = AppTest.from_string(COMPARISON_APP, default_timeout=60).run()
        if cls.initial.exception:
            raise AssertionError([item.value for item in cls.initial.exception])

    def new_app(self):
        app = AppTest.from_string(COMPARISON_APP, default_timeout=60).run()
        self.assertEqual([item.value for item in app.exception], [])
        app.multiselect(key="comparar_codigos").set_value(["28120", "28052"]).run()
        return app

    def assert_clean(self, app):
        self.assertEqual([item.value for item in app.exception], [])
        self.assertEqual(len(app.error), 0)

    def test_defaults_and_complete_eligible_options(self):
        app = self.initial
        self.assert_clean(app)
        self.assertEqual(app.multiselect(key="comparar_codigos").value, [])
        self.assertEqual(len(app.multiselect(key="comparar_codigos").options), 155)
        self.assertNotIn("Madrid", app.multiselect(key="comparar_codigos").options)
        self.assertEqual(len(app.get("plotly_chart")), 0)
        self.assertEqual(len(app.dataframe), 0)  # No implicit municipal comparison.

    def test_zero_or_one_municipality_shows_info_without_chart_or_download(self):
        app = self.new_app()
        for codes in ([], ["28001"]):
            app.multiselect(key="comparar_codigos").set_value(codes).run()
            self.assert_clean(app)
            self.assertEqual(len(app.info), 1)
            self.assertEqual(len(app.get("plotly_chart")), 0)
            self.assertEqual(len(app.get("download_button")), 0)

    def test_add_current_sheet_callback_runs_before_multiselect_and_disables_at_three(self):
        app = self.new_app()
        app.button(key="comparar_anadir_ficha").click().run()
        self.assert_clean(app)
        self.assertEqual(app.multiselect(key="comparar_codigos").value, ["28120", "28052", "28001"])
        self.assertTrue(app.button(key="comparar_anadir_ficha").disabled)
        self.assertEqual(app.selectbox(key="test_sheet").value, "28001")

    def test_add_button_disabled_when_current_sheet_already_compared(self):
        app = self.new_app()
        app.selectbox(key="test_sheet").set_value("28120").run()
        self.assert_clean(app)
        self.assertTrue(app.button(key="comparar_anadir_ficha").disabled)
        self.assertEqual(app.multiselect(key="comparar_codigos").value, ["28120", "28052"])

    def test_housing_profile_change_preserves_comparison_codes(self):
        app = self.new_app()
        app.multiselect(key="comparar_codigos").set_value(["28001", "28120", "28052"]).run()
        app.selectbox(key="test_profile").set_value("VC").run()
        self.assert_clean(app)
        self.assertEqual(app.multiselect(key="comparar_codigos").value, ["28001", "28120", "28052"])
        rent_table = app.dataframe[0].value
        acebeda_rent = rent_table.loc[rent_table["Dato y referencia"].str.contains("Alquiler declarado"), "La Acebeda"].iloc[0]
        self.assertEqual(acebeda_rent, "No hay datos disponibles")
        self.assertTrue(any("Vivienda colectiva" in item.value for item in app.caption))

    def test_personalized_partial_scope_explained_and_generates_second_chart(self):
        app = self.new_app()
        app.radio(key="test_mode").set_value("personalizado").run()
        app.slider(key="test_weight_vivienda").set_value(0).run()
        self.assert_clean(app)
        self.assertEqual(len(app.get("plotly_chart")), 2)
        self.assertIn("Alcance parcial", app.warning[0].value)
        self.assertIn("Vivienda", app.warning[0].value)

    def test_real_download_has_exact_rows_profile_weights_scope_units_and_periods(self):
        app = self.new_app()
        downloads = []
        store = MemoryMediaFileStorage.load_and_get_id

        def capture(storage, path_or_data, mimetype, kind, filename=None):
            if mimetype == "text/csv" and isinstance(path_or_data, bytes):
                downloads.append((filename, path_or_data))
            return store(storage, path_or_data, mimetype, kind, filename)

        with patch.object(MemoryMediaFileStorage, "load_and_get_id", new=capture):
            app.radio(key="test_mode").set_value("personalizado").run()
            app.slider(key="test_weight_vivienda").set_value(0).run()
            app.selectbox(key="test_profile").set_value("VC").run()
        self.assert_clean(app)
        filename, content = downloads[-1]
        self.assertEqual(filename, "HabitaMadrid_comparacion_VC.csv")
        table = pd.read_csv(BytesIO(content), dtype={"codigo_ine": str})
        self.assertEqual(table["codigo_ine"].tolist(), ["28120", "28052"])
        self.assertEqual(set(table["perfil_vivienda"]), {"VC"})
        self.assertEqual(set(table["modo_indice"]), {"personalizado"})
        self.assertEqual(set(table["alcance"]), {"parcial_dimensiones_seleccionadas"})
        weights = json.loads(table.iloc[0]["pesos_json"])
        self.assertEqual(weights["vivienda"], 0)
        self.assertAlmostEqual(sum(weights.values()), 1)
        self.assertEqual(json.loads(table.iloc[0]["unidades_json"])["alquiler_mes"], "€/mes")
        self.assertEqual(json.loads(table.iloc[0]["periodos_json"])["alquiler_mes"], "2024")

    def test_general_download_keeps_active_equal_weights_separate_from_custom_weights(self):
        from src.app_compare_ui import _download
        frame = chart_fixture().iloc[:2].copy()
        frame.attrs["columnas"] = {"puntuacion_sanidad": {"unidad": "puntos / 100", "periodo": "Fuentes con fechas distintas"}}
        general = {d: .25 for d in DIMENSIONS}
        custom = {"sanidad": .5, "educacion": .5, "transporte": 0, "vivienda": 0}
        content = _download(frame, "VU", "general", general, custom, "cuatro_dimensiones", "parcial_dimensiones_seleccionadas", "ivp-1.0.0")
        table = pd.read_csv(BytesIO(content))
        self.assertEqual(json.loads(table.iloc[0]["pesos_json"]), general)
        self.assertEqual(json.loads(table.iloc[0]["pesos_personalizados_json"]), custom)


if __name__ == "__main__":
    unittest.main()
