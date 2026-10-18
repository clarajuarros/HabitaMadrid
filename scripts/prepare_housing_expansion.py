"""Prepare a separate, reviewable housing proposal; never replace IVP 1.0.

Run with --download once, then run offline with no arguments to reproduce it.
"""
from pathlib import Path
from datetime import datetime, timezone
import argparse, gzip, hashlib, json, re, shutil, sys, unicodedata
from io import BytesIO
from urllib.request import urlopen
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.app_data import load_app_data
from src.integration_common import IntegrationError
from src.ivp_calculation import (DIMENSIONS, METHODS, calculate_index,
    fit_normalization, transform_normalization)
from src.housing_calculation import calculate_composite_dimension
from src.housing_validation import controlled_diagnostics

RAW=ROOT/'data/reference/housing_expansion'
OUT=ROOT/'data/processed/housing_expansion'
VERSION='ivp-vivienda-2.0-propuesta'
SOURCES={
 'atlas':('INE · Atlas de distribución de renta de los hogares, Madrid', 'https://www.ine.es/jaxiT3/files/t/es/csv_bdsc/31097.csv','https://www.ine.es/jaxiT3/Tabla.htm?t=31097'),
 'ipva':('INE/Comunidad de Madrid · IPVA, base 2015','4c5a6c8b-fdf7-4e20-a92d-92c119571fc6',None),
 'census_type':('INE/Comunidad de Madrid · Censo 2021, vivienda principal/no principal','74a52c41-11c4-4768-bf95-ed3271655843',None),
 'census_usage':('INE/Comunidad de Madrid · Censo 2021, viviendas vacías por consumo eléctrico','70f8104c-efae-4fd4-b611-c19b7373aebc',None),
 'census_age':('INE/Comunidad de Madrid · Censo 2021, año de construcción','8a594474-a275-4c74-8d2f-8f864e6d3d45',None),
 'purchase':('MIVAU/Comunidad de Madrid · Valor tasado por municipio y antigüedad','86ed5966-29e2-4943-a749-dfad2f6879cb',None),
}
MISSING={'','-','.','..','...','N/D','ND','n.d.'}


def sha(raw):return hashlib.sha256(raw).hexdigest()
def dump(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
def norm(text):return ''.join(c for c in unicodedata.normalize('NFKD',str(text).casefold()) if not unicodedata.combining(c) and c.isalnum())

def download(keys=None):
 RAW.mkdir(parents=True,exist_ok=True)
 records=json.loads((RAW/'sources.json').read_text()) if keys and (RAW/'sources.json').exists() else {}
 for key,(title,reference,page) in SOURCES.items():
  if keys and key not in keys:continue
  if page:
   url=reference;metadata={'title':title,'license_url':'https://www.ine.es/aviso_legal'}
  else:
   api='https://datos.comunidad.madrid/api/3/action/package_show?id='+reference
   metadata=json.load(urlopen(api,timeout=40))['result']
   resource=next(x for x in metadata['resources'] if x['format'].upper()=='CSV')
   url=resource['url'];page='https://datos.comunidad.madrid/dataset/'+reference
  raw=urlopen(url,timeout=90).read()
  if len(raw)>100_000_000:raise IntegrationError('Fuente demasiado grande')
  filename=key+'.csv.gz';compressed=gzip.compress(raw,mtime=0);(RAW/filename).write_bytes(compressed)
  meta=key+'_metadata.json';dump(RAW/meta,metadata)
  records[key]={'titulo':title,'url':url,'pagina':page,'archivo':filename,
    'bytes_originales':len(raw),'sha256_original':sha(raw),'sha256_archivo':sha(compressed),
    'metadatos':meta,'sha256_metadatos':sha((RAW/meta).read_bytes()),
    'descarga_utc':datetime.now(timezone.utc).isoformat(),
    'licencia':metadata.get('license_url',metadata.get('license_title','Consultar condiciones oficiales'))}
  print('Descargado:',key,len(raw),flush=True)
 dump(RAW/'sources.json',records)


def source(key):
 records=json.loads((RAW/'sources.json').read_text());record=records[key]
 raw=(RAW/record['archivo']).read_bytes()
 if sha(raw)!=record['sha256_archivo']:raise IntegrationError('Huella incorrecta: '+key)
 if sha((RAW/record['metadatos']).read_bytes())!=record['sha256_metadatos']:raise IntegrationError('Metadatos alterados: '+key)
 raw=gzip.decompress(raw)
 if sha(raw)!=record['sha256_original'] or len(raw)!=record['bytes_originales']:raise IntegrationError('Fuente alterada: '+key)
 encoding='utf-8-sig' if key=='atlas' else 'cp1252'
 frame=pd.read_csv(BytesIO(raw),sep=';',dtype=str,keep_default_na=False,encoding=encoding)
 frame['fila_fuente']=range(2,len(frame)+2)
 return frame,record


def number(value,thousands=False):
 if value in MISSING:return np.nan
 text=value.replace('.','') if thousands else value
 if not re.fullmatch(r'[+-]?[0-9]+(?:\.[0-9]+)?',text):raise IntegrationError('Número no reconocido: '+repr(value))
 result=float(text)
 if not np.isfinite(result):raise IntegrationError('Número no finito')
 return result


def municipal(frame,master):
 frame=frame.loc[frame['Tipo territorio'].eq('Municipios')].copy()
 codes=master.set_index('codigo_almudena').codigo_ine.to_dict()
 frame['codigo_ine']=frame['Código territorio'].map(codes)
 if frame.codigo_ine.isna().any():raise IntegrationError('Código Almudena desconocido')
 allowed={norm(x):row.codigo_ine for row in master.itertuples() for x in (row.municipio,row.municipio_padron)}
 for row in frame.itertuples(index=False):
  # Source retains its original name; code is authoritative, name corroborates.
  name=getattr(row,'Territorio')
  if allowed.get(norm(name))!=row.codigo_ine:raise IntegrationError('Nombre y código municipal no corresponden: '+name)
 return frame


def unique(frame,keys):
 if frame.duplicated(keys).any():raise IntegrationError('Claves duplicadas: '+str(keys))


def spearman(a,b):
 x=pd.concat([a,b],axis=1).dropna()
 if len(x)<3:return None
 corr=x.iloc[:,0].rank().corr(x.iloc[:,1].rank())
 return None if pd.isna(corr) else float(corr)


def prepare():
 OUT.mkdir(parents=True,exist_ok=True)
 data=load_app_data(ROOT)
 master=pd.read_csv(ROOT/'data/processed/municipios/maestro_municipios.csv',dtype=str,keep_default_na=False)
 eligible=data['municipios'][['codigo_ine','municipio','poblacion']].set_index('codigo_ine')
 atlas,ar=source('atlas')
 atlas=atlas.loc[atlas.Distritos.eq('')&atlas.Secciones.eq('')].copy()
 atlas['codigo_ine']=atlas.Municipios.str.extract(r'^(28\d{3}) ',expand=False)
 if atlas.codigo_ine.isna().any() or not set(atlas.codigo_ine).issubset(set(master.codigo_ine)):
  raise IntegrationError('Atlas no corresponde a municipios de Madrid')
 official_names={norm(x):row.codigo_ine for row in master.itertuples() for x in (row.municipio,row.municipio_padron)}
 if not atlas.Municipios.str[6:].map(norm).map(official_names).eq(atlas.codigo_ine).all():raise IntegrationError('Atlas: nombre y código no coinciden')
 income=atlas.loc[atlas['Indicadores de renta media y mediana'].eq('Renta neta media por hogar')].copy()
 income['anio']=income.Periodo.astype(int);income['renta_hogar']=income.Total.map(lambda x:number(x,True))
 unique(income,['codigo_ine','anio'])
 if (income.renta_hogar.dropna()<=0).any():raise IntegrationError('Renta de hogar no positiva')
 hist=data['vivienda_historico'].copy()
 common=sorted(set(income.anio)&set(hist.anio.astype(int)))
 year=max(common)
 # Same-year numerator and denominator; no extrapolation or CPI imputation.
 income=income.loc[income.anio.eq(year)].set_index('codigo_ine')
 if not set(eligible.index).issubset(income.index):raise IntegrationError('Faltan filas de renta municipal')
 extras=eligible.copy();extras['renta_neta_hogar_eur_anio']=income.renta_hogar.reindex(extras.index)
 extras['renta_anio']=year;extras['renta_fila_fuente']=income.fila_fuente.reindex(extras.index)
 # Principales/no principales are not equivalent to available/vacant homes.
 typ,tr=source('census_type');typ=municipal(typ,master);typ['valor']=typ.Valor.map(number)
 unique(typ,['codigo_ine','Año','Tipo'])
 if not typ.valor.dropna().between(0,100).all():raise IntegrationError('Porcentaje censal fuera de rango')
 if set(typ['Año'])!={'2021'} or set(typ.Unidad)!={'Porcentaje'}:raise IntegrationError('Censo: año o unidad inesperados')
 wide=typ.pivot(index='codigo_ine',columns='Tipo',values='valor')
 if ((wide.sum(axis=1,min_count=2)-100).abs().dropna()>.03).any():raise IntegrationError('Porcentajes censales no suman cien')
 extras['viviendas_principales_pct']=wide['Principales'].reindex(extras.index)
 extras['viviendas_no_principales_pct']=wide['No principales'].reindex(extras.index)
 extras['censo_anio']=2021
 age,agr=source('census_age');age=municipal(age,master);age['valor']=age.Valor.map(number)
 unique(age,['codigo_ine','Año','Año construcción'])
 if (age.valor.dropna()<0).any() or (age.valor.dropna()%1).ne(0).any():raise IntegrationError('Recuento censal no válido')
 if set(age['Año'])!={'2021'} or set(age.Unidad)!={'Número'}:raise IntegrationError('Antigüedad: unidad o año inesperados')
 agewide=age.pivot(index='codigo_ine',columns='Año construcción',values='valor')
 before=[x for x in agewide if x=='Antes del año 1900' or re.search(r'y (1920|1940|1950|1960|1970|1980)$',x)]
 if len(before)!=7:raise IntegrationError('Intervalos de antigüedad cambiados')
 total=agewide.sum(axis=1,min_count=len(agewide.columns))
 numerator=agewide[before].sum(axis=1,min_count=len(before))
 extras['viviendas_anteriores_1981_pct']=(100*numerator/total.where(total>0)).reindex(extras.index)
 usage,ur=source('census_usage')
 usage_aggregate=usage.loc[usage['Código territorio'].eq('9999')]
 if not usage_aggregate.Territorio.eq('Sin municipio asignado').all():raise IntegrationError('Agregado de consumo inesperado')
 usage_aggregate.to_csv(OUT/'consumo_agregados_excluidos.csv',index=False,encoding='utf-8-sig')
 usage=municipal(usage.loc[~usage['Código territorio'].eq('9999')],master);usage['valor']=usage.Valor.map(number)
 unique(usage,['codigo_ine','Año','Consumo'])
 if set(usage['Año'])!={'2021'} or set(usage.Unidad)!={'Número'}:raise IntegrationError('Uso eléctrico: año/unidad inesperados')
 if (usage.valor.dropna()<0).any() or (usage.valor.dropna()%1).ne(0).any():raise IntegrationError('Recuento eléctrico inválido')
 vacant=usage.loc[usage.Consumo.eq('Viviendas vacias')].set_index('codigo_ine')
 extras['viviendas_vacias_consumo_num']=vacant.valor.reindex(extras.index)
 extras['viviendas_vacias_estado']=vacant['Estado dato'].reindex(extras.index)
 # IPVA is used as within-municipality annual change, never as euro price.
 ipva,ir=source('ipva')
 excluded=ipva.loc[ipva['Código territorio'].eq('9999')].copy()
 if not excluded.Territorio.eq('Sin municipio asignado').all():raise IntegrationError('Agregado IPVA inesperado')
 excluded.to_csv(OUT/'ipva_agregados_excluidos.csv',index=False,encoding='utf-8-sig')
 ipva=municipal(ipva.loc[~ipva['Código territorio'].eq('9999')],master);ipva['valor']=ipva.Valor.map(number)
 if (ipva.valor.dropna()<=0).any():raise IntegrationError('Índice de precios no positivo')
 unique(ipva,['codigo_ine','Año'])
 if set(ipva.Unidad)!={'Índice'}:raise IntegrationError('IPVA: unidad inesperada')
 ipyear=max(ipva['Año'].astype(int));ip=ipva.assign(anio=ipva['Año'].astype(int)).pivot(index='codigo_ine',columns='anio',values='valor')
 extras['ipva_variacion_anual_pct']=(100*(ip[ipyear]/ip[ipyear-1].where(ip[ipyear-1]>0)-1)).reindex(extras.index)
 extras['ipva_anio']=ipyear
 # This source has names, no INE column: exact official-name crosswalk only.
 buy,br=source('purchase');names={norm(x):row.codigo_ine for row in master.itertuples() for x in (row.municipio,row.municipio_padron)}
 buy['codigo_ine']=buy.Territorio.map(lambda x:names.get(norm(x)))
 if buy.codigo_ine.isna().any():raise IntegrationError('Compra: nombre sin correspondencia oficial')
 buy=buy.loc[buy.Antigüedad.eq('Total')&buy.Indicador.eq('Serie')].copy()
 buy['valor']=buy.Valor.map(number)
 unique(buy,['codigo_ine','Año','Periodo'])
 if (buy.valor.dropna()<=0).any():raise IntegrationError('Tasación no positiva')
 if set(buy.Unidad)!={'Euros/m2'}:raise IntegrationError('Compra: unidad inesperada')
 if not buy.Periodo.str.fullmatch('T[1-4]').all():raise IntegrationError('Periodo de tasaciones inesperado')
 latest=buy['Año'].astype(int)*10+buy.Periodo.str[1:].astype(int);period=int(latest.max());buy=buy.loc[latest.eq(period)].set_index('codigo_ine')
 extras['valor_tasado_eur_m2']=buy.valor.reindex(extras.index)
 extras['tasacion_anio']=period//10;extras['tasacion_trimestre']='T'+str(period%10)
 extras['tasacion_fila_fuente']=buy.fila_fuente.reindex(extras.index)
 extras.to_csv(OUT/'contexto_municipal.csv',index=True,encoding='utf-8-sig')
 # Archive the unmodified original and its calculation source before proposal.
 frozen=OUT/'original_ivp_1';frozen.mkdir(exist_ok=True)
 baseline_records={}
 for name in ['ivp_general.csv','dimensiones_ivp.csv','normalizacion.json','configuracion_ivp.json','observaciones_seleccionadas.csv']:
  src=ROOT/'data/processed/ivp'/name;dest=frozen/name
  if dest.exists() and dest.read_bytes()!=src.read_bytes():raise IntegrationError('La referencia original ya no coincide: '+name)
  if not dest.exists():shutil.copy2(src,dest)
  baseline_records[name]=sha(src.read_bytes())
 engine=ROOT/'src/ivp_calculation.py'
 engine_frozen=frozen/'ivp_calculation.py'
 if engine_frozen.exists() and engine_frozen.read_bytes()!=engine.read_bytes():raise IntegrationError('Motor original alterado')
 if not engine_frozen.exists():shutil.copy2(engine,engine_frozen)
 config={'original_engine_sha256':sha(engine.read_bytes()),'version':VERSION,'estado':'propuesta_no_activada','version_principal':data['configuracion']['metodologia_version'],
   'anio_vivienda_propuesta':year,'anio_alquiler_original':2024,'peso_coste':.75,'peso_relacion_alquiler_ingresos':.25,
   'formula_relacion':'100 * 12 * mediana_alquiler_mensual_SERPAVI / renta_neta_media_anual_hogar_INE',
   'interpretacion_relacion':'Cociente de estadísticas agregadas del mismo año y municipio; no esfuerzo observado de los inquilinos ni porcentaje de hogares sobrecargados.',
   'ausencias':'Sin vivienda compuesta si falta cualquier indicador con peso interno positivo. Sin redistribución municipal.',
   'global':{d:.25 for d in DIMENSIONS},'original_sha256':baseline_records,
   'sources':json.loads((RAW/'sources.json').read_text()),'referencias':{},'resumen':{}}
 comparisons=[];sensitivities=[];inputs=[]
 for profile in ('VC','VU'):
  h=hist.loc[hist.tipo.eq(profile)&hist.anio.eq(year)].set_index('codigo_ine').reindex(eligible.index)
  cost=pd.to_numeric(h.alquiler_m2_mes_mediana,errors='raise').astype(float)
  monthly=pd.to_numeric(h.alquiler_mes_mediana,errors='raise').astype(float)
  relation=100*12*monthly/extras.renta_neta_hogar_eur_anio
  dims=data['dimensiones'].loc[data['dimensiones'].perfil_vivienda.eq(profile)].set_index('codigo_ine').reindex(eligible.index)
  original=data['ivp_general'].loc[data['ivp_general'].perfil_vivienda.eq(profile)].set_index('codigo_ine').reindex(eligible.index)
  if 'metodo' in dims and not dims.metodo.eq('minmax').all():raise IntegrationError('Se requieren dimensiones minmax')
  raw=pd.DataFrame({'coste':cost,'relacion':relation},index=eligible.index)
  trace=pd.DataFrame({'codigo_ine':eligible.index,'perfil':profile,'anio':year,'alquiler_m2_mes':cost.values,'alquiler_mes':monthly.values,'renta_hogar':extras.renta_neta_hogar_eur_anio.values,'relacion_pct':relation.values,'fila_serpavi':h.fila_fuente_excel.values,'fila_atlas':extras.renta_fila_fuente.values})
  inputs.append(trace)
  refs={key:fit_normalization(raw[key],'menor_mejor','minmax') for key in raw}
  config['referencias'][profile]=refs
  scores=pd.DataFrame({key:transform_normalization(raw[key],refs[key]) for key in raw})
  housing=calculate_composite_dimension(scores,{'coste':.75,'relacion':.25})
  newdims=dims[list(DIMENSIONS)].copy();newdims['vivienda']=housing.puntuacion
  proposed=calculate_index(newdims,{d:.25 for d in DIMENSIONS})
  bridge_dims=dims[list(DIMENSIONS)].copy();bridge_dims['vivienda']=scores.coste
  bridge=calculate_index(bridge_dims,{d:.25 for d in DIMENSIONS})
  result=eligible.copy();result['perfil']=profile;result['version_original']=data['configuracion']['metodologia_version'];result['version_propuesta']=VERSION
  result['ivp_original']=original.valor_indice;result['puesto_original']=original.posicion
  result['ivp_puente_mismo_anio']=bridge.valor_indice
  result['ivp_propuesto']=proposed.valor_indice;result['puesto_propuesto']=proposed.posicion
  result['vivienda_original']=dims.vivienda;result['vivienda_propuesta']=housing.puntuacion
  result['puntuacion_coste']=scores.coste;result['puntuacion_relacion']=scores.relacion
  result['delta_puntuacion']=result.ivp_propuesto-result.ivp_original
  result['efecto_cambio_anio']=result.ivp_puente_mismo_anio-result.ivp_original
  result['efecto_indicador_nuevo']=result.ivp_propuesto-result.ivp_puente_mismo_anio
  result['estado_propuesta']=proposed.estado_indice
  result['vivienda_faltantes_json']=housing.indicadores_faltantes_json
  common=result.ivp_original.notna()&result.ivp_propuesto.notna()
  result['puesto_original_muestra_comun']=result.ivp_original.where(common).rank(method='min',ascending=False)
  result['puesto_propuesto_muestra_comun']=result.ivp_propuesto.where(common).rank(method='min',ascending=False)
  result['cambio_puesto_comun']=result.puesto_original_muestra_comun-result.puesto_propuesto_muestra_comun
  result['cambio_puesto_global']=result.puesto_original-result.puesto_propuesto
  for d in DIMENSIONS:result['puntuacion_'+d]=newdims[d]
  summaries={'municipios':len(result),'vivienda_original':int(dims.vivienda.notna().sum()),'vivienda_propuesta':int(housing.puntuacion.notna().sum()),
   'ivp_original':int(result.ivp_original.notna().sum()),'ivp_propuesto':int(result.ivp_propuesto.notna().sum()),'comparables':int(common.sum()),
   'correlacion_indicadores_spearman':spearman(raw.coste,raw.relacion),
   'correlacion_ivp_spearman_comun':spearman(result.ivp_original.where(common),result.ivp_propuesto.where(common)),
   'cambio_max_puesto_comun':int(result.cambio_puesto_comun.abs().max()),
   'max_cambio_puntuacion':float(result.delta_puntuacion.abs().max()),
   'top5_original':result.loc[result.ivp_original.notna()].sort_values(['ivp_original','municipio'],ascending=[False,True]).head(5).index.tolist(),
   'top5_propuesto':result.loc[result.ivp_propuesto.notna()].sort_values(['ivp_propuesto','municipio'],ascending=[False,True]).head(5).index.tolist()}
  config['resumen'][profile]=summaries
  comparisons.append(result.reset_index())
  for method in METHODS:
   alternate=pd.DataFrame({key:transform_normalization(raw[key],fit_normalization(raw[key],'menor_mejor',method)) for key in raw})
   for weight in (0,.5,.75,.9,1):
    composite=calculate_composite_dimension(alternate,{'coste':weight,'relacion':1-weight})
    sd=dims[list(DIMENSIONS)].copy();sd.vivienda=composite.puntuacion
    si=calculate_index(sd,{d:.25 for d in DIMENSIONS})
    sensitivity=pd.DataFrame({'codigo_ine':eligible.index,'perfil':profile,'normalizacion_vivienda':method,'peso_coste':weight,'peso_relacion':1-weight,'ivp':si.valor_indice.values,'puesto':si.posicion.values})
    sensitivities.append(sensitivity)
 pd.concat(inputs,ignore_index=True).to_csv(OUT/'indicadores_propuesta.csv',index=False,encoding='utf-8-sig')
 pd.concat(comparisons,ignore_index=True).to_csv(OUT/'comparacion_ivp.csv',index=False,encoding='utf-8-sig')
 sensitivity=pd.concat(sensitivities,ignore_index=True);sensitivity.to_csv(OUT/'sensibilidad_escenarios.csv',index=False,encoding='utf-8-sig')
 ranges=sensitivity.groupby(['codigo_ine','perfil']).agg(puesto_min=('puesto','min'),puesto_max=('puesto','max'),escenarios_con_indice=('ivp','count')).reset_index()
 ranges['escenarios_totales']=15;ranges.to_csv(OUT/'sensibilidad_municipios.csv',index=False,encoding='utf-8-sig')
 config['cobertura_contexto']={c:int(extras[c].notna().sum()) for c in ['renta_neta_hogar_eur_anio','viviendas_principales_pct','viviendas_no_principales_pct','viviendas_anteriores_1981_pct','viviendas_vacias_consumo_num','ipva_variacion_anual_pct','valor_tasado_eur_m2']}
 details,moderate,review=controlled_diagnostics(data,pd.concat(comparisons,ignore_index=True),pd.concat(inputs,ignore_index=True),sensitivity)
 details.to_csv(OUT/'descomposicion_controlada.csv',index=False,encoding='utf-8-sig')
 moderate.to_csv(OUT/'sensibilidad_pesos_controlada.csv',index=False,encoding='utf-8-sig')
 dump(OUT/'revision_metodologica.json',review)
 config['revision']=review
 dump(OUT/'metodologia.json',config)
 records={}
 for file in OUT.glob('*.csv'):records[file.name]={'sha256':sha(file.read_bytes()),'bytes':file.stat().st_size}
 selection=[
  {'indicador':'Coste del alquiler por m2','papel':'propuesta','peso_interno':.75,'anio':year,'unidad':'EUR/m2/mes','fuente':'MIVAU SERPAVI','criterio':'menor_mejor','limitacion':'Alquiler declarado; no oferta actual'},
  {'indicador':'Relación alquiler-ingresos agregada','papel':'propuesta','peso_interno':.25,'anio':year,'unidad':'porcentaje de cociente agregado','fuente':'MIVAU SERPAVI + INE ADRH','criterio':'menor_mejor','limitacion':'No mide esfuerzo real; hogares de distintas poblaciones estadísticas; solapamiento con coste'},
  {'indicador':'Valor tasado','papel':'contexto','peso_interno':0,'anio':period//10,'unidad':'EUR/m2','fuente':'MIVAU/CM','criterio':'sin puntuación','limitacion':'Solo cuatro municipios elegibles; no precio de compraventa'},
  {'indicador':'Evolución IPVA','papel':'contexto','peso_interno':0,'anio':ipyear,'unidad':'variación anual porcentual','fuente':'INE/CM','criterio':'sin puntuación','limitacion':'27 municipios; resto agregado no asignable'},
  {'indicador':'Vivienda principal/no principal','papel':'contexto','peso_interno':0,'anio':2021,'unidad':'porcentaje','fuente':'INE Censo/CM','criterio':'sin dirección objetiva','limitacion':'No principal no equivale a secundaria o vacía ni disponible'},
  {'indicador':'Viviendas vacías según consumo eléctrico','papel':'contexto','peso_interno':0,'anio':2021,'unidad':'Número','fuente':'INE Censo/CM','criterio':'sin puntuación','limitacion':'Recuento provisional; no viviendas disponibles ni calidad residencial'},
  {'indicador':'Antigüedad anterior a 1981','papel':'contexto','peso_interno':0,'anio':2021,'unidad':'porcentaje','fuente':'INE Censo/CM','criterio':'sin dirección objetiva','limitacion':'No acredita conservación o rehabilitación; sin cálculo con celdas ausentes'}]
 pd.DataFrame(selection).to_csv(OUT/'seleccion_indicadores.csv',index=False,encoding='utf-8-sig')
 records['seleccion_indicadores.csv']={'sha256':sha((OUT/'seleccion_indicadores.csv').read_bytes()),'bytes':(OUT/'seleccion_indicadores.csv').stat().st_size}
 records['revision_metodologica.json']={'sha256':sha((OUT/'revision_metodologica.json').read_bytes()),'bytes':(OUT/'revision_metodologica.json').stat().st_size}
 records['metodologia.json']={'sha256':sha((OUT/'metodologia.json').read_bytes()),'bytes':(OUT/'metodologia.json').stat().st_size}
 dump(OUT/'manifest.json',{'version':VERSION,'archivos':records,'fuentes_sha256':sha((RAW/'sources.json').read_bytes()),'original_sha256':baseline_records})
 print(json.dumps({'resumen':config['resumen'],'cobertura_contexto':config['cobertura_contexto']},ensure_ascii=False,indent=2))

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--download',action='store_true');parser.add_argument('--download-source',action='append',choices=list(SOURCES));args=parser.parse_args()
 if args.download:download()
 elif args.download_source:download(args.download_source)
 prepare()
