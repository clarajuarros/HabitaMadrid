#!/usr/bin/env python3
"""Refresh official sector checks in reports/, preserving the sealed app snapshot.

This downloads published education, migration and authorization files plus the
complete health-centre/consultorio directory. It does not infer a closure from
absence in an authorization export and does not change the IVP. Raw forensic
archives are excluded from publication by .gitignore.
"""
from pathlib import Path
import argparse,urllib.request,concurrent.futures,gzip,json,re,html,hashlib,datetime
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]


def fetch(url):
    with urllib.request.urlopen(url,timeout=60) as response:
        return response.read(),response.headers.get('Last-Modified')


def verify(output,previous_health=None):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    reference=json.loads((ROOT/'data/processed/data_quality/sector_source_verification.json').read_text())
    records={}
    for key,source in reference.items():
        content,last=fetch(source['url']);packed=gzip.compress(content,mtime=0)
        (output/source['archivo']).write_bytes(packed)
        sha=hashlib.sha256(content).hexdigest()
        records[key]={'url':source['url'],'reference_sha256':source['reference_sha256'],'download_sha256':sha,'unchanged':sha==source['reference_sha256'],'downloaded_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'last_modified':last,'archivo':source['archivo'],'sha256_archivo':hashlib.sha256(packed).hexdigest(),'bytes':len(content)}
    (output/'sector_source_verification.json').write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n')
    baseline=pd.read_csv(ROOT/'data/processed/ivp/sanidad_catalogo_revisado.csv',dtype=str).set_index('registro_id')
    rows=[];sources=[]
    for category,identifier in [('centro_salud','687'),('consultorio','688')]:
        url='https://www.comunidad.madrid/centros/buscador-centros-sanitarios?f%5B0%5D=tipo_centro_paragraph%3A'+identifier
        content,_=fetch(url);(output/(category+'.html.gz')).write_bytes(gzip.compress(content,mtime=0))
        match=re.search(r'<script type="application/json" data-drupal-selector="drupal-settings-json">(.*?)</script>',content.decode(),re.S)
        if not match:raise ValueError('Falta el mapa institucional publicado')
        features=list(json.loads(match.group(1))['leaflet'].values())[0]['features']
        for feature in features:
            link=re.search(r'<a href="([^"]+)"[^>]*>(.*?)</a>',feature.get('popup',{}).get('value',''),re.S)
            if not link:raise ValueError('Falta identidad en la instalación sanitaria')
            rows.append({'registro_id':'cm_directorio:'+str(feature['entity_id']),'nombre':html.unescape(re.sub('<[^>]+>','',link.group(2))).strip(),'latitud_directorio':feature.get('lat'),'longitud_directorio':feature.get('lon'),'categoria':category})
        sources.append({'url':url,'sha256_original':hashlib.sha256(content).hexdigest(),'consultado_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'registros':len(features)})
    current=pd.DataFrame(rows).set_index('registro_id')
    if current.index.duplicated().any():raise ValueError('Identidad sanitaria repetida')
    changes=[]
    for code in sorted(set(baseline.index)|set(current.index)):
        if code not in baseline.index or code not in current.index:
            changes.append({'registro_id':code,'tipo':'alta_baja_en_directorio','nuevo':code in current.index});continue
        for col in ['nombre','categoria','latitud_directorio','longitud_directorio']:
            before,after=baseline.loc[code,col],current.loc[code,col]
            same=before==str(after) if col in ['nombre','categoria'] else abs(float(before)-float(after))<1e-8
            if not same:changes.append({'registro_id':code,'campo':col,'anterior':before,'nuevo':after})
    summary={'fuentes':sources,'registros_directorio':len(current),'registros_previos':len(baseline),'cambios':changes,'nota':'Estar publicado no certifica apertura, horarios, asignación o capacidad.'}
    (output/'primary_health_verification.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    if previous_health and Path(previous_health).exists():
        old=pd.read_csv(previous_health,sep=';',dtype=str,keep_default_na=False).drop(columns='NIF',errors='ignore')
        new=pd.read_csv(output/records['centros_servicios_establecimientos_sanitarios']['archivo'],sep=';',dtype=str,keep_default_na=False).drop(columns='NIF',errors='ignore')
        before={k:g for k,g in old.groupby('centro_nro_registro')};after={k:g for k,g in new.groupby('centro_nro_registro')};changed=[]
        for code in sorted(set(before)|set(after)):
            x=before.get(code,old.iloc[:0]);y=after.get(code,new.iloc[:0])
            if set(map(tuple,x.to_numpy()))!=set(map(tuple,y.to_numpy())):
                ref=(y if len(y) else x).iloc[0]
                changed.append({'registro_id':code,'municipio':ref.municipio_nombre,'tipo':ref.centro_tipo,'filas_antes':len(x),'filas_despues':len(y),'cambio':'retirado_del_fichero' if not len(y) else 'añadido_al_fichero' if not len(x) else 'modificado','afecta_ivp':False,'motivo':'El IVP usa el directorio público de centros de salud y consultorios, no este registro de autorizaciones.'})
        pd.DataFrame(changed).to_csv(output/'health_register_changes.csv',index=False,encoding='utf-8-sig')
    print(json.dumps({'primaria_revisada':len(current),'cambios_primaria':len(changes),'fuentes_sectoriales':{k:v['unchanged'] for k,v in records.items()}},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'reports/audit_sectorial')
    parser.add_argument('--previous-health',type=Path,default=ROOT/'data/raw/recopilacion_2026-10-15/centros_servicios_establecimientos_sanitarios.csv')
    args=parser.parse_args();verify(args.output,args.previous_health)
