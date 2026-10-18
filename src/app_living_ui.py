"""Guided discovery, transparent explanations, complementary data and sharing."""
from datetime import date,timedelta
import json
from pathlib import Path
from urllib.parse import urlsplit
import pandas as pd
import streamlit as st
from src.app_identity import municipality_photo
from src.app_presenter import esc,number,PROFILE_LABELS,DIMENSION_LABELS
from src.app_recommendations import recommend,describe,observation_details,finite
from src.app_living_data import load_living_data,timetable,gtfs_time
from src.app_share import make_plan,public_base,share_url
from src.app_personal_report import build_personal_pdf
from src.app_navigation import RESULTS, FORM, request_navigation, target_attributes
from src.app_state import clear_assistant

POPULATIONS={'Cualquier tamaño':(0,50000),'Menos de 5.000 habitantes':(0,4999),'De 5.000 a 20.000 habitantes':(5000,20000),'De 20.001 a 50.000 habitantes':(20001,50000)}
DIMS=('sanidad','educacion','transporte','vivienda')


def submit_assistant():
    """Form callback runs before the main selector and map are recreated."""
    state=st.session_state
    priorities=state.get('assistant_priorities',[])
    if state.get('assistant_profile') not in ('VU','VC'):
        state['_assistant_error']='Elige el tipo de vivienda que buscas.'
        return
    if not priorities:
        state['_assistant_error']='Selecciona al menos una prioridad para continuar.'
        return
    state.pop('_assistant_error',None)
    state['perfil_vivienda']=state['assistant_profile'];state['modo_indice']='personalizado'
    for d in DIMS:state['peso_'+d]=100 if d in priorities else 25
    state['filtro_poblacion']=POPULATIONS[state['assistant_population']]
    state['filtro_alquiler_max']=state.get('assistant_budget')
    for d in ('sanidad','educacion','transporte'):state['filtro_'+d+'_km']=None
    state['filtro_nombre']='';state['filtro_solo_indice']=True
    state['_assistant_results']=True;state['_assistant_open']=True
    request_navigation(state,RESULTS)


def render_assistant_entry():
    attributes=target_attributes(st.session_state,FORM)
    st.html('<section '+attributes+' class="discovery-heading" aria-label="Encuentra tu municipio ideal"><h2>Encuentra tu municipio ideal</h2><p>Elige qué te importa más. Compararemos los datos disponibles con tus preferencias.</p></section>')
    with st.container(key='discovery_entry'):
        st.button('Ajustar mis respuestas' if st.session_state.get('_assistant_results') else 'Encuentra tu municipio ideal',
                  key='open_assistant',on_click=lambda:st.session_state.update({'_assistant_open':True}),
                  help='Abre las cuatro preguntas para personalizar la recomendación.')
    if not st.session_state.get('_assistant_open'):return
    with st.container(border=True,key='assistant_form_panel'):
        st.markdown('### Tu próximo municipio, en cuatro decisiones')
        with st.form('municipio_ideal'):
            left,right=st.columns(2)
            with left:
                st.selectbox('1. ¿Qué vivienda buscas?',['VU','VC'],index=None,placeholder='Elige una opción',format_func=lambda x:PROFILE_LABELS[x],key='assistant_profile',help='Colectiva: pisos y apartamentos. Unifamiliar o rural: casas y chalets.')
                st.selectbox('2. ¿Qué tamaño de municipio prefieres?',list(POPULATIONS),key='assistant_population',help='Si no tienes preferencia, deja Cualquier tamaño.')
            with right:
                st.multiselect('3. ¿Qué es más importante para ti?',DIMS,default=None,format_func=lambda x:DIMENSION_LABELS[x],key='assistant_priorities',help='Marca al menos una categoría. Las elegidas influyen más en tu puntuación; las demás siguen contando.')
                st.number_input('4. Alquiler máximo de referencia · €/m²/mes',min_value=0.0,max_value=1000.0,value=None,step=.5,placeholder='Sin límite',key='assistant_budget',help='Opcional. Es un precio por metro cuadrado, no el alquiler mensual total.')
            st.caption('Usamos alquileres declarados en 2024, no anuncios actuales. Cada categoría marcada tiene cuatro veces más importancia que las demás. Después puedes ajustar los porcentajes en Prioridades y filtros.')
            submit,clear=st.columns([2,1])
            with submit:st.form_submit_button('Descubrir mi municipio ideal',type='primary',width='stretch',on_click=submit_assistant)
            with clear:st.form_submit_button('Borrar respuestas',type='secondary',width='stretch',on_click=clear_assistant,args=(st.session_state,))
        if st.session_state.get('_assistant_error'):st.error(st.session_state['_assistant_error'])
        st.caption('Borrar respuestas elimina estas respuestas y sus candidatos. Los filtros del explorador se conservan. Solo Reiniciar restablece toda la aplicación.')


def _bullets(texts):return '<ul>'+''.join('<li>'+esc(x)+'</li>' for x in texts)+'</ul>'


def render_candidates(root,data,full,filtered,mode,choose,living):
    if not st.session_state.get('_assistant_results'):return
    results=recommend(filtered,mode)
    attributes=target_attributes(st.session_state,RESULTS)
    st.html('<div '+attributes+' class="section-heading" role="region" aria-label="Resultados personalizados"><h3>Tus candidatos, según tus prioridades</h3><span class="section-tag">'+str(len(results))+' opciones</span></div>')
    st.caption('Ordenados por el IVP elegido entre los resultados que cumplen tus filtros. Las explicaciones comparan los indicadores con la mediana de los 155 municipios de esta edición.')
    if results.empty:st.info('No hay municipios con índice que cumplan esta combinación. Amplía el tamaño o el límite de alquiler en Prioridades y filtros.');return
    score='ivp_personalizado' if mode=='personalizado' else 'ivp_general'
    label='IVP personalizado' if mode=='personalizado' else 'IVP general'
    for column,row in zip(st.columns(len(results),gap='medium'),results.to_dict('records')):
        with column,st.container(border=True):
            explanation=describe(row,full,mode)
            st.html('<div class="candidate-photo">'+municipality_photo(root,row['codigo_ine'],row['municipio'])+'</div>')
            st.html('<div class="candidate-summary"><h3>'+esc(row['municipio'])+'</h3><strong>'+number(row[score],1)+'<small> / 100</small></strong><p>'+label+' · '+str(explanation['available'])+'/4 categorías con datos</p></div>')
            st.html('<div class="candidate-reasons">'+_bullets([text.split(' Puntuación igual')[0] for text in explanation['positives'][:2]])+'</div>')
            st.button('Abrir ficha de '+row['municipio'],key='candidate_'+row['codigo_ine'],width='stretch',on_click=choose,args=(row['codigo_ine'],))
    st.html('<a class="candidate-anchor" href="#ficha-municipal" target="_self">Ir a la ficha seleccionada ↓</a>')
    render_report_actions(root,data,full,mode,results.codigo_ine.tolist(),living,key='assistant')


def render_evidence(data,frame,code,profile,mode):
    row=frame.set_index('codigo_ine').loc[code];explanation=describe(row,frame,mode)
    missing=' · Falta: '+', '.join(explanation['missing']) if explanation['missing'] else ''
    st.html('<div class="data-evidence"><span>'+str(explanation['available'])+' de 4 categorías con datos'+esc(missing)+'</span><span>Alquiler 2024 · Población 2025 · Servicios consultados en 2026</span></div>')
    with st.expander('Consultar disponibilidad, fechas y fuentes del IVP'):
        st.dataframe(pd.DataFrame(observation_details(data,code,profile)),hide_index=True,width='stretch',column_config={'Enlace':st.column_config.LinkColumn()})
        st.caption('Esta etiqueta indica cuántas categorías tienen información. Para calcular tu IVP necesitamos datos de todas las categorías a las que das importancia.')


def render_fit(frame,code,mode):
    row=frame.set_index('codigo_ine').loc[code];explanation=describe(row,frame,mode)
    st.html('<div class="section-heading"><h3>¿Por qué puede encajar contigo?</h3><span class="section-tag">Explicación de tus indicadores</span></div>')
    left,right=st.columns(2,gap='medium')
    with left:st.html('<div class="fit-card"><h4>A favor de esta opción</h4>'+_bullets(explanation['positives'])+'</div>')
    with right:st.html('<div class="fit-card fit-consider"><h4>Aspectos que valorar</h4>'+_bullets(explanation['consider'])+'</div>')
    st.caption('Comparamos tus categorías elegidas con el valor central de los 155 municipios. Los datos que faltan se indican y no se sustituyen por ceros.')


def render_connectivity(living,code):
    metadata=living.get('connectivity',{});row=metadata.get('municipalities',{}).get(code)
    st.html('<div class="section-heading"><h3>Conectividad para tu día a día</h3><span class="section-tag">Junio 2025 · Información complementaria</span></div>')
    if not row:st.info('No hay datos disponibles de conectividad para este municipio.');return
    items=[('Fibra óptica · FTTH','fibra_pct'),('Al menos 100 Mbps','banda100_pct'),('Al menos 1 Gbps','giga_pct'),('Cobertura móvil 5G','movil5g_pct')]
    st.html('<div class="connectivity-grid">'+''.join('<div><span>'+esc(title)+'</span><strong>'+number(row.get(field),1,missing='Sin datos')+('<small> %</small>' if finite(row.get(field)) else '')+'</strong><small>de hogares del municipio</small></div>' for title,field in items)+'</div>')
    st.caption('SETELECO · Cobertura agregada a 30/06/2025. Las velocidades fijas se refieren a condiciones de máxima demanda. La disponibilidad de una vivienda concreta requiere consulta al operador.')
    st.link_button('Consultar fuente y mapas de cobertura',metadata.get('source_url','https://digital.gob.es/'),icon='↗')


@st.cache_data(show_spinner=False,max_entries=64)
def _cached_timetable(root,signature,code,day,stop):
    return timetable(load_living_data(root),code,day,stop)


def render_transport(root,living,code):
    st.html('<div class="section-heading"><h3>Horarios de autobús descargados</h3><span class="section-tag">Catálogo parcial · no sustituye el horario oficial</span></div>')
    if not living.get('transport') or not Path(living['database']).exists():st.info('No hay datos disponibles de horarios.');return
    with st.container(border=True,key='timetable_panel'):
        today=date.today();monday=today+timedelta(days=(0-today.weekday())%7);saturday=today+timedelta(days=(5-today.weekday())%7);sunday=today+timedelta(days=(6-today.weekday())%7)
        options={'Lunes '+monday.strftime('%d/%m'):monday,'Sábado '+saturday.strftime('%d/%m'):saturday,'Domingo '+sunday.strftime('%d/%m'):sunday,'Elegir fecha':None}
        a,b=st.columns(2)
        with a:choice=st.selectbox('Día que quieres consultar',list(options),key='timetable_day_choice')
        with b:scope=st.radio('Ámbito del horario',['Resumen del municipio','Una parada'],key='timetable_scope',horizontal=True)
        day=options[choice]
        if day is None:day=st.date_input('Fecha del servicio',value=today,key='timetable_date')
        signature=(Path(living['database']).stat().st_mtime_ns,(Path(root)/'data/reference/living/transport.json').stat().st_mtime_ns)
        summary=_cached_timetable(str(root),signature,code,day,None)
        if not summary['available'] or not summary['stops']:st.info('No hay datos disponibles de paradas con horarios para este municipio.');return
        stop=None
        if scope=='Una parada':
            names={x['stop_id']:x['name']+' · '+x['stop_id'] for x in summary['stops']}
            active_stops={x['stop_id'] for x in summary['departures']}
            st.session_state.setdefault('timetable_stop_'+code,next((s for s in names if s in active_stops),next(iter(names))))
            stop=st.selectbox('Parada',list(names),format_func=names.get,key='timetable_stop_'+code)
            summary=_cached_timetable(str(root),signature,code,day,stop)
        st.caption('Fecha exacta: '+day.strftime('%d/%m/%Y')+' · Datos de horarios y días de servicio (GTFS). Versión '+living['transport'].get('feed_version','')+' · Descarga 15/10/2026.')
        if not summary.get('date_in_range',False):st.warning('La fecha queda fuera del intervalo de calendarios publicado. Consulta el operador para un horario actualizado.')
        if not summary['rows']:
            st.info('No hay salidas programadas para esta fecha y selección en el catálogo consultado.')
            st.caption('Catálogo parcial de autobuses interurbanos CRTM; consulta el operador para confirmar el servicio.')
            st.link_button('Abrir información oficial del CRTM','https://www.crtm.es/tu-transporte-publico/')
            return
        rows=[]
        for x in summary['rows']:
            row={'Línea':x['linea'],'Destino publicado':x['destino'],('Horas de salida' if stop else 'Expediciones'):x['salidas'],'Primera':x['primera'],'Última':x['ultima']}
            if stop:row['Intervalo mediano']=number(x['intervalo_mediano_min'],0,missing='No calculable')+(' min' if x['intervalo_mediano_min'] is not None else '')
            rows.append(row)
        st.dataframe(pd.DataFrame(rows),hide_index=True,width='stretch')
        if stop:
            st.caption('El intervalo mediano describe la separación entre horas de salida distintas en esta parada y destino durante el día del servicio. Varias expediciones publicadas pueden coincidir en una misma hora, que se muestra una sola vez. La frecuencia puede variar por franja horaria.')
            with st.expander('Ver todas las horas de salida'):
                for x in summary['rows']:
                    st.markdown('**'+x['linea']+' · '+x['destino']+'**');st.write(' · '.join(gtfs_time(t) for t in x['times']))
        else:st.caption('Se cuenta una vez cada expedición con subida permitida y continuación de itinerario en el municipio, aunque tenga varias paradas. Selecciona Una parada para consultar intervalos y horas de salida.')
        st.caption('Fuente parcial: autobuses interurbanos CRTM. Las salidas son programadas, con excepciones de calendario aplicadas; consulta el operador para incidencias y cambios posteriores a la descarga. Los horarios posteriores a medianoche se indican con +1 día.')
        st.link_button('Abrir información oficial del CRTM','https://www.crtm.es/tu-transporte-publico/')


def _filters_text(state):
    pop=state.get('filtro_poblacion',(0,50000));parts=['Población: '+number(pop[0])+' a '+number(pop[1])+' habitantes']
    for k,label,unit in [('alquiler_max','Alquiler máximo',' €/m²/mes'),('sanidad_km','Atención primaria máxima',' km en línea recta'),('educacion_km','Centro educativo máximo',' km en línea recta'),('transporte_km','Parada máxima',' km en línea recta')]:
        value=state.get('filtro_'+k)
        if value is not None:parts.append(label+': '+number(value,2)+unit)
    if state.get('filtro_nombre'):parts.append('Búsqueda: '+state['filtro_nombre'])
    return ' · '.join(parts)


def report_payload(root,data,frame,mode,codes,living,url,state):
    index=frame.set_index('codigo_ine');weights=frame.attrs['pesos_normalizados'] if mode=='personalizado' else {d:.25 for d in DIMS};records=[]
    for code in codes:
        row=index.loc[code];r={k:(float(row[k]) if finite(row.get(k)) else None) for k in ('poblacion','alquiler_m2_mes')}
        r.update(codigo_ine=code,municipio=row.municipio,active_score=float(row['ivp_personalizado' if mode=='personalizado' else 'ivp_general']) if finite(row['ivp_personalizado' if mode=='personalizado' else 'ivp_general']) else None,explanation=describe(row,frame,mode),sources=observation_details(data,code,frame.attrs['perfil_vivienda']),fibra_pct=living.get('connectivity',{}).get('municipalities',{}).get(code,{}).get('fibra_pct'))
        records.append(r)
    return {'mode':mode,'profile':frame.attrs['perfil_vivienda'],'weights':weights,'records':records,'share_url':url,'filters_text':_filters_text(state)}


@st.cache_data(show_spinner=False,max_entries=16)
def _pdf(root,payload,day):return build_personal_pdf(root,payload)


def render_report_actions(root,data,frame,mode,codes,living,key='sheet'):
    codes=list(dict.fromkeys(codes))[:3]
    if not codes:return
    plan=make_plan(st.session_state,st.session_state.get('municipio_codigo') or codes[0],frame.attrs['perfil_vivienda'],mode,compare=codes)
    context_url=getattr(st.context,'url','')
    base=public_base(root,context_url);url=share_url(base,plan)
    with st.container(key='report_actions_'+key):
        left,right=st.columns([1.2,1.4])
        with left,st.popover('Compartir esta selección',width='stretch'):
            st.write('El enlace conserva municipios, tipología de vivienda, prioridades y filtros.')
            st.code(url,language=None,wrap_lines=True)
            st.link_button('Abrir selección compartida',url,width='stretch')
            if urlsplit(base).hostname in ('127.0.0.1','localhost'):st.caption('Este enlace es local. Al publicar la aplicación podrá abrirse desde otros equipos.')
        with right:
            payload=report_payload(root,data,frame,mode,codes,living,url,st.session_state)
            st.download_button('Descargar mi informe · PDF',_pdf(str(root),payload,date.today().isoformat()),file_name='HabitaMadrid_mi_seleccion.pdf',mime='application/pdf',key='personal_pdf_'+key,on_click='ignore',width='stretch')
