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
        app.button(key=('mobile_nav_' if mobile or view=='fuentes' else 'nav_')+view).click().run()
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
        featured=[b for b in app.button if b.key and b.key.startswith('top5_')]
        self.assertEqual(len(featured),5)
        featured_code=featured[0].key.removeprefix('top5_')
        app.button(key='top5_'+featured_code).click().run();self.check(app)
        self.assertEqual(app.session_state['_active_view'],'fichas')
        self.assertEqual(app.session_state['municipio_codigo'],featured_code)

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

    def test_home_buttons_and_clickable_identity_preserve_the_current_work(self):
        app=self.new()
        for destination in ('mapa','fichas','ideal','comparar'):
            button=app.button(key='feature_'+destination)
            self.assertEqual(button.proto.type,'secondary')
            button.click().run();self.check(app)
            self.assertEqual(app.session_state['_active_view'],destination)
            if destination=='fichas':
                app.selectbox(key='municipio_codigo').set_value('28120').run();self.check(app)
            app.button(key='nav_home_logo').click().run();self.check(app)
            self.assertEqual(app.session_state['_active_view'],'inicio')
        self.assertEqual(app.session_state['municipio_codigo'],'28120')
        self.assertEqual(app.button(key='feature_fichas').label,'Buscar mi municipio')
        self.assertEqual(app.button(key='reset_application').label,'Reiniciar aplicación')
        app.button(key='nav_close').click().run();self.check(app)
        self.assertFalse(app.session_state['mobile_menu'])

    def test_public_views_keep_housing_context_without_the_experimental_comparison(self):
        app=self.new()
        for destination in ('fuentes','fichas','comparar'):
            self.goto(app,destination)
            if destination=='fichas':
                app.selectbox(key='municipio_codigo').set_value('28120').run();self.check(app)
                self.assertIn('### Características del parque residencial',[e.value for e in app.markdown])
            labels=[e.label for e in app.expander]
            self.assertFalse(any('propuesta ampliada' in label or 'ampliando la valoración' in label for label in labels))
            self.assertFalse(any('IVP propuesto' in e.value for e in app.markdown))
            self.assertFalse(any(e.key=='housing_proposal_comparison' for e in app.get('plotly_chart')))
