"""Reproducible quality checks. An anomaly is never silently repaired or imputed."""
from pathlib import Path
import json,hashlib
import numpy as np
import pandas as pd
from src.ine_municipal import load_context
from src.app_services import load_service_tables
from src.app_data import _verify_snapshot


class Audit:
    def __init__(self):
        self.checks=[];self.anomalies=[];self.inventory=[]

    def check(self,name,valid,records,detail=''):
        ok=bool(np.asarray(valid).all())
        self.checks.append({'regla':name,'resultado':'correcto' if ok else 'error_confirmado',
                            'registros':int(records),'detalle':detail})
        return ok

    def flag(self,code,rule,value,*,dataset,year='',profile='',status='requiere_contraste',detail=''):
        self.anomalies.append({'codigo_ine':str(code),'regla':rule,'valor':value,'dataset':dataset,
                                'anio':year,'perfil':profile,'estado':status,'detalle':detail})

    def read(self,root,name,**kwargs):
        path=root/name
        df=pd.read_csv(path,dtype={'codigo_ine':str,'codigo_ine_origen':str,'codigo_ine_destino':str},**kwargs)
        self.inventory.append({'dataset':name,'filas':len(df),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        return df


def extreme_mask(values,multiplier=1.5):
    values=pd.to_numeric(values,errors='coerce')
    q1,q3=values.quantile([.25,.75]);iqr=q3-q1
    if not np.isfinite(iqr) or iqr==0:return pd.Series(False,index=values.index)
    return values.lt(q1-multiplier*iqr)|values.gt(q3+multiplier*iqr)


def audit(root):
    root=Path(root);a=Audit()
    _,verified=_verify_snapshot(root)
    a.check('Integridad de archivos y cadena de procedencia',True,verified['archivos_verificados'])
    ine,manifest,sources=load_context(root)
    master=a.read(root,'data/processed/municipios/maestro_municipios.csv')
    codes=set(master.codigo_ine)
    a.check('Códigos INE oficiales únicos',~master.codigo_ine.duplicated() & master.codigo_ine.isin(ine.index),len(master))
    a.check('Padrón definitivo, nombres y elegibilidad contrastados',ine.coincide_poblacion & ine.coincide_nombre & ine.elegible_ine.eq(ine.elegible_hasta_50000),len(ine))
    history=a.read(root,'data/processed/integracion/evolucion_poblacion.csv')
    direct=a.read(root,'data/processed/ine_municipal/padron_historico_ine.csv')
    joined=history.merge(direct,on=['codigo_ine','anio'],how='inner',validate='one_to_one')
    a.check('Evolución: coincidencia con la serie oficial INE',np.isclose(joined.poblacion,joined.poblacion_ine,equal_nan=True),len(joined))
    a.check('Población no negativa y claves únicas',history.poblacion.dropna().ge(0).all() and not history.duplicated(['codigo_ine','anio']).any(),len(history))
    for _,r in history.loc[history.variacion_porcentaje.abs().gt(20)].iterrows():
        a.flag(r.codigo_ine,'Variación poblacional superior al 20 %',r.variacion_porcentaje,dataset='evolucion_poblacion',year=int(r.anio),status='coincide_ine' if ((joined.codigo_ine==r.codigo_ine)&(joined.anio==r.anio)).any() else 'fuente_cm_sin_contraste_ine',detail='Salto a revisar, especialmente en poblaciones pequeñas; no es un error por sí solo.')
    housing=a.read(root,'data/processed/integracion/vivienda_historico.csv')
    a.check('Vivienda: claves, ámbito y tipologías',not housing.duplicated(['codigo_ine','anio','tipo']).any() and housing.codigo_ine.isin(codes).all() and housing.tipo.isin(['VC','VU']).all(),len(housing))
    proof=json.loads((root/'data/processed/data_quality/housing_source_verification.json').read_text())
    a.check('Vivienda: 50.120 celdas y 179 identidades contrastadas con Excel',not proof['errors'] and proof['records']==len(housing) and proof['derived_sha256']==hashlib.sha256((root/'data/processed/integracion/vivienda_historico.csv').read_bytes()).hexdigest(),proof['numeric_cells_compared'])
    numeric=['testigos']+[c for c in housing if c.startswith(('alquiler_','superficie_'))]
    a.check('Vivienda: importes y superficies positivos; testigos no negativos',(housing[numeric[1:]].gt(0)|housing[numeric[1:]].isna()).all().all() and (housing.testigos.ge(0)|housing.testigos.isna()).all(),len(housing))
    a.check('Vivienda: unidades conservadas',housing.unidad_alquiler_mes.eq('EUR/mes') & housing.unidad_alquiler_m2_mes.eq('EUR/m2/mes') & housing.unidad_superficie.eq('m2'),len(housing))
    for stem in ['alquiler_m2_mes','alquiler_mes','superficie_m2']:
        vals=housing[[stem+'_p25',stem+'_mediana',stem+'_p75']].dropna()
        a.check('Vivienda: percentiles ordenados '+stem,vals.iloc[:,0].le(vals.iloc[:,1]) & vals.iloc[:,1].le(vals.iloc[:,2]),len(vals))
    a.check('Vivienda: ausencia publicada conservada; mínimo de 10 testigos',housing.alquiler_m2_mes_mediana.isna()|housing.testigos.ge(10),len(housing))
    for (year,profile),part in housing.groupby(['anio','tipo']):
        for metric in ['alquiler_m2_mes_mediana','alquiler_mes_mediana']:
            for _,r in part.loc[extreme_mask(part[metric])].iterrows():
                a.flag(r.codigo_ine,'Atípico IQR: '+metric,float(r[metric]),dataset='vivienda_historico',year=int(year),profile=profile,status='coincide_excel_oficial',detail='Celda '+r['columna_fuente_'+metric]+str(int(r.fila_fuente_excel))+' · Municipio y valores contrastados con el Excel; no se sustituye por otra cifra.')
    for (code,profile),part in housing.groupby(['codigo_ine','tipo']):
        part=part.sort_values('anio');jump=part.alquiler_m2_mes_mediana.pct_change(fill_method=None)
        for _,r in part.loc[jump.abs().gt(.4)].iterrows():
            a.flag(code,'Cambio anual de alquiler superior al 40 %',float(jump.loc[r.name]*100),dataset='vivienda_historico',year=int(r.anio),profile=profile,status='coincide_excel_oficial',detail='Comparación de medianas, no índice de precios de una vivienda constante; puede variar la composición.')
    for _,r in housing.loc[housing.anio.eq(2024)&housing.testigos.lt(25)&housing.alquiler_m2_mes_mediana.notna()].iterrows():
        a.flag(r.codigo_ine,'Pocas observaciones publicadas',int(r.testigos),dataset='vivienda_historico',year=2024,profile=r.tipo,status='limitacion_muestral',detail='El mínimo oficial de publicación es 10. Umbral de aviso de HabitaMadrid: menos de 25; no invalida automáticamente el valor.')
    context=a.read(root,'data/processed/indicadores/demografia_componentes.csv')
    a.check('Demografía: crecimiento con denominador correcto',np.isclose(context.crecimiento_2020_2025_pct,100*(context.poblacion_2025/context.poblacion_2020-1),equal_nan=True),len(context))
    a.check('Demografía: saldo por mil, sin confundirlo con porcentaje',np.isclose(context.saldo_migratorio_externo_cm_2024_por1000,1000*context.saldo_migratorio_externo_cm_2024/context.poblacion_2024,equal_nan=True),len(context))
    balance=[]
    for value in context.componentes_migratorios_json:
        components=json.loads(value)
        balance.append(sum(c['signo']*c['valor'] for c in components.values()) if all(c['valor'] is not None for c in components.values()) else np.nan)
    a.check('Demografía: suma de los cuatro flujos migratorios',np.isclose(balance,context.saldo_migratorio_externo_cm_2024,equal_nan=True),len(context))
    extended=a.read(root,'data/processed/housing_expansion/contexto_municipal.csv')
    for col in ['viviendas_principales_pct','viviendas_no_principales_pct','viviendas_anteriores_1981_pct']:
        a.check('Contexto residencial: límites de '+col,extended[col].dropna().between(0,100),len(extended))
    direct_housing=ine.loc[extended.codigo_ine]
    a.check('Vivienda principal: contraste INE directo con redondeo de la CM',np.isclose(extended.viviendas_principales_pct.to_numpy(),100*direct_housing.principales_2021/direct_housing.viviendas_2021,atol=.00501,equal_nan=True),len(extended))
    services=load_service_tables(root)
    from shapely.geometry import shape,Point
    from shapely.ops import transform,unary_union
    from pyproj import Transformer
    projection=Transformer.from_crs(4326,25830,always_xy=True).transform
    boundaries=json.loads((root/'data/processed/geografia/limites_municipales.geojson').read_text())
    polygons={f['properties']['codigo_ine']:transform(projection,shape(f['geometry'])) for f in boundaries['features']}
    shared=json.loads((root/'data/processed/geografia/jurisdicciones_compartidas.geojson').read_text())
    for feature in shared['features']:
        for code in feature['properties']['codigos_ine_participantes'].split(';'):
            polygons[code]=unary_union([polygons[code],transform(projection,shape(feature['geometry']))])
    a.check('Geografía: geometrías válidas y 179 códigos oficiales',set(polygons)==codes and all(poly.is_valid for poly in polygons.values()),len(polygons))
    # The loader also verifies source IDs, operational status, joins and stop/route identities.
    for kind,table in services.items():
        if not isinstance(table,pd.DataFrame) or 'servicio_id' not in table:continue
        a.check(kind+': identificadores únicos y municipio conocido',not table.servicio_id.duplicated().any() and table.codigo_ine.isin(codes).all(),len(table))
        located=table.dropna(subset=['longitud','latitud'])
        a.check(kind+': coordenadas en el entorno de Madrid',located.longitud.between(-4.7,-3)&located.latitud.between(39.8,41.3),len(located),'Envolvente de control; no certifica el edificio ni apertura actual.')
        if kind=='educacion':
            for _,r in table.loc[table.longitud.isna()].iterrows():a.flag(r.codigo_ine,'Centro sin coordenadas operativas',r.servicio_id,dataset='educacion',status='limitacion_fuente',detail='Se conserva como registro local; no se usa para distancia.')
        for _,r in located.iterrows():
            offset=polygons[r.codigo_ine].distance(Point(r.x_25830,r.y_25830))
            if offset>30:
                a.flag(r.codigo_ine,'Coordenadas fuera del municipio declarado',round(offset,2),dataset=kind,detail=r.servicio_id+' · metros hasta el límite, tolerancia de control 30 m. Requiere contrastar la coordenada y el límite oficial; no se reasigna automáticamente.')
        dup=located.duplicated(['codigo_ine','nombre','longitud','latitud'],keep=False)
        for _,r in located.loc[dup].iterrows():a.flag(r.codigo_ine,'Coincidencia de nombre y coordenadas',r.servicio_id,dataset=kind,detail='No se elimina sin contrastar identidad, tipología y autorización.')
    # Recompute all published straight-line distances independently of the ranking logic.
    geo=a.read(root,'data/processed/geografia/catalogo_servicios_geograficos.csv').set_index('servicio_id')
    origins=a.read(root,'data/processed/geografia/origenes_municipales.csv').set_index('codigo_ine')
    distances=a.read(root,'data/processed/geografia/proximidad_servicios.csv')
    destination=geo.reindex(distances.servicio_id);origin=origins.reindex(distances.codigo_ine_origen)
    calc=np.hypot(destination.x_25830.to_numpy()-origin.x_etrs89_utm30.to_numpy(),destination.y_25830.to_numpy()-origin.y_etrs89_utm30.to_numpy())
    a.check('Distancias: metros en EPSG:25830 y destino por ID',np.isclose(calc,distances.distancia_m,rtol=1e-10,atol=1e-6),len(distances))
    a.check('Distancias: código de destino coincide con servicio',destination.codigo_ine.to_numpy()==distances.codigo_ine_destino.to_numpy(),len(distances))
    observations=a.read(root,'data/processed/ivp/observaciones_seleccionadas.csv')
    dimensions=a.read(root,'data/processed/ivp/dimensiones_ivp.csv')
    general=a.read(root,'data/processed/ivp/ivp_general.csv')
    a.check('IVP: observaciones únicas y no negativas',not observations.duplicated(['codigo_ine','indicador_id']).any() and observations.valor.dropna().ge(0).all(),len(observations))
    refs=json.loads((root/'data/processed/ivp/normalizacion.json').read_text())
    field_map={'sanidad':'distancia_atencion_primaria_directorio_m','educacion':'distancia_centro_educativo_general_m','transporte':'distancia_parada_bus_parcial_m'}
    obs=observations.pivot(index='codigo_ine',columns='indicador_id',values='valor')
    # Independently verify that the IVP observes the original rental values, not scores or unit conversions.
    for profile in ['VC','VU']:
        source=housing.loc[housing.anio.eq(2024)&housing.tipo.eq(profile)].set_index('codigo_ine')
        key='vivienda_alquiler_'+profile.lower()+'_m2_mediana'
        a.check('IVP: alquiler original, misma tipología y unidad '+profile,np.isclose(obs[key],source.alquiler_m2_mes_mediana.reindex(obs.index),equal_nan=True),len(obs))
    from src.indicator_services import GENERAL_EDUCATION_TYPES
    for dimension,key in field_map.items():
        kind={'sanidad':'sanidad','educacion':'educacion','transporte':'transporte'}[dimension]
        destinations=services[kind].dropna(subset=['x_25830','y_25830'])
        if kind=='educacion':destinations=destinations.loc[destinations.modalidad.isin(GENERAL_EDUCATION_TYPES)]
        origin=origins.reindex(obs.index)
        matrix=np.hypot(origin.x_etrs89_utm30.to_numpy()[:,None]-destinations.x_25830.to_numpy()[None,:],origin.y_etrs89_utm30.to_numpy()[:,None]-destinations.y_25830.to_numpy()[None,:])
        a.check('IVP: distancia mínima recalculada '+dimension,np.isclose(matrix.min(axis=1),obs[key],atol=1e-6),len(obs))
        lookup=destinations.set_index('servicio_id')
        selected=observations.loc[observations.indicador_id.eq(key)]
        matched=[]
        for _,record in selected.iterrows():
            detail=json.loads(record.detalle_json);sid=detail['servicio_id']
            matched.append(sid in lookup.index and lookup.loc[sid,'codigo_ine']==str(detail['codigo_ine_destino']))
        a.check('IVP: identidad y municipio del destino '+dimension,matched,len(selected))
    normalized={}
    for method,indicators in refs.items():
        normalized[method]={}
        for key,reference in indicators.items():
            vals=obs[key]
            valid=vals.dropna();lo,hi=valid.min(),valid.max()
            a.check('IVP: anclas y cobertura '+method+'/'+key,np.isclose(lo,reference['minimo_observado']) and np.isclose(hi,reference['maximo_observado']) and len(valid)==reference['n_observaciones'],len(valid))
            if method=='winsor_p05_p95':lo,hi=valid.quantile([.05,.95])
            if method=='percentil':score=100*(1-(vals.rank(method='average')-1)/(len(valid)-1))
            else:score=100*(1-(vals.clip(lo,hi)-lo)/(hi-lo)) if hi!=lo else vals.where(vals.isna(),50)
            normalized[method][key]=score
    for (method,profile),part in dimensions.groupby(['metodo','perfil_vivienda']):
        for dim,key in {**field_map,'vivienda':'vivienda_alquiler_'+profile.lower()+'_m2_mediana'}.items():
            expected=normalized[method][key].reindex(part.codigo_ine)
            a.check('IVP: transformación '+method+'/'+profile+'/'+dim,np.isclose(expected,part[dim],atol=1e-9,equal_nan=True),len(part))
    for profile,part in general.groupby('perfil_vivienda'):
        scores=part[['puntuacion_'+dim for dim in ['sanidad','educacion','transporte','vivienda']]]
        expected=scores.sum(axis=1,min_count=4)/4
        ranks=expected.rank(method='min',ascending=False)
        a.check('IVP general: pesos iguales y ausencias sin imputación '+profile,np.isclose(expected,part.valor_indice,equal_nan=True),len(part))
        a.check('Ranking general y Top 5 '+profile,np.isclose(ranks,part.posicion,equal_nan=True),len(part))
        for _,r in part.loc[expected.isna()&part.valor_indice.notna()].iterrows():a.flag(r.codigo_ine,'IVP calculado con ausencia',r.valor_indice,dataset='ivp_general',status='error_confirmado')
    transports=json.loads((root/'data/reference/municipal_context/transport_lines.json').read_text())
    pages=list(transports['municipalities'].values());lines=[x for page in pages for x in page.get('lines',[])]
    a.check('CRTM: cobertura de consulta municipal',set(transports['municipalities'])==codes or set(transports['municipalities'])==set(master.loc[master.elegible_hasta_50000,'codigo_ine']),len(pages))
    a.check('CRTM: líneas con enlace y verificación de etiqueta',all(x.get('verified') and x.get('label_verified') and x.get('http_status')==200 for x in lines),len(lines))
    for page in pages:
        for line in page.get('lines',[]):
            if not line.get('schedules'):a.flag(page['codigo_ine'],'Línea sin PDF horario publicado',line['line'],dataset='transport_lines',status='limitacion_fuente',detail='Se mantiene el enlace a la ficha oficial; no se inventa horario.')
    primary=json.loads((root/'data/processed/data_quality/primary_health_verification.json').read_text())
    a.check('Atención primaria: contraste actual de identidades, nombres y puntos del directorio',not primary['cambios'],primary['registros_directorio'])
    sector=json.loads((root/'data/processed/data_quality/sector_source_verification.json').read_text())
    for key in ['centros_educativos','migraciones_por_procedencia_destino_y_sexo']:
        a.check('Fuente oficial descargada de nuevo: '+key,sector[key]['unchanged'],1,'Coincidencia completa de bytes con la edición procesada; fecha de consulta separada de la fecha del dato.')
    changes=a.read(root,'data/processed/data_quality/health_register_changes.csv')
    for _,change in changes.iterrows():
        a.flag('', 'Cambio en el registro de autorizaciones sanitarias',change.registro_id,dataset='registro_autorizaciones_sanitarias',status='actualizacion_sin_uso_ivp',detail=change.municipio+' · '+change.cambio+' · No implica un cierre demostrado. La interfaz sanitaria y el IVP utilizan el directorio contrastado, no este registro histórico de autorizaciones.')
    ivp_hashes={name:hashlib.sha256((root/'data/processed/ivp'/name).read_bytes()).hexdigest() for name in ['ivp_general.csv','dimensiones_ivp.csv','normalizacion.json','observaciones_seleccionadas.csv','configuracion_ivp.json']}
    return {'schema':1,'municipios':len(master),'elegibles':int(master.elegible_hasta_50000.sum()),'checks':a.checks,'inventory':a.inventory,'anomalies':a.anomalies,'errores_confirmados':sum(x['resultado']=='error_confirmado' for x in a.checks),'ivp_sha256':ivp_hashes,'cambios_ivp':0,'nota':'Los extremos estadísticos se conservan. Cero errores en estas reglas no certifica apertura actual de centros ni exhaustividad de la oferta.'}
