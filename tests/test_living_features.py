from datetime import date
from pathlib import Path
import tempfile,sqlite3,unittest
from src.app_share import encode_plan,decode_plan,restore_plan,share_url
from src.app_living_data import active_services,timetable,gtfs_time,load_living_data
from src.app_recommendations import recommend,describe
from src.app_data import load_app_data
from src.app_explorer import build_explorer_frame

ROOT=Path(__file__).resolve().parents[1]

class ShareTests(unittest.TestCase):
    def plan(self):return {'v':1,'code':'28120','profile':'VC','mode':'personalizado','weights':{'sanidad':100,'educacion':25,'transporte':0,'vivienda':25},'population':[100,20000],'filters':{'alquiler_max':7.5},'query':'Sierra','compare':['28120','28052'],'recommendations':True,'only_index':True}
    def test_round_trip_restores_scenario_and_does_not_override_later_search(self):
        p=self.plan();token=encode_plan(p);self.assertEqual(decode_plan(token,{'28120','28052'}),p)
        state={};self.assertTrue(restore_plan(token,state,{'28120','28052'}));self.assertEqual(state['peso_transporte'],0);self.assertEqual(state['comparar_codigos'],p['compare']);self.assertEqual(state['filtro_alquiler_max'],7.5)
        state['municipio_codigo']='28052';self.assertFalse(restore_plan(token,state,{'28120','28052'}));self.assertEqual(state['municipio_codigo'],'28052')
    def test_rejects_untrusted_invalid_values(self):
        for mutate in (lambda p:p.update(code='28079'),lambda p:p['weights'].update(sanidad=25.5),lambda p:p['weights'].update(sanidad=float('nan')),lambda p:p.update(population=[20000,100]),lambda p:p.update(compare=['28120']*4),lambda p:p.update(only_index='false')):
            p=self.plan();mutate(p)
            with self.assertRaises(ValueError):decode_plan(encode_plan(p),{'28120','28052'})
        for token in ('__import__("os")','a'*7000,'////'):
            with self.assertRaises(ValueError):decode_plan(token,{'28120'})

class TimetableTests(unittest.TestCase):
    def metadata(self):return {'calendar':[{'service_id':'week','monday':'1','tuesday':'1','wednesday':'1','thursday':'1','friday':'1','saturday':'0','sunday':'0','start_date':'20260101','end_date':'20261231'}],'exceptions':[{'service_id':'week','date':'20261019','exception_type':'2'},{'service_id':'special','date':'20261019','exception_type':'1'}],'routes':{'r':{'route_short_name':'1','route_long_name':'A-B'}}}
    def test_calendar_exceptions_and_weekend(self):
        m=self.metadata();self.assertEqual(active_services(m,date(2026,10,19)),{'special'});self.assertEqual(active_services(m,date(2026,10,20)),{'week'});self.assertEqual(active_services(m,date(2026,10,18)),set());self.assertEqual(gtfs_time(25*3600+30*60),'01:30 (+1 día)')
    def test_municipal_counts_do_not_duplicate_stops_and_frequency_uses_one_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'t.sqlite';db=sqlite3.connect(p);db.executescript('CREATE TABLE stops(stop_id TEXT,codigo_ine TEXT,name TEXT);CREATE TABLE departures(stop_id TEXT,trip_id TEXT,route_id TEXT,service_id TEXT,direction_id TEXT,headsign TEXT,seconds INTEGER);')
            db.executemany('INSERT INTO stops VALUES(?,?,?)',[('a','28120','A'),('b','28120','B')]);db.executemany('INSERT INTO departures VALUES(?,?,?,?,?,?,?)',[('a','t1','r','special','0','Madrid',3600),('b','t1','r','special','0','Madrid',3700),('a','t2','r','special','0','Madrid',5400)]);db.commit();db.close();living={'database':str(p),'transport':self.metadata()}
            municipal=timetable(living,'28120',date(2026,10,19));self.assertEqual(municipal['rows'][0]['salidas'],2);self.assertIsNone(municipal['rows'][0]['intervalo_mediano_min'])
            stop=timetable(living,'28120',date(2026,10,19),'a');self.assertEqual(stop['rows'][0]['intervalo_mediano_min'],30)
            db=sqlite3.connect(p);db.execute('INSERT INTO departures VALUES(?,?,?,?,?,?,?)',('a','t3','r','special','0','Madrid',5400));db.commit();db.close()
            stop=timetable(living,'28120',date(2026,10,19),'a');self.assertEqual(stop['rows'][0]['salidas'],2);self.assertEqual(stop['rows'][0]['expediciones_gtfs'],3)
            with self.assertRaises(ValueError):timetable(living,'28120',date(2026,10,19),'untrusted')

class ActualLivingDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.data=load_app_data(ROOT);cls.frame=build_explorer_frame(cls.data,'VU')
    def test_complete_connectivity_has_real_bounded_values_and_period(self):
        x=load_living_data(ROOT)['connectivity'];self.assertEqual(set(x['municipalities']),set(self.frame.codigo_ine));self.assertEqual(x['period'],'2025-06-30')
        for r in x['municipalities'].values():
            for k in ('fibra_pct','banda100_pct','movil5g_pct'):self.assertTrue(r[k] is None or 0<=r[k]<=100)
    def test_missing_scores_are_not_recommended_or_described_as_zero(self):
        result=recommend(self.frame,'general');self.assertEqual(len(result),3);self.assertTrue(result.ivp_general.notna().all());self.assertTrue(result.ivp_general.is_monotonic_decreasing)
        acebeda=self.frame.set_index('codigo_ine').loc['28001'];x=describe(acebeda,self.frame);self.assertIn('Vivienda',x['missing']);self.assertTrue(any('Falta información' in t for t in x['consider']))
    def test_personalized_recommendations_follow_current_weights(self):
        f=build_explorer_frame(self.data,'VU',{'sanidad':100,'educacion':0,'transporte':0,'vivienda':0});x=recommend(f,'personalizado');self.assertTrue(x.ivp_personalizado.is_monotonic_decreasing);self.assertEqual(describe(x.iloc[0],f,'personalizado')['active'],1)

if __name__=='__main__':unittest.main()
