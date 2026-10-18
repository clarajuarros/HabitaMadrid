"""Exercise guided discovery and shared plans against the actual Streamlit app."""
from pathlib import Path
import unittest
from streamlit.testing.v1 import AppTest
from src.app_share import encode_plan
ROOT=Path(__file__).resolve().parents[1]

class LivingUiTests(unittest.TestCase):
    def new(self):
        a=AppTest.from_file(str(ROOT/'app.py'),default_timeout=60).run();self.assertFalse(a.exception);return a
    def test_assistant_applies_profile_weights_and_filters(self):
        a=self.new();a.button(key='nav_ideal').click().run();a.selectbox(key='assistant_profile').set_value('VC');a.selectbox(key='assistant_population').set_value('Menos de 5.000 habitantes');a.multiselect(key='assistant_priorities').set_value(['sanidad','educacion']);a.number_input(key='assistant_budget').set_value(8.0)
        a.button(key='FormSubmitter:municipio_ideal-Descubrir mi municipio ideal').click().run()
        self.assertFalse(a.exception);self.assertEqual(a.selectbox(key='perfil_vivienda').value,'VC');self.assertEqual(a.radio(key='modo_indice').value,'personalizado');self.assertEqual(a.slider(key='filtro_poblacion').value,(0,4999));self.assertEqual(a.slider(key='peso_sanidad').value,100);self.assertEqual(a.slider(key='peso_transporte').value,25)
        self.assertTrue(any(b.key.startswith('candidate_') for b in a.button if b.key))
        a.button(key='candidate_'+next(b.key[10:] for b in a.button if b.key and b.key.startswith('candidate_'))).click().run();self.assertFalse(a.exception)
    def test_shared_plan_restores_comparison_and_priorities(self):
        p={'v':1,'code':'28052','profile':'VC','mode':'personalizado','weights':{'sanidad':100,'educacion':25,'transporte':25,'vivienda':50},'population':[0,50000],'filters':{'alquiler_max':9.5},'query':'','compare':['28120','28052','28008'],'only_index':True,'recommendations':True}
        a=AppTest.from_file(str(ROOT/'app.py'),default_timeout=60);a.query_params['plan']=encode_plan(p);a.run();self.assertFalse(a.exception)
        self.assertEqual(a.selectbox(key='municipio_codigo').value,'28052');self.assertEqual(a.multiselect(key='comparar_codigos').value,p['compare']);self.assertEqual(a.slider(key='peso_vivienda').value,50);self.assertEqual(a.number_input(key='filtro_alquiler_max').value,9.5)
    def test_bad_share_link_falls_back_to_working_explorer(self):
        a=AppTest.from_file(str(ROOT/'app.py'),default_timeout=60);a.query_params['plan']='bad!';a.run();self.assertFalse(a.exception);self.assertTrue(any('no es válido' in w.value for w in a.warning));self.assertEqual(len(a.selectbox(key='municipio_codigo').options),155)
