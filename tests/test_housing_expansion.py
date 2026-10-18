"""Housing proposal: real source joins, same-year ratios, missingness and isolation."""
from pathlib import Path
from copy import deepcopy
from tempfile import TemporaryDirectory
import importlib.util,json,unittest,shutil
import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal,assert_series_equal
from streamlit.testing.v1 import AppTest
from src.housing_calculation import calculate_composite_dimension
from src.ivp_calculation import calculate_index,DIMENSIONS
from src.integration_common import IntegrationError
from src.app_data import load_app_data
from src.app_housing_expansion import load_expansion,build_proposal_frame
from src.app_explorer import build_explorer_frame,filter_explorer,ranking_view
from src.app_home_ui import top_five

ROOT=Path(__file__).resolve().parents[1]

class CompositeTests(unittest.TestCase):
 def test_internal_weights_do_not_increase_global_housing_weight(self):
  scores=pd.DataFrame({'coste':[100.,20.],'relacion':[0.,80.]},index=['28001','28002'])
  housing=calculate_composite_dimension(scores,{'coste':3,'relacion':1})
  self.assertEqual(housing.puntuacion.tolist(),[75.,35.])
  four=pd.DataFrame({d:[50.,50.] for d in DIMENSIONS},index=scores.index);four.vivienda=housing.puntuacion
  ivp=calculate_index(four,{d:1 for d in DIMENSIONS})
  self.assertEqual(ivp.valor_indice.tolist(),[56.25,46.25])
 def test_missing_is_not_zero_or_locally_reweighted(self):
  scores=pd.DataFrame({'coste':[0.,np.nan,100.],'relacion':[100.,50.,np.nan]})
  out=calculate_composite_dimension(scores,{'coste':.75,'relacion':.25})
  self.assertEqual(out.puntuacion.iloc[0],25.);self.assertTrue(out.puntuacion.iloc[1:].isna().all())
  self.assertEqual(json.loads(out.indicadores_faltantes_json.iloc[2]),['relacion'])
  zero=calculate_composite_dimension(scores,{'coste':1.,'relacion':0.})
  self.assertEqual(zero.puntuacion.iloc[2],100.)
 def test_invalid_inputs_rejected_without_mutation(self):
  original=pd.DataFrame({'coste':[20.],'relacion':[60.]});copy=original.copy()
  for weights in [{},{'coste':0,'relacion':0},{'coste':-1,'relacion':2},{'coste':True,'relacion':1},{'coste':float('inf'),'relacion':1}]:
   with self.assertRaises(IntegrationError):calculate_composite_dimension(original,weights)
  for value in [101.,-1.,float('inf'),'10',True]:
   bad=original.astype(object);bad.loc[0,'coste']=value
   with self.assertRaises(IntegrationError):calculate_composite_dimension(bad,{'coste':1,'relacion':1})
  assert_frame_equal(original,copy)

class SourceContractTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  spec=importlib.util.spec_from_file_location('prepare_housing',ROOT/'scripts/prepare_housing_expansion.py')
  cls.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.module)
 def test_source_specific_decimal_and_missing_markers(self):
  self.assertEqual(self.module.number('31.213',True),31213.)
  self.assertEqual(self.module.number('109.085'),109.085)
  self.assertEqual(self.module.number('0'),0.)
  for marker in ['-','.','..','']:
   self.assertTrue(np.isnan(self.module.number(marker)))
  for invalid in ['aprox 12','10,4','False']:
   with self.assertRaises(IntegrationError):self.module.number(invalid)
 def test_unrecognized_code_and_conflicting_name_rejected(self):
  master=pd.DataFrame([{'codigo_almudena':'0014','codigo_ine':'28001','municipio':'La Acebeda','municipio_padron':'Acebeda (La)'}])
  for code,name in [('9999','Sin municipio asignado'),('0014','Ajalvir')]:
   source=pd.DataFrame([{'Tipo territorio':'Municipios','Código territorio':code,'Territorio':name}])
   with self.assertRaises(IntegrationError):self.module.municipal(source,master)

class RealHousingDataTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.base=load_app_data(ROOT);cls.new=load_expansion(ROOT)
 def test_full_ine_universe_no_code_loss_or_duplicates(self):
  expected=set(self.base['municipios'].codigo_ine)
  self.assertEqual(len(expected),155)
  for key in ['contexto','comparacion','indicadores','sensibilidad']:
   self.assertEqual(set(self.new[key].codigo_ine),expected)
  self.assertEqual(len(self.new['comparacion']),310)
  self.assertEqual(self.new['contexto'].renta_neta_hogar_eur_anio.notna().sum(),155)
 def test_ratio_is_exact_and_uses_same_year_sources(self):
  raw=self.new['indicadores'];self.assertEqual(set(raw.anio),{2023})
  self.assertTrue((raw.renta_hogar>0).all())
  np.testing.assert_allclose(raw.relacion_pct,1200*raw.alquiler_mes/raw.renta_hogar,equal_nan=True)
  self.assertTrue(raw.fila_atlas.notna().all());self.assertTrue(raw.fila_serpavi.notna().all())
  self.assertEqual(set(raw.loc[raw.codigo_ine.eq('28002'),'renta_hogar']),{45022.})
 def test_coverage_matches_real_observations_and_no_aggregation_assigned_to_pueblos(self):
  c=self.new['contexto'];self.assertEqual(c.ipva_variacion_anual_pct.notna().sum(),27)
  self.assertEqual(c.valor_tasado_eur_m2.notna().sum(),4)
  self.assertEqual(c.viviendas_principales_pct.notna().sum(),155)
  self.assertEqual(c.viviendas_vacias_consumo_num.notna().sum(),111)
  self.assertEqual(set(c.viviendas_vacias_estado),{'Provisional'})
  np.testing.assert_allclose(c.viviendas_principales_pct+c.viviendas_no_principales_pct,100,atol=.03)
  small=c.loc[c.poblacion<10000];self.assertTrue(small.ipva_variacion_anual_pct.isna().all())
 def test_annual_bridge_decomposes_every_comparable_score_change(self):
  c=self.new['comparacion'].dropna(subset=['ivp_original','ivp_propuesto'])
  np.testing.assert_allclose(c.delta_puntuacion,c.efecto_cambio_anio+c.efecto_indicador_nuevo,atol=1e-12)
  np.testing.assert_allclose(c.vivienda_propuesta,.75*c.puntuacion_coste+.25*c.puntuacion_relacion,atol=1e-12)
  np.testing.assert_allclose(c.ivp_propuesto,c[[f'puntuacion_{d}' for d in DIMENSIONS]].mean(axis=1),atol=1e-12)
 def test_original_reproduced_and_never_replaced(self):
  self.assertEqual(self.new['metodologia']['version_principal'],'ivp-1.0.0')
  self.assertEqual(self.new['metodologia']['estado'],'propuesta_no_activada')
  for profile in ['VC','VU']:
   original=build_explorer_frame(self.base,profile).set_index('codigo_ine')
   saved=self.new['comparacion'].loc[self.new['comparacion'].perfil.eq(profile)].set_index('codigo_ine').reindex(original.index)
   np.testing.assert_allclose(original.ivp_general,saved.ivp_original,equal_nan=True)
 def test_proposed_general_custom_rank_top5_and_filter_share_one_version(self):
  for profile in ['VC','VU']:
   frame=build_proposal_frame(self.base,self.new,profile)
   np.testing.assert_allclose(frame.ivp_general,frame.ivp_personalizado,equal_nan=True)
   expected=self.new['metodologia']['resumen'][profile]['top5_propuesto']
   self.assertEqual(top_five(frame).codigo_ine.tolist(),expected)
   filtered=filter_explorer(frame,max_rent=6,mode='general')
   self.assertTrue(filtered.alquiler_m2_mes.le(6).all())
   before=frame.set_index('codigo_ine');after=filtered.set_index('codigo_ine')
   np.testing.assert_allclose(before.loc[after.index].ivp_general,after.ivp_general,equal_nan=True)
   self.assertEqual(frame.attrs['metodologia_version'],'ivp-vivienda-2.0-propuesta')
   partial=build_proposal_frame(self.base,self.new,profile,{'sanidad':1,'educacion':1,'transporte':1,'vivienda':0})
   self.assertTrue(partial.ivp_personalizado.notna().all())
   self.assertTrue(partial.alcance_personalizado.str.startswith('parcial').all())
 def test_sensitivity_has_fifteen_reproducible_scenarios_per_profile(self):
  s=self.new['sensibilidad'];self.assertEqual(set(s.escenarios_totales),{15})
  self.assertTrue(s.escenarios_con_indice.between(0,15).all())
  self.assertTrue((s.puesto_max.dropna()>=s.puesto_min.dropna()).all())
 def test_altered_processed_snapshot_rejected(self):
  with TemporaryDirectory() as folder:
   root=Path(folder);shutil.copytree(ROOT/'data/processed/housing_expansion',root/'data/processed/housing_expansion')
   p=root/'data/processed/housing_expansion/contexto_municipal.csv';p.write_text('alterado')
   with self.assertRaises(IntegrationError):load_expansion(root)

class HousingUiTests(unittest.TestCase):
 def test_home_stays_original_and_sources_show_both_editions(self):
  app=AppTest.from_file(str(ROOT/'app.py'),default_timeout=60).run()
  self.assertFalse(app.exception)
  self.assertEqual([b.key for b in app.button if b.key and b.key.startswith('top5_')],['top5_28128','top5_28055','top5_28120','top5_28180','top5_28060'])
  app.button(key='home_methodology').click().run();self.assertFalse(app.exception)
  self.assertTrue(any('no está activada' in e.value for e in app.info))
  self.assertTrue(any(e.key=='housing_proposal_csv' for e in app.get('download_button')))
 def test_real_municipal_housing_context_and_no_empty_purchase_category(self):
  app=AppTest.from_file(str(ROOT/'app.py'),default_timeout=60)
  app.query_params['municipio']='28120';app.run();self.assertFalse(app.exception)
  texts='\n'.join(e.value for e in app.markdown)
  self.assertIn('Características del parque residencial',texts)
  self.assertIn('Alquiler e ingresos del municipio',texts)
  self.assertNotIn('### Compra · valor tasado',texts)
