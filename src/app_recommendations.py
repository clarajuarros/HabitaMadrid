"""Explain saved IVP scores with traceable, deterministic comparisons."""
import math
import json
import pandas as pd
from src.app_presenter import DIMENSION_LABELS,number,distance

DIMENSIONS=('sanidad','educacion','transporte','vivienda')
RAW={'sanidad':'distancia_sanidad_m','educacion':'distancia_educacion_m','transporte':'distancia_transporte_m','vivienda':'alquiler_m2_mes'}

def finite(value):
    try:return math.isfinite(float(value))
    except (ValueError,TypeError):return False


def recommend(filtered,mode='general',limit=3):
    column='ivp_personalizado' if mode=='personalizado' else 'ivp_general'
    return filtered.loc[filtered[column].notna()].sort_values([column,'municipio','codigo_ine'],ascending=[False,True,True],kind='stable').head(limit).copy()


def describe(row,frame,mode='general'):
    weights=frame.attrs.get('pesos_normalizados',{}) if mode=='personalizado' else {d:.25 for d in DIMENSIONS}
    active=[d for d in DIMENSIONS if weights.get(d,0)>0]
    scheduled_transport=frame.attrs.get('transporte_oferta_programada',False)
    transport={}
    if scheduled_transport:
        transport={key:(float(row.get(key)) if finite(row.get(key)) else None) for key in ('expediciones_dia','destinos_directos_dia','expediciones_laborable','expediciones_sabado','expediciones_domingo')}
        for key in ('inicio_semana','fin_semana','estado_cobertura','nota_cobertura'):
            value=row.get(key)
            transport[key]='' if value is None or pd.isna(value) else str(value)
        period='semana del '+transport['inicio_semana']+' al '+transport['fin_semana']
        coverage=' Cobertura declarada: '+transport['estado_cobertura'].replace('_',' ')+'.'
        if transport['nota_cobertura']:coverage+=' '+transport['nota_cobertura']
    positives=[];consider=[];available=[]
    descriptions={
        'sanidad':lambda v:'Atención primaria a '+distance(v)+' en línea recta desde el núcleo principal.',
        'educacion':lambda v:'Centro educativo seleccionado a '+distance(v)+' en línea recta.',
        'transporte':lambda v:'Parada interurbana a '+distance(v)+' en línea recta; catálogo parcial.',
        'vivienda':lambda v:'Alquiler de referencia: '+number(v,2)+' €/m²/mes (SERPAVI 2024).',
    }
    for d in DIMENSIONS:
        score=row.get('puntuacion_'+d)
        if finite(score):available.append(d)
        elif d in active:
            text='Falta información de '+DIMENSION_LABELS[d].lower()+' para calcular este escenario.'
            if d=='transporte' and scheduled_transport:text+=coverage
            consider.append(text)
    for d in sorted(active,key=lambda d:(-weights[d],-float(row.get('puntuacion_'+d)) if finite(row.get('puntuacion_'+d)) else 0)):
        if d=='transporte' and scheduled_transport:
            score=row.get('puntuacion_transporte')
            if not finite(score):continue
            trips,destinations=transport['expediciones_dia'],transport['destinos_directos_dia']
            if trips is None or destinations is None:
                consider.append('Consulta el detalle de oferta programada y cobertura de transporte antes de interpretar esta puntuación.')
                continue
            text=(number(trips,1)+' expediciones intermunicipales/día y '+number(destinations,1)+' destinos municipales directos/día de media ('+period+'). Oferta programada, sin transbordos ni tiempo real.'+coverage)
            median=frame['puntuacion_transporte'].median()
            if finite(median) and score>=median:positives.append(text+' Puntuación igual o superior a la mediana municipal de esta edición.')
            else:consider.append(text+' Puntuación inferior a la mediana municipal de esta edición.')
            continue
        score=row.get('puntuacion_'+d);value=row.get(RAW[d])
        if not finite(score) or not finite(value):continue
        median=frame['puntuacion_'+d].median()
        raw_median=frame[RAW[d]].median()
        text=descriptions[d](value)
        if finite(median) and score>=median:
            positives.append(text+' Puntuación igual o superior a la mediana municipal de esta edición.')
        else:
            detail=(' La mediana de referencia es '+(number(raw_median,2)+' €/m²/mes.' if d=='vivienda' else distance(raw_median)+'.')) if finite(raw_median) else ''
            consider.append(text+detail)
    if not positives:positives=['Consulta los indicadores publicados para valorar si se ajusta a tus necesidades.']
    if len(active)<4:consider.append('Escenario parcial: '+str(len(active))+' dimensiones tienen peso positivo.')
    if not consider:
        consider=['Los horarios programados pueden cambiar. Las distancias a sanidad y educación no acreditan plazas o capacidad.' if scheduled_transport else 'La proximidad no acredita horarios, plazas disponibles ni capacidad de los servicios.']
    result={'positives':positives[:3],'consider':consider[:3],'available':len(available),'missing':[DIMENSION_LABELS[d] for d in DIMENSIONS if d not in available],'active':len(active),'active_available':len([d for d in active if d in available])}
    if scheduled_transport:result.update(transport_offer_programmed=True,transport_summary=transport)
    return result


def observation_details(data,code,profile):
    config=data['configuracion'];identifiers={**config['indicadores_comunes'],'vivienda':config['perfiles'][profile]['indicador']};result=[]
    for d in DIMENSIONS:
        rows=data['observaciones'].loc[(data['observaciones'].codigo_ine==code)&(data['observaciones'].indicador_id==identifiers[d])]
        row=rows.iloc[0] if len(rows)==1 else {}
        try:sources=json.loads(row.get('fuentes_json','[]'))
        except (ValueError,TypeError):sources=[]
        urls=data['fuentes'].set_index('fuente')['url'].to_dict()
        labels={'cm_directorio_atencion_primaria':'Directorio de atención primaria · Comunidad de Madrid','crtm_interurbanos_gtfs':'GTFS interurbano · CRTM','MIVAU_SERPAVI_2011_2024':'SERPAVI · Ministerio de Vivienda','cm_centros_educativos':'Directorio de centros educativos · Comunidad de Madrid'}
        source_text=', '.join(labels.get(x,x) for x in sources) if isinstance(sources,list) else 'Consultar fuentes'
        source_url=next((urls[x] for x in sources if x in urls),'') if isinstance(sources,list) else ''
        reference=str(row.get('periodo') or row.get('anio') or 'No publicada')
        if d=='transporte' and identifiers[d]=='oferta_conectividad_programada':
            source_text='Oferta y conectividad programada · '+(source_text or 'Horarios GTFS documentados')
            offer=data.get('transporte_oferta',pd.DataFrame())
            if 'codigo_ine' in offer:
                selected=offer.loc[offer.codigo_ine.eq(code)]
                if len(selected)==1:
                    record=selected.iloc[0]
                    reference='Semana '+str(record.get('inicio_semana',''))+' a '+str(record.get('fin_semana',''))
                    source_text+=' · Cobertura: '+str(record.get('estado_cobertura','No publicada')).replace('_',' ')
        result.append({'Dimensión':DIMENSION_LABELS[d],'Disponible':'Sí' if finite(row.get('valor')) else 'Sin datos','Referencia':reference,'Fuente':source_text or 'Consultar ficha de fuentes','Enlace':source_url})
    return result
