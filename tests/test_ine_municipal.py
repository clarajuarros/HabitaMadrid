"""Source integrity and complete municipal coverage, including deliberate corruptions."""
from pathlib import Path
import json,hashlib,gzip,shutil,tempfile,unittest
import numpy as np
from src.ine_municipal import build_context,load_context,context_signature,RAW,BASE
ROOT=Path(__file__).resolve().parents[1]

class IneMunicipalTests(unittest.TestCase):
    def test_all_official_municipalities_and_reference_years(self):
        context,manifest,sources=load_context(ROOT)
        self.assertEqual(len(context),179)
        self.assertEqual(int(context.elegible_ine.sum()),155)
        self.assertTrue(context.coincide_poblacion.all())
        self.assertTrue(context.coincide_nombre.all())
        self.assertEqual(set(context.censo_anio),{2025})
        self.assertEqual(set(context.padron_anio),{2025})
        self.assertTrue(np.allclose(context[['menores_16_pct','entre_16_64_pct','mayores_65_pct']].sum(axis=1),100))
        self.assertEqual(context.loc['28128','hogares_2021'],259)
        self.assertEqual(context.loc['28128','viviendas_2021'],692)
        self.assertEqual(sources['hogares']['referencia'],'2021-01-01')
        self.assertTrue(context_signature(ROOT))

    def temp_root(self,path):
        target=Path(path)
        for folder in [RAW,BASE]:shutil.copytree(ROOT/folder,target/folder)
        file=target/'data/processed/municipios/maestro_municipios.csv';file.parent.mkdir(parents=True);shutil.copyfile(ROOT/'data/processed/municipios/maestro_municipios.csv',file)
        return target

    def test_raw_corruption_cannot_be_prepared(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=self.temp_root(tmp);sources=json.loads((root/RAW/'sources.json').read_text());file=root/RAW/sources['padron']['archivo'];file.write_bytes(file.read_bytes()+b'corruption')
            with self.assertRaisesRegex(ValueError,'Fuente INE alterada'):build_context(root)

    def test_a_validly_hashed_but_missing_age_series_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=self.temp_root(tmp);file=root/RAW/'sources.json';sources=json.loads(file.read_text());record=sources['edades'];path=root/RAW/record['archivo'];data=json.loads(gzip.decompress(path.read_bytes()));data.pop();content=json.dumps(data).encode();path.write_bytes(gzip.compress(content,mtime=0));record['sha256_original']=hashlib.sha256(content).hexdigest();record['sha256_archivo']=hashlib.sha256(path.read_bytes()).hexdigest();file.write_text(json.dumps(sources))
            with self.assertRaisesRegex(ValueError,'Faltan grupos'):build_context(root)

    def test_changed_processed_data_cannot_be_displayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=self.temp_root(tmp);file=root/BASE/'contexto_municipal.csv';file.write_text(file.read_text().replace(',259,',',999,'))
            with self.assertRaisesRegex(ValueError,'Contexto INE alterado'):load_context(root)

    def test_rebuild_preserves_every_processed_observation(self):
        rebuilt,history,_=build_context(ROOT);loaded,_,_=load_context(ROOT)
        self.assertTrue(np.array_equal(rebuilt.set_index('codigo_ine').poblacion_ine,loaded.poblacion_ine))
        self.assertEqual(len(history.codigo_ine.unique()),179)
