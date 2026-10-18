#!/usr/bin/env python3
"""Download official INE sources offline; prepare_ine_municipal.py validates them.

Review reference/publication dates against the linked INEbase table before
updating the DATE_METADATA below. Download date never substitutes reference.
"""
from pathlib import Path
import urllib.request,urllib.parse,gzip,json,hashlib,datetime
ROOT=Path(__file__).resolve().parents[1]
DATE_METADATA={'padron':('2025-01-01','2025-12-11'),'edades':('2025-01-01','2025-12-02'),'hogares':('2021-01-01','2023-06-29'),'viviendas':('2021-01-01','2023-06-29')}


def fetch(url):
    with urllib.request.urlopen(url,timeout=90) as response:
        return response.read(),response.headers.get('Last-Modified')


def download(root=ROOT):
    folder=Path(root)/'data/raw/ine_municipal';folder.mkdir(parents=True,exist_ok=True)
    group=json.loads(fetch('https://servicios.ine.es/wstempus/js/ES/VALORES_GRUPOSTABLA/68540/143059?det=1')[0])
    municipalities=[r for r in group if r['Variable']['Id']==19 and str(r['Codigo']).startswith('28')]
    params=[('nult','1'),('tip','AM'),('tv','18:451'),('tv','771:304582')]+[('tv','19:'+str(row['Id'])) for row in municipalities]
    specs={'padron':('ine_padron_2881.csv','https://www.ine.es/jaxiT3/files/t/csv_bdsc/2881.csv','https://www.ine.es/jaxiT3/Tabla.htm?t=2881'),
           'edades':('ine_edades_68540.json','https://servicios.ine.es/wstempus/js/ES/DATOS_TABLA/68540?'+urllib.parse.urlencode(params),'https://www.ine.es/jaxiT3/Tabla.htm?t=68540'),
           'hogares':('ine_hogares_59543.csv','https://www.ine.es/jaxi/files/tpx/csv_bdsc/59543.csv','https://www.ine.es/jaxi/Tabla.htm?tpx=59543'),
           'viviendas':('ine_viviendas_59525.csv','https://www.ine.es/jaxi/files/tpx/csv_bdsc/59525.csv','https://www.ine.es/jaxi/Tabla.htm?tpx=59525')}
    # Stage all downloads before replacing the preserved edition.
    staged={};records={}
    for key,(name,url,page) in specs.items():
        content,last=fetch(url);packed=gzip.compress(content,mtime=0)
        if content.lstrip().startswith(b'<'):raise ValueError('La fuente devolvió HTML: '+key)
        if key=='edades':
            values=json.loads(content)
            if any(str(s['Data'][0]['Anyo'])!=DATE_METADATA[key][0][:4] for s in values):
                raise ValueError('Nueva edición censal: revisar sus fechas y ámbito antes de preparar')
        if key=='padron':
            import csv,io
            latest=max(int(r['Periodo']) for r in csv.DictReader(io.StringIO(content.decode('utf-8-sig')),delimiter=';'))
            if str(latest)!=DATE_METADATA[key][0][:4]:raise ValueError('Nuevo padrón: actualizar fechas y regenerar las dependencias')
        staged[name+'.gz']=packed
        reference,publication=DATE_METADATA[key]
        records[key]={'archivo':name+'.gz','url':url,'pagina':page,'referencia':reference,'publicacion':publication,'descarga_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'http_last_modified':last,'sha256_original':hashlib.sha256(content).hexdigest(),'sha256_archivo':hashlib.sha256(packed).hexdigest(),'bytes_originales':len(content),'licencia':'https://www.ine.es/aviso_legal'}
    for name,data in staged.items():(folder/name).write_bytes(data)
    (folder/'sources.json').write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n')
    print('Descargadas cuatro fuentes oficiales. Ejecutar prepare_ine_municipal.py y la auditoría antes de publicar.')


if __name__=='__main__':download()
