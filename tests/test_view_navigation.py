"""Citizen journeys across views, including Streamlit widget cleanup cycles."""
import unittest
import json
from pathlib import Path
from streamlit.testing.v1 import AppTest

ROOT=Path(__file__).resolve().parents[1]


def html(app):
    return '\n'.join(e.proto.body for e in app.get('html'))


class ViewJourneys(unittest.TestCase):
    def new(self):
        app=AppTest.from_file(str(ROOT/'app.py'),default_timeout=60).run()
        self.check(app)
        return app

    def check(self, app):
        self.assertFalse(app.exception, [e.value for e in app.exception])

    def goto(self,app,view,mobile=False):
        app.button(key=('mobile_nav_' if mobile else 'nav_')+view).click().run()
        self.check(app)
        self.assertEqual(app.session_state['_active_view'],view)
        self.assertFalse(app.session_state['mobile_menu'])
        return app

    def test_neutral_home_and_every_functionality_has_a_destination(self):
        app=self.new()
        self.assertEqual(app.session_state['_active_view'],'inicio')
        self.assertIsNone(app.session_state['municipio_codigo'])
        body=html(app)
        self.assertIn('¿Qué es el IVP?',body)
        self.assertIn('encaja contigo',body)
        self.assertNotIn('class="municipal-cover"',body)
        self.assertEqual(len([b for b in app.button if b.key and b.key.startswith('top5_')]),5)
        app.button(key='feature_fichas').click().run();self.check(app)
        self.assertIn('Cada municipio tiene su historia',html(app))
        self.goto(app,'inicio')
        app.button(key='home_ideal').click().run();self.check(app)
        self.assertEqual(app.session_state['_active_view'],'ideal')
        self.assertTrue(app.selectbox(key='assistant_profile'))
        self.goto(app,'inicio')
        app.button(key='feature_comparar').click().run();self.check(app)
        self.assertEqual(app.multiselect(key='comparar_codigos').value,[])
        self.goto(app,'inicio')
        app.button(key='home_methodology').click().run();self.check(app)
        self.assertEqual(app.session_state['_active_view'],'fuentes')
        self.assertIsNone(app.session_state['municipio_codigo'])
        self.assertIn('Fuentes utilizadas y enlaces de descarga',[e.label for e in app.expander])

    def test_map_identity_selection_priorities_filters_and_compare_survive_roundtrips(self):
        app=self.new();self.goto(app,'mapa')
        chart=app.get('deck_gl_json_chart')[0].proto
        identity,camera=chart.id,json.loads(chart.json)['initialViewState']
        app.radio(key='modo_indice').set_value('personalizado').run()
        app.slider(key='peso_sanidad').set_value(70).run()
        app.slider(key='filtro_poblacion').set_value((5000,40000)).run()
        app.selectbox(key='municipio_codigo').set_value('28052').run();self.check(app)
        self.assertEqual(app.session_state['_active_view'],'fichas')
        self.assertIn('INE 28052',html(app))
        self.goto(app,'comparar')
        app.multiselect(key='comparar_codigos').set_value(['28052','28120']).run();self.check(app)
        for view in ['inicio','ideal','fichas','comparar','mapa']:
            self.goto(app,view)
            self.assertEqual(app.session_state['municipio_codigo'],'28052')
            self.assertEqual(app.session_state['peso_sanidad'],70)
            self.assertEqual(app.session_state['filtro_poblacion'],(5000,40000))
            self.assertEqual(app.session_state['comparar_codigos'],['28052','28120'])
            self.assertEqual(app.get('deck_gl_json_chart')[0].proto.id,identity)
            self.assertEqual(json.loads(app.get('deck_gl_json_chart')[0].proto.json)['initialViewState'],camera)
        self.assertEqual(app.multiselect(key='comparar_codigos').value if app.session_state['_active_view']=='comparar' else app.session_state['comparar_codigos'],['28052','28120'])
        app.button(key='reset_application').click().run();self.check(app)
        self.assertEqual(app.session_state['_active_view'],'inicio')
        self.assertIsNone(app.session_state['municipio_codigo'])
        self.assertEqual(app.session_state['peso_sanidad'],25)
        self.assertEqual(app.session_state['comparar_codigos'],[])
        self.assertEqual(app.session_state['filtro_poblacion'],(0,50000))

    def test_mobile_navigation_assistant_answers_and_top_five(self):
        app=self.new();self.goto(app,'ideal',mobile=True)
        app.selectbox(key='assistant_profile').set_value('VC')
        app.multiselect(key='assistant_priorities').set_value(['sanidad','transporte'])
        app.button(key='FormSubmitter:municipio_ideal-Descubrir mi municipio ideal').click().run();self.check(app)
        self.assertTrue(app.session_state['_assistant_results'])
        candidates=[b for b in app.button if b.key and b.key.startswith('candidate_')]
        self.assertEqual(len(candidates),3)
        chosen=candidates[0].key.removeprefix('candidate_')
        self.goto(app,'inicio',mobile=True);self.goto(app,'ideal',mobile=True)
        self.assertEqual(app.selectbox(key='assistant_profile').value,'VC')
        self.assertEqual(app.multiselect(key='assistant_priorities').value,['sanidad','transporte'])
        app.button(key='candidate_'+chosen).click().run();self.check(app)
        self.assertEqual(app.session_state['_active_view'],'fichas')
        self.assertEqual(app.session_state['municipio_codigo'],chosen)
        self.goto(app,'inicio')
        app.button(key='top5_28055').click().run();self.check(app)
        self.assertEqual(app.session_state['_active_view'],'fichas')
        self.assertEqual(app.session_state['municipio_codigo'],'28055')

    def test_shared_link_opens_sheet_and_home_keeps_selection(self):
        app=AppTest.from_file(str(ROOT/'app.py'),default_timeout=60)
        app.query_params['municipio']='28120';app.query_params['vivienda']='VC'
        app.run();self.check(app)
        self.assertEqual(app.session_state['_active_view'],'fichas')
        self.goto(app,'inicio')
        app.run();self.check(app)
        self.assertEqual(app.session_state['_active_view'],'inicio')
        self.assertEqual(app.session_state['municipio_codigo'],'28120')
        self.assertNotIn('class="municipal-cover"',html(app))
        app.selectbox(key='home_housing').set_value('VU').run();self.check(app)
        self.assertEqual(app.session_state['perfil_vivienda'],'VU')
        self.goto(app,'fichas');self.assertEqual(app.selectbox(key='perfil_vivienda').value,'VU')
