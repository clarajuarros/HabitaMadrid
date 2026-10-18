"""Official urban origins, routing fallback, map payload and reactive UI."""
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest
from src.app_map import load_map_data
from src.app_service_map import service_map_choices, build_service_map_html, service_map_payload
from src.app_services import load_service_tables
from src.service_origins import load_urban_origins
from src.service_routes import straight_distance

ROOT=Path(__file__).resolve().parents[1]
UNAVAILABLE={'status':'unavailable','reason':'Ruta no disponible para esta prueba.'}


class ServiceLocatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.geo=load_map_data(ROOT)
        cls.tables=load_service_tables(ROOT)
        cls.origins=load_urban_origins(ROOT,cls.geo).set_index('codigo_ine')

    def test_all_155_origins_are_official_capitals_inside_their_municipality(self):
        self.assertEqual(len(self.origins),155)
        self.assertEqual(set(self.origins.index),set(self.geo['origenes'].codigo_ine))
        self.assertTrue(self.origins.tipo_ign.eq('Capital de municipio').all())
        # Official NGBE/NGMEP point, not a derived municipal centroid.
        r=self.origins.loc['28120']
        self.assertAlmostEqual(r.longitud,-3.880406282,places=8)
        self.assertAlmostEqual(r.latitud,40.90404891,places=8)

    def test_choices_distance_markers_use_one_origin_without_changing_ivp_inputs(self):
        before=self.tables['origenes'].copy(deep=True)
        for code in self.origins.index:
            origin=self.origins.loc[code]
            choices=service_map_choices(self.tables,code,'sanidad',origin=origin)
            self.assertFalse(choices.empty)
            row=choices.iloc[0]
            expected=straight_distance([origin.longitud,origin.latitud],[row.longitud,row.latitud])
            self.assertAlmostEqual(row.distancia_m,expected,places=8)
            data=service_map_payload(origin,row,'sanidad',UNAVAILABLE)
            self.assertEqual(data['origin']['position'],[origin.longitud,origin.latitud])
        pd.testing.assert_frame_equal(before,self.tables['origenes'])

    def test_map_escapes_names_and_handles_hidden_panels_and_route_bounds(self):
        origin=self.origins.loc['28120'].copy()
        row=service_map_choices(self.tables,'28120','sanidad',origin=origin).iloc[0].copy()
        row['nombre']='</script><img src=x onerror=alert(1)>'
        html=build_service_map_html(origin,row,'sanidad',UNAVAILABLE)
        self.assertNotIn('</script><img',html)
        self.assertIn('ResizeObserver',html)
        self.assertIn('IntersectionObserver',html)
        self.assertIn('fitPoints.push(...routePoints)',html)
        self.assertIn('OpenStreetMap',html)

    def test_ui_updates_municipality_destination_and_fallback_without_changing_profile(self):
        with patch('src.app_service_map.get_driving_route',return_value=UNAVAILABLE):
            app=AppTest.from_file(str(ROOT/'app.py'),default_timeout=60)
            app.query_params.update({'municipio':'28120','vivienda':'VC'})
            app.session_state['localizador_servicios_abierto']=True
            app.run()
            self.assertFalse(app.exception)
            self.assertTrue(any(m.label=='Distancia en línea recta' for m in app.metric))
            chosen=service_map_choices(self.tables,'28120','sanidad',origin=self.origins.loc['28120']).iloc[-1]
            app.selectbox(key='servicio_mapa_28120_sanidad').set_value(chosen.servicio_id).run()
            self.assertFalse(app.exception)
            app.selectbox(key='municipio_codigo').set_value('28052').run()
            self.assertFalse(app.exception)
            self.assertIn('servicio_mapa_28052_sanidad',[w.key for w in app.selectbox])
            self.assertEqual(app.selectbox(key='perfil_vivienda').value,'VC')
            self.assertEqual(app.query_params['municipio'],'28052')
            self.assertTrue(any('centro urbano' in str(x.value) for x in app.markdown))

    def test_closed_locator_does_not_request_routes(self):
        with patch('src.app_service_map.get_driving_route') as request:
            app=AppTest.from_file(str(ROOT/'app.py'),default_timeout=60).run()
            self.assertFalse(app.exception)
            request.assert_not_called()
