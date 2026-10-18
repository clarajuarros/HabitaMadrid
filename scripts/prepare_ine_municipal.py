#!/usr/bin/env python3
"""Rebuild verified INE context from preserved raw downloads; no runtime network.

Run after refreshing the sources in data/raw/ine_municipal and their hashes.
A newer padrón that changes the master deliberately stops preparation until
all upstream demographic eligibility dependencies have been regenerated.
"""
from pathlib import Path
import sys,json,hashlib,datetime
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.ine_municipal import build_context, BASE, RAW


def prepare(root=ROOT):
    root=Path(root);base=root/BASE;base.mkdir(parents=True,exist_ok=True)
    context,history,sources=build_context(root)
    context.to_csv(base/'contexto_municipal.csv',index=False,encoding='utf-8-sig')
    history.to_csv(base/'padron_historico_ine.csv',index=False,encoding='utf-8-sig')
    paths=[base/'contexto_municipal.csv',base/'padron_historico_ine.csv',root/RAW/'sources.json',root/'data/processed/municipios/maestro_municipios.csv',*[root/RAW/source['archivo'] for source in sources.values()]]
    manifest={'schema':1,'generado_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'municipios':len(context),'elegibles':int(context.elegible_ine.sum()),
        'padron_anio':int(context.padron_anio.max()),'censo_anio':int(context.censo_anio.max()),
        'poblaciones_discrepantes':int((~context.coincide_poblacion).sum()),
        'nombres_discrepantes':context.loc[~context.coincide_nombre,['codigo_ine','municipio','nombre_oficial_ine']].to_dict('records'),
        'uso':'Contexto y contraste; no incorpora indicadores ni altera el IVP',
        'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'files':{str(p.relative_to(root)):{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size} for p in paths}}
    (base/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in manifest.items() if k!='files'},ensure_ascii=False,indent=2))
    return manifest


if __name__=='__main__':prepare()
