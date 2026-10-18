"""Navigation events are consumed once, separately from native map picking."""
import json
from pathlib import Path
import re
import unittest
from streamlit.testing.v1 import AppTest
from src.app_navigation import PENDING, RESULTS, SHEET, request_navigation, target_attributes
from src.app_selection import apply_map_selection

ROOT = Path(__file__).resolve().parents[1]
SUBMIT = 'FormSubmitter:municipio_ideal-Descubrir mi municipio ideal'


def command(app):
    body = next(e.proto.body for e in app.get('html') if 'const request = ' in e.proto.body)
    return json.loads(re.search(r'const request = (.*);', body).group(1))


class NavigationStateTests(unittest.TestCase):
    def test_new_explicit_action_replaces_pending_with_distinct_token(self):
        state = {}
        request_navigation(state, SHEET, '28120')
        first = state[PENDING]['id']
        request_navigation(state, SHEET, '28120')
        self.assertNotEqual(first, state[PENDING]['id'])
        self.assertIn(state[PENDING]['id'], target_attributes(state, SHEET, '28120'))
        self.assertNotIn(state[PENDING]['id'], target_attributes(state, SHEET, '28052'))

    def test_only_internal_targets_and_valid_municipal_codes(self):
        for target, code in [('other', ''), (SHEET, ''), (SHEET, '<123>'), (SHEET, '１２３４５')]:
            with self.subTest(target=target, code=code), self.assertRaises(ValueError):
                request_navigation({}, target, code)

    def test_map_selection_requests_the_selected_sheet(self):
        state = {'municipio_codigo':'28120','perfil_vivienda':'VU',
                 'mapa_municipios':{'selection':{'objects':{'municipios':[
                     {'type':'Feature','properties':{'codigo_ine':'28052'}}]}}}}
        self.assertTrue(apply_map_selection(state, {}, ['28120','28052']))
        self.assertEqual(state[PENDING]["target"], SHEET)
        self.assertEqual(state[PENDING]["municipality"], "28052")
        self.assertEqual(state["_active_view"], "fichas")


class NavigationUiTests(unittest.TestCase):
    def new(self):
        app = AppTest.from_file(str(ROOT/'app.py'),default_timeout=60).run()
        self.assertFalse(app.exception)
        self.assertIsNone(command(app))
        return app

    def test_repeated_search_updates_sheet_once_without_recreating_map(self):
        app = self.new()
        chart = app.get('deck_gl_json_chart')[0].proto
        identity, camera = chart.id, json.loads(chart.json)['initialViewState']
        previous = None
        for code in ['28052','28001','28120']:
            app.selectbox(key='municipio_codigo').set_value(code).run()
            self.assertFalse(app.exception)
            action = command(app)
            self.assertEqual((action['target'], action['municipality']), (SHEET, code))
            self.assertNotEqual(action['id'], previous)
            previous = action['id']
            body = next(e.proto.body for e in app.get('html') if 'class="municipal-cover"' in e.proto.body)
            self.assertIn('data-habita-navigation="'+action['id']+'"', body)
            self.assertIn('INE '+code, body)
            chart = app.get('deck_gl_json_chart')[0].proto
            self.assertEqual(chart.id, identity)
            self.assertEqual(json.loads(chart.json)['initialViewState'], camera)
            app.run()
            self.assertIsNone(command(app))
        app.selectbox(key='perfil_vivienda').set_value('VC').run()
        self.assertIsNone(command(app))
        app.radio(key='modo_indice').set_value('personalizado').run()
        self.assertIsNone(command(app))
        app.slider(key='peso_sanidad').set_value(75).run()
        self.assertIsNone(command(app))

    def test_invalid_form_stays_then_repeated_valid_submissions_navigate_to_new_results(self):
        app = self.new()
        app.button(key='nav_ideal').click().run()
        app.selectbox(key='assistant_profile').set_value('VU')
        app.multiselect(key='assistant_priorities').set_value([])
        app.button(key=SUBMIT).click().run()
        self.assertTrue(any('al menos una prioridad' in e.value for e in app.error))
        self.assertIsNone(command(app))
        tokens = []
        for priorities in [['sanidad'], ['transporte','vivienda']]:
            if not app.session_state['_assistant_open']:
                app.button(key='nav_ideal').click().run()
                self.assertIsNone(command(app))
            app.multiselect(key='assistant_priorities').set_value(priorities)
            app.button(key=SUBMIT).click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            action = command(app)
            self.assertEqual(action['target'], RESULTS)
            tokens.append(action['id'])
            result_html = next(e.proto.body for e in app.get('html') if 'id="'+RESULTS+'"' in e.proto.body)
            self.assertIn(action['id'], result_html)
            candidates = [b for b in app.button if b.key and b.key.startswith('candidate_')]
            self.assertEqual(len(candidates), 3)
            app.run()
            self.assertIsNone(command(app))
        self.assertNotEqual(*tokens)
        candidate = next(b for b in app.button if b.key and b.key.startswith('candidate_'))
        code = candidate.key.removeprefix('candidate_')
        candidate.click().run()
        self.assertEqual(command(app)['municipality'], code)
        self.assertEqual(command(app)['target'], SHEET)
