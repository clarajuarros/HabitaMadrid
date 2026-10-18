"""Map events update the municipal selector once, before widget rendering."""

from copy import deepcopy
import csv
import json
from pathlib import Path
import unittest

from streamlit.elements.deck_gl_json_chart import PydeckSelectionSerde
from streamlit.testing.v1 import AppTest

from src.app_selection import apply_map_selection


ALLOWED = ["28120", "28052", "28001"]
ROOT = Path(__file__).resolve().parents[1]


def selection(code="28052", *, layer="municipios"):
    return {
        "selection": {
            "indices": {layer: [0]},
            "objects": {
                layer: [{"type": "Feature", "properties": {"codigo_ine": code}}]
            },
        }
    }


def existing_state(event=None):
    return {
        "municipio_codigo": "28120",
        "perfil_vivienda": "VC",
        "busqueda_nombre": "rascafria",
        "_last_query": ("28120", "VC"),
        "mapa_municipios": selection() if event is None else event,
    }


class MapSelectionStateTests(unittest.TestCase):
    def test_new_map_selection_updates_search_sheet_and_query(self):
        state = existing_state()
        original_event = deepcopy(state["mapa_municipios"])
        query = {"municipio": "28120", "vivienda": "VC", "origen": "presentacion"}
        self.assertTrue(apply_map_selection(state, query, ALLOWED))
        self.assertEqual(state["municipio_codigo"], "28052")
        self.assertEqual(state["busqueda_nombre"], "")
        self.assertEqual(state["_mapa_pick_revision"], 1)
        self.assertEqual(state["perfil_vivienda"], "VC")
        self.assertEqual(state["_last_query"], ("28052", "VC"))
        self.assertEqual(query, {"municipio": "28052", "vivienda": "VC", "origen": "presentacion"})
        self.assertEqual(state["mapa_municipios"], original_event)

    def test_real_read_only_streamlit_event_is_supported(self):
        event = PydeckSelectionSerde().deserialize(json.dumps(selection()))
        state = existing_state(event)
        self.assertTrue(apply_map_selection(state, {}, ALLOWED))
        self.assertEqual(state["municipio_codigo"], "28052")
        self.assertIs(state["mapa_municipios"], event)

    def test_callback_reads_only_its_explicit_chart_key(self):
        state = existing_state()
        state["mapa_municipios_19"] = selection("28001")
        self.assertTrue(apply_map_selection(state, {}, ALLOWED, event_key="mapa_municipios_19"))
        self.assertEqual(state["municipio_codigo"], "28001")
        self.assertEqual(state["mapa_municipios"]["selection"]["objects"]["municipios"][0]["properties"]["codigo_ine"], "28052")

    def test_current_municipality_reopens_its_sheet(self):
        state = existing_state(selection("28120"))
        query = {"municipio": "28120", "vivienda": "VC", "extra": "conservar"}
        before_query = deepcopy(query)
        self.assertTrue(apply_map_selection(state, query, ALLOWED))
        self.assertEqual(state["_active_view"], "fichas")
        self.assertEqual(state["_navigation_pending"]["municipality"], "28120")
        self.assertEqual(query, before_query)

    def test_excluded_municipality_does_not_change_the_current_sheet(self):
        state = existing_state(selection("28079"))  # Madrid: over 50,000.
        query = {"municipio": "28120", "vivienda": "VC"}
        before = deepcopy(state), deepcopy(query)
        self.assertFalse(apply_map_selection(state, query, ALLOWED))
        self.assertEqual((state, query), before)

    def test_missing_malformed_or_other_layer_event_has_no_side_effects(self):
        events = [None, {}, {"selection": {}}, {"selection": {"objects": {}}},
                  {"selection": {"objects": {"municipios": []}}},
                  selection(layer="servicios"), selection("desconocido")]
        for event in events:
            with self.subTest(event=event):
                state = existing_state()
                state["mapa_municipios"] = event
                query = {"municipio": "28120", "vivienda": "VC"}
                before = deepcopy(state), deepcopy(query)
                self.assertFalse(apply_map_selection(state, query, ALLOWED))
                self.assertEqual((state, query), before)

    def test_unifamiliar_profile_is_preserved(self):
        state = existing_state()
        state["perfil_vivienda"] = "VU"
        query = {}
        self.assertTrue(apply_map_selection(state, query, ALLOWED))
        self.assertEqual(state["perfil_vivienda"], "VU")
        self.assertEqual(query["vivienda"], "VU")

    def test_invalid_housing_profile_cannot_be_written_to_the_url(self):
        state = existing_state()
        state["perfil_vivienda"] = "desconocido"
        query = {}
        before = deepcopy(state), deepcopy(query)
        self.assertFalse(apply_map_selection(state, query, ALLOWED))
        self.assertEqual((state, query), before)


# PyDeck is not exposed as an interactive Element by AppTest. Inject its native
# protobuf event to exercise Streamlit's real serde, callback and rerun order.
CALLBACK_APP = '''
import pydeck as pdk
import streamlit as st
from src.app_selection import apply_map_selection

allowed = ["28120", "28052", "28001"]
if "municipio_codigo" not in st.session_state:
    st.session_state["municipio_codigo"] = "28120"
    st.session_state["perfil_vivienda"] = "VC"
    st.session_state["busqueda_nombre"] = "rascafria"

def select_map():
    apply_map_selection(st.session_state, st.query_params, allowed)

st.text_input("Nombre", key="busqueda_nombre")
st.selectbox("Municipio", allowed, key="municipio_codigo")
st.selectbox("Vivienda", ["VC", "VU"], key="perfil_vivienda")
st.query_params["municipio"] = st.session_state["municipio_codigo"]
st.query_params["vivienda"] = st.session_state["perfil_vivienda"]
st.session_state["_last_query"] = (st.session_state["municipio_codigo"], st.session_state["perfil_vivienda"])
chart = pdk.Deck(layers=[pdk.Layer("GeoJsonLayer", id="municipios", data={"type": "FeatureCollection", "features": []}, pickable=True)], map_provider=None, map_style=None)
st.pydeck_chart(chart, key="mapa_municipios", selection_mode="single-object", on_select=select_map)
'''


class NativeStreamlitMapCallbackTests(unittest.TestCase):
    def new_app(self):
        app = AppTest.from_string(CALLBACK_APP, default_timeout=15).run()
        self.assertEqual(list(app.exception), [])
        return app

    def inject_map_event(self, app, event):
        widgets = app._tree.get_widget_states()
        chart = app.get("deck_gl_json_chart")[0]
        widget = widgets.widgets.add()
        widget.id = chart.proto.id
        widget.string_value = json.dumps(event)
        app._run(widget_state=widgets)
        self.assertEqual([item.value for item in app.exception], [])
        return app

    def test_native_callback_updates_selector_before_it_is_instantiated(self):
        app = self.new_app()
        self.inject_map_event(app, selection())
        self.assertEqual(app.selectbox(key="municipio_codigo").value, "28052")
        self.assertEqual(app.text_input(key="busqueda_nombre").value, "")
        self.assertEqual(app.selectbox(key="perfil_vivienda").value, "VC")
        self.assertEqual(app.query_params, {"municipio": "28052", "vivienda": "VC"})

    def test_retained_map_event_does_not_override_a_later_search_choice(self):
        app = self.new_app()
        self.inject_map_event(app, selection())
        app.selectbox(key="municipio_codigo").set_value("28001")
        # AppTest does not serialize this unsupported chart element itself.
        # A browser sends its unchanged selection along with the new selector.
        self.inject_map_event(app, selection())
        self.assertEqual([item.value for item in app.exception], [])
        self.assertEqual(app.selectbox(key="municipio_codigo").value, "28001")
        self.assertEqual(app.query_params["municipio"], "28001")
        self.assertEqual(app.session_state["mapa_municipios"]["selection"]["objects"]["municipios"][0]["properties"]["codigo_ine"], "28052")
        self.inject_map_event(app, selection())
        self.assertEqual(app.selectbox(key="municipio_codigo").value, "28001")
        self.assertEqual(app.query_params["municipio"], "28001")

    def test_native_excluded_map_selection_does_not_change_sheet(self):
        app = self.new_app()
        self.inject_map_event(app, selection("28079"))
        self.assertEqual(app.selectbox(key="municipio_codigo").value, "28120")
        self.assertEqual(app.text_input(key="busqueda_nombre").value, "rascafria")


class ActualMunicipalMapTests(unittest.TestCase):
    """Exercise the complete app, real municipal snapshot and map callback."""

    def new_app(self):
        app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
        app.query_params["municipio"] = "28120"
        app.query_params["vivienda"] = "VC"
        app.run()
        self.assert_no_errors(app)
        self.assert_no_errors(app)
        self.assertEqual(app.selectbox(key="municipio_codigo").value, "28120")
        return app

    def assert_no_errors(self, app):
        self.assertEqual([item.value for item in app.exception], [])
        self.assertEqual(len(app.error), 0)

    def inject_map_event(self, app, event):
        event = deepcopy(event)
        layer = json.loads(app.get("deck_gl_json_chart")[0].proto.json)["layers"][0]["id"]
        for field in ("indices", "objects"):
            if "municipios" in event["selection"][field]:
                event["selection"][field][layer] = event["selection"][field].pop("municipios")
        before = app.get("deck_gl_json_chart")[0].proto
        camera = json.loads(before.json)["initialViewState"]
        identity = before.id
        widgets = app._tree.get_widget_states()
        widget = widgets.widgets.add()
        widget.id = app.get("deck_gl_json_chart")[0].proto.id
        widget.string_value = json.dumps(event)
        app._run(widget_state=widgets)
        self.assert_no_errors(app)
        after = app.get("deck_gl_json_chart")[0].proto
        self.assertEqual(after.id, identity)
        self.assertEqual(json.loads(after.json)["initialViewState"], camera)

    def html(self, app):
        return "\n".join(element.proto.body for element in app.get("html"))

    def assert_palette_preserved(self, app):
        deck = json.loads(app.get("deck_gl_json_chart")[0].proto.json)
        self.assertEqual(deck["layers"][0]["_subLayerProps"]["polygons-fill"]["getFillColor"], "@@=properties.color_rgba")
        self.assertEqual(deck["initialViewState"]["transitionDuration"], 0)

    def test_map_clears_previous_name_filter_and_synchronizes_actual_sheet(self):
        app = self.new_app()
        app.query_params["origen"] = "presentacion"
        previous_key = app.get("deck_gl_json_chart")[0].key
        self.inject_map_event(app, selection("28052"))
        self.assertEqual(app.selectbox(key="municipio_codigo").value, "28052")
        self.assertEqual(len(app.selectbox(key="municipio_codigo").options), 155)
        self.assertEqual(app.selectbox(key="perfil_vivienda").value, "VC")
        self.assertEqual(app.query_params, {"municipio": "28052", "vivienda": "VC", "origen": "presentacion"})
        self.assertIn("<h2>Chinchón</h2>", self.html(app))
        self.assertEqual(app.get("deck_gl_json_chart")[0].key, previous_key)
        self.assert_palette_preserved(app)

    def test_later_name_search_and_one_new_map_click_remain_synchronized(self):
        app = self.new_app()
        self.inject_map_event(app, selection("28052"))
        self.assert_palette_preserved(app)
        app.selectbox(key="municipio_codigo").set_value("28001").run()
        self.assertEqual(app.radio(key="encuadre_mapa").value, "region")
        self.assert_no_errors(app)
        self.assertEqual(app.selectbox(key="municipio_codigo").value, "28001")
        self.assertEqual(app.selectbox(key="perfil_vivienda").value, "VC")
        self.assertEqual(app.query_params, {"municipio": "28001", "vivienda": "VC"})
        self.assertIn("<h2>La Acebeda</h2>", self.html(app))
        self.assert_palette_preserved(app)
        # Chinchón is selectable again with one click; no stale toggle remains.
        self.inject_map_event(app, selection("28052"))
        self.assertEqual(app.selectbox(key="municipio_codigo").value, "28052")
        self.assertEqual(app.query_params, {"municipio": "28052", "vivienda": "VC"})
        self.assert_palette_preserved(app)

    def test_clicking_current_municipality_preserves_map_identity_and_camera(self):
        app = self.new_app()
        previous_key = app.get("deck_gl_json_chart")[0].key
        self.inject_map_event(app, selection("28120"))
        self.assertEqual(app.selectbox(key="municipio_codigo").value, "28120")
        self.assertEqual(app.query_params, {"municipio": "28120", "vivienda": "VC"})
        self.assertEqual(app.get("deck_gl_json_chart")[0].key, previous_key)
        self.assert_palette_preserved(app)

    def test_reset_view_renews_chart_and_preserves_selection_profile_and_frame(self):
        app = self.new_app()
        app.radio(key="encuadre_mapa").set_value("municipio").run()
        self.assert_no_errors(app)
        previous_key = app.get("deck_gl_json_chart")[0].key
        app.button(key="restablecer_vista_mapa").click().run()
        self.assert_no_errors(app)
        self.assertNotEqual(app.get("deck_gl_json_chart")[0].key, previous_key)
        self.assertEqual(app.radio(key="encuadre_mapa").value, "municipio")
        self.assertEqual(app.selectbox(key="municipio_codigo").value, "28120")
        self.assertEqual(app.selectbox(key="perfil_vivienda").value, "VC")
        self.assertEqual(app.query_params, {"municipio": "28120", "vivienda": "VC"})
        self.assert_palette_preserved(app)

    def test_selections_keep_explicit_municipal_camera_and_chart_id(self):
        app = self.new_app()
        app.radio(key="encuadre_mapa").set_value("municipio").run()
        for code in ("28052", "28001", "28120", "28120"):
            self.inject_map_event(app, selection(code))
            self.assertEqual(app.selectbox(key="municipio_codigo").value, code)
        self.inject_map_event(app, {"selection": {"indices": {}, "objects": {}}})
        self.assertEqual(app.selectbox(key="municipio_codigo").value, "28120")

    def test_all_24_large_municipality_events_preserve_actual_selection(self):
        with (ROOT / "data/processed/municipios/maestro_municipios.csv").open(encoding="utf-8-sig", newline="") as handle:
            excluded = [row["codigo_ine"] for row in csv.DictReader(handle) if row["elegible_hasta_50000"] == "False"]
        self.assertEqual(len(excluded), 24)
        app = self.new_app()
        for code in excluded:
            with self.subTest(codigo_ine=code):
                previous_key = app.get("deck_gl_json_chart")[0].key
                self.inject_map_event(app, selection(code))
                self.assertEqual(app.selectbox(key="municipio_codigo").value, "28120")
                self.assertEqual(app.selectbox(key="perfil_vivienda").value, "VC")
                self.assertEqual(app.query_params, {"municipio": "28120", "vivienda": "VC"})
                self.assertIn("<h2>Rascafría</h2>", self.html(app))
                self.assertEqual(app.get("deck_gl_json_chart")[0].key, previous_key)
                self.assert_palette_preserved(app)


if __name__ == "__main__":
    unittest.main()
