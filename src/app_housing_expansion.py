"""Verified, offline housing context and a separate IVP proposal for review."""
from pathlib import Path
import hashlib,json
import numpy as np
import pandas as pd
from src.integration_common import IntegrationError


def digest(path):
 return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def expansion_signature(root):
 base=Path(root)/'data/processed/housing_expansion';manifest=base/'manifest.json'
 if not manifest.exists():return None
 data=json.loads(manifest.read_text())
 paths=[manifest,*[base/name for name in data['archivos']],Path(root)/'data/reference/housing_expansion/sources.json']
 return tuple((str(p),p.stat().st_mtime_ns,p.stat().st_size) for p in paths)


def load_expansion(root):
 root=Path(root);base=root/'data/processed/housing_expansion'
 manifest=json.loads((base/'manifest.json').read_text())
 for name,entry in manifest['archivos'].items():
  if Path(name).name!=name:raise IntegrationError('Nombre de salida no válido')
  path=base/name
  if path.stat().st_size!=entry['bytes'] or digest(path)!=entry['sha256']:
   raise IntegrationError('Ampliación alterada: '+name)
 sources=root/'data/reference/housing_expansion/sources.json'
 if digest(sources)!=manifest['fuentes_sha256']:raise IntegrationError('Fuentes de vivienda alteradas')
 for name,hashcode in manifest['original_sha256'].items():
  if digest(root/'data/processed/ivp'/name)!=hashcode:raise IntegrationError('La referencia IVP original ha cambiado')
 method=json.loads((base/'metodologia.json').read_text())
 if method['estado']!='propuesta_no_activada':raise IntegrationError('Estado de publicación no previsto')
 tables={name:pd.read_csv(base/filename,dtype={'codigo_ine':str,'perfil':str}) for name,filename in {
  'contexto':'contexto_municipal.csv','indicadores':'indicadores_propuesta.csv',
  'comparacion':'comparacion_ivp.csv','sensibilidad':'sensibilidad_municipios.csv',
  'descomposicion':'descomposicion_controlada.csv'}.items()}
 context=tables['contexto']
 if len(context)!=155 or context.codigo_ine.duplicated().any() or not context.codigo_ine.str.fullmatch(r'28\d{3}').all():
  raise IntegrationError('Universo municipal de la ampliación no válido')
 for name in ('indicadores','comparacion','sensibilidad','descomposicion'):
  table=tables[name]
  if table.duplicated(['codigo_ine','perfil']).any() or set(table.perfil)!={'VC','VU'}:raise IntegrationError('Claves de vivienda inválidas')
  for _,part in table.groupby('perfil'):
   if set(part.codigo_ine)!=set(context.codigo_ine):raise IntegrationError('Faltan municipios en la ampliación')
 for column in ['ivp_original','ivp_propuesto','vivienda_propuesta','puntuacion_coste','puntuacion_relacion']:
  values=tables['comparacion'][column].dropna()
  if not np.isfinite(values).all() or not values.between(0,100).all():raise IntegrationError('Puntuación de vivienda no válida')
 tables['metodologia']=method;tables['fuentes']=json.loads(sources.read_text())
 return tables


def build_proposal_frame(data, expansion, profile, weights=None):
 """Offline preview using the existing explorer/filter contracts.

 This never changes the application's primary edition or any caller table.
 """
 from src.app_explorer import build_explorer_frame
 from src.ivp_calculation import DIMENSIONS, calculate_index
 frame=build_explorer_frame(data,profile,weights).set_index('codigo_ine')
 rows=expansion['comparacion'].loc[expansion['comparacion'].perfil.eq(profile)].set_index('codigo_ine').reindex(frame.index)
 inputs=expansion['indicadores'].loc[expansion['indicadores'].perfil.eq(profile)].set_index('codigo_ine').reindex(frame.index)
 frame['puntuacion_vivienda']=rows.vivienda_propuesta
 frame['alquiler_m2_mes']=inputs.alquiler_m2_mes
 frame['alquiler_anio']=expansion['metodologia']['anio_vivienda_propuesta']
 frame['ivp_general']=rows.ivp_propuesto;frame['posicion_general']=rows.puesto_propuesto.astype('Int64')
 frame['estado_general']=rows.estado_propuesta
 scores=frame[[f'puntuacion_{d}' for d in DIMENSIONS]].rename(columns={f'puntuacion_{d}':d for d in DIMENSIONS})
 custom=calculate_index(scores,frame.attrs['pesos_normalizados'],blocked_dimensions=frame.attrs['bloqueos_dimensiones'])
 frame['ivp_personalizado']=custom.valor_indice;frame['posicion_personalizada']=custom.posicion
 frame['estado_personalizado']=custom.estado_indice
 frame['dimensiones_faltantes_json']=custom.dimensiones_faltantes_json
 frame['dimensiones_bloqueadas_json']=custom.dimensiones_bloqueadas_json
 frame['peso_disponible']=custom.peso_disponible
 frame.attrs['metodologia_version']=expansion['metodologia']['version']
 frame.attrs['estado_version']='propuesta_no_activada'
 return frame.reset_index()
