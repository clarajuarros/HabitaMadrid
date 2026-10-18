"""Citizen-facing housing context and transparent proposal comparison."""
from pathlib import Path
import pandas as pd
import streamlit as st
from src.app_housing_expansion import expansion_signature, load_expansion
from src.integration_common import IntegrationError
from src.app_presenter import number,finite,raw_item,PROFILE_LABELS
from src.app_charts import _figure
import plotly.graph_objects as go


@st.cache_data(show_spinner=False,max_entries=2)
def _load(root,signature):return load_expansion(root)


def prepared(root):
 try:
  signature=expansion_signature(root)
  return _load(str(root),signature) if signature else None
 except (OSError,ValueError,KeyError,IntegrationError):
  st.info('La ampliación de vivienda necesita revisión. El IVP vigente y sus datos originales se conservan.')
  return None


def render_extended_housing(root,code,profile):
 data=prepared(root)
 if data is None:return
 context=data['contexto'].set_index('codigo_ine').loc[code]
 row=data['indicadores'].loc[data['indicadores'].codigo_ine.eq(code)&data['indicadores'].perfil.eq(profile)].iloc[0]
 sources=data['fuentes']
 if finite(context.valor_tasado_eur_m2):
  st.markdown('### Compra · valor tasado')
  st.html(raw_item('Tasación media por superficie',number(context.valor_tasado_eur_m2,2)+' €/m²',str(int(context.tasacion_anio))+' · '+context.tasacion_trimestre+' · MIVAU'))
  st.caption('Son valoraciones de tasación, no precios de compraventa ni anuncios. No se separan pisos y casas. Se muestran como contexto y no intervienen en el IVP vigente ni en la propuesta de alquiler.')
  st.link_button('Fuente de las tasaciones',sources['purchase']['pagina'])
 if finite(row.relacion_pct):
  st.markdown('### Alquiler e ingresos del municipio')
  st.html('<div class="raw-grid">'+raw_item('Alquiler mensual de referencia',number(row.alquiler_mes)+' €/mes','Mediana SERPAVI · '+str(int(row.anio))+' · '+PROFILE_LABELS[profile])+raw_item('Ingresos medios por hogar',number(row.renta_hogar)+' €/año','Renta neta media · INE · '+str(int(row.anio)))+raw_item('Relación entre ambas cifras',number(row.relacion_pct,1)+' %','Alquiler anual de referencia / ingresos medios del mismo año')+'</div>')
  st.caption('Comparamos dos estadísticas municipales. Este porcentaje no es el esfuerzo real de los inquilinos: los ingresos incluyen hogares propietarios y las viviendas observadas tienen tamaños distintos. No indica cuántas familias pueden pagar un alquiler.')
  st.link_button('Fuente de ingresos · Atlas del INE',sources['atlas']['pagina'])
 if finite(context.ipva_variacion_anual_pct):
  st.markdown('### Evolución del precio del alquiler')
  year=int(context.ipva_anio)
  st.html(raw_item('Variación del índice de precios',number(context.ipva_variacion_anual_pct,1)+' %',str(year)+' respecto a '+str(year-1)+' · INE, IPVA base 2015'))
  st.caption('Este índice mide la evolución de los precios; no es un importe en euros. Se mantiene separado de la serie de medianas SERPAVI y no interviene en las puntuaciones. Los municipios pequeños no tienen un dato individual en esta fuente.')
  st.link_button('Fuente del índice de precios',sources['ipva']['pagina'])
 residential=[]
 for field,label,note in [('viviendas_principales_pct','Viviendas principales','Viviendas con residencia habitual'),('viviendas_no_principales_pct','Viviendas no principales','No permite distinguir aquí entre secundarias y vacías'),('viviendas_anteriores_1981_pct','Viviendas construidas antes de 1981','Sobre el total, incluido año desconocido; solo si no hay celdas ausentes')]:
  if finite(context[field]):residential.append(raw_item(label,number(context[field],1)+' %','Censo 2021 · '+note))
 if finite(context.viviendas_vacias_consumo_num):
  residential.append(raw_item('Viviendas clasificadas como vacías',number(context.viviendas_vacias_consumo_num)+' viviendas','Censo 2021 · Consumo eléctrico · '+str(context.viviendas_vacias_estado)))
 if residential:
  st.markdown('### Características del parque residencial')
  st.html('<div class="raw-grid">'+''.join(residential)+'</div>')
  st.caption('La antigüedad y el uso de una vivienda aportan contexto; no se puntúan como mejores o peores condiciones. Una vivienda no principal no equivale a una vivienda disponible para alquilar. El recuento de vacías procede de la clasificación por consumo eléctrico, es provisional y tampoco acredita oferta disponible.')
  with st.expander('Fuentes del parque residencial'):
   st.link_button('Tipo de vivienda · Censo 2021',sources['census_type']['pagina'])
   st.link_button('Año de construcción · Censo 2021',sources['census_age']['pagina'])
   if finite(context.viviendas_vacias_consumo_num):st.link_button('Uso según consumo eléctrico · Censo 2021',sources['census_usage']['pagina'])
 with st.expander('Cómo se está ampliando la valoración de vivienda'):
  st.write('El IVP vigente sigue valorando el alquiler por metro cuadrado de 2024. Hemos preparado una propuesta que combina coste y relación alquiler-ingresos, con datos de 2023 para que ambas cifras correspondan al mismo año. La revisión concluye que no hay evidencia suficiente para sustituir el IVP vigente; tu puntuación actual se conserva.')
  r=data['comparacion'].loc[data['comparacion'].codigo_ine.eq(code)&data['comparacion'].perfil.eq(profile)].iloc[0]
  st.write('IVP vigente: **'+number(r.ivp_original,2)+'** · IVP propuesto: **'+number(r.ivp_propuesto,2)+'**.')
  st.caption('La comparación completa, las ponderaciones y sus limitaciones están en Cómo se calcula y fuentes.')


def render_housing_review(root,profile):
 data=prepared(root)
 if data is None:return
 method=data['metodologia'];stats=method['resumen'][profile]
 st.markdown('## Ampliación de vivienda · resultado de la revisión')
 st.info('El IVP principal sigue siendo '+method['version_principal']+'. La propuesta de vivienda no está activada: mapa, fichas, Top 5, recomendaciones, informes y comparaciones siguen usando la edición vigente.')
 st.info('Revisión completada: se conserva el IVP vigente. Los datos nuevos enriquecen las fichas; la combinación experimental no ha acreditado una mejora que justifique sustituirlo.')
 st.caption('Modalidad de alquiler: '+PROFILE_LABELS[profile]+' · Propuesta '+method['version']+' · Año común '+str(method['anio_vivienda_propuesta']))
 st.write('La propuesta combina el coste del alquiler por superficie y una relación entre alquiler mensual e ingresos medios por hogar. Esta segunda cifra es una comparación entre estadísticas agregadas, no una medición del esfuerzo de los inquilinos.')
 st.markdown('**Dentro de vivienda:** 75 % coste y 25 % relación alquiler-ingresos. Las cuatro dimensiones mantienen un 25 % cada una en el IVP general. Estos pesos internos son una hipótesis de trabajo, no una calibración estadística ni una garantía de mejor calidad.')
 st.markdown('**Mismo año para alquiler e ingresos:** 2023. La versión vigente usa alquiler de 2024. Separamos el efecto de ese cambio temporal del efecto de añadir un indicador.')
 comparison=data['comparacion'].loc[data['comparacion'].perfil.eq(profile)].copy()
 coverage=pd.DataFrame({'Versión':['Vigente · alquiler 2024','Propuesta · alquiler e ingresos 2023'],'Municipios con vivienda':[stats['vivienda_original'],stats['vivienda_propuesta']],'Municipios con IVP completo':[stats['ivp_original'],stats['ivp_propuesto']]})
 st.dataframe(coverage,hide_index=True,width='stretch')
 st.write('Municipios comparables: **'+str(stats['comparables'])+'**. Correlación de las dos variables residenciales: **'+number(stats['correlacion_indicadores_spearman'],3)+'**. Correlación entre ambos IVP: **'+number(stats['correlacion_ivp_spearman_comun'],3)+'**.')
 st.caption('Son correlaciones de rangos sobre los municipios con ambos datos. No demuestran independencia, causalidad ni superioridad de la propuesta. Las dos variables comparten información de alquiler y pueden solaparse.')
 common=comparison.dropna(subset=['ivp_original','ivp_propuesto'])
 fig=_figure('IVP vigente y propuesto · municipios comparables',height=340)
 fig.add_trace(go.Scatter(x=common.ivp_original,y=common.ivp_propuesto,text=common.municipio,mode='markers',marker={'color':'#173d35','size':7},hovertemplate='%{text}<br>Vigente: %{x:.2f}<br>Propuesto: %{y:.2f}<extra></extra>'))
 fig.add_trace(go.Scatter(x=[0,100],y=[0,100],mode='lines',line={'color':'#899b7c','dash':'dot'},hoverinfo='skip'))
 fig.update_xaxes(title='IVP vigente',range=[0,100]);fig.update_yaxes(title='IVP propuesto',range=[0,100])
 st.plotly_chart(fig,width='stretch',key='housing_proposal_comparison',config={'displayModeBar':False})
 table=comparison[['codigo_ine','municipio','ivp_original','ivp_puente_mismo_anio','ivp_propuesto','delta_puntuacion','efecto_cambio_anio','efecto_indicador_nuevo','puesto_original','puesto_propuesto','cambio_puesto_comun','estado_propuesta']].sort_values('cambio_puesto_comun',ascending=False,na_position='last')
 table=table.rename(columns={'ivp_original':'IVP vigente','ivp_puente_mismo_anio':'IVP solo coste 2023','ivp_propuesto':'IVP propuesto','efecto_cambio_anio':'Año y reajuste de escala','efecto_indicador_nuevo':'Efecto nuevo indicador','cambio_puesto_comun':'Cambio puesto en muestra común'})
 st.dataframe(table,hide_index=True,width='stretch')
 st.caption('Un cambio de puesto positivo indica una subida. La columna de muestra común compara exactamente los mismos municipios; los puestos globales también pueden variar por la cobertura. Ninguna ausencia se transforma en cero.')
 names=comparison.set_index('codigo_ine').municipio.to_dict()
 st.write('**Top 5 vigente:** '+', '.join(names[c] for c in stats['top5_original'])+'.')
 st.write('**Top 5 propuesto:** '+', '.join(names[c] for c in stats['top5_propuesto'])+'.')
 with st.expander('Qué cambia por el año, la escala y el indicador añadido'):
  audit=method['revision']['resumen'][profile]
  st.write('Para separar los cambios usamos primero los precios de 2023 con la escala original de 2024, después reajustamos esa escala y finalmente añadimos el indicador de alquiler e ingresos.')
  st.dataframe(pd.DataFrame({
   'Componente':['Año, conservando la escala original','Reajuste de la escala','Indicador nuevo'],
   'Cambio absoluto medio del IVP':[audit['media_abs_efecto_anio'],audit['media_abs_efecto_referencias'],audit['media_abs_efecto_indicador']]
  }),hide_index=True,width='stretch')
  st.caption('Media de los cambios absolutos en la misma muestra. No deben sumarse las medias absolutas: los componentes pueden compensarse. El desglose con signo sí suma exactamente para cada municipio. No son efectos causales.')
  detail=data['descomposicion'].loc[data['descomposicion'].perfil.eq(profile)]
  st.dataframe(detail[['municipio','ivp_original','ivp_2023_anclas_2024','ivp_puente_mismo_anio','ivp_propuesto','efecto_anio_anclas_constantes','efecto_reajuste_referencias','efecto_nuevo_indicador_controlado']],hide_index=True,width='stretch')
  st.write('Manteniendo el mismo año y la misma muestra, añadir el indicador mueve hasta **'+str(audit['cambio_max_puesto_solo_indicador'])+' puestos**. Al variar el peso del coste entre 50, 75 y 90 %, sin cambiar la normalización ni la muestra, el rango máximo es de **'+str(audit['rango_max_puesto_pesos_moderados'])+' puestos**; la mediana del rango es **'+number(audit['rango_mediano_puesto_pesos_moderados'],0)+'**.')
  st.caption('Estas variaciones son escenarios de diseño. No miden incertidumbre estadística ni validan un peso concreto.')
  st.download_button('Descargar desglose controlado · CSV',detail.to_csv(index=False).encode('utf-8-sig'),file_name='HabitaMadrid_desglose_vivienda_'+profile+'.csv',mime='text/csv',key='housing_controlled_csv',on_click='ignore')
 with st.expander('Fórmulas, normalización, ausencias y sensibilidad'):
  st.code('Relación (%) = 100 × (12 × alquiler mensual mediano 2023) / renta neta media anual por hogar 2023\nPuntuación(x) = 100 × (máximo de referencia − x) / (máximo − mínimo)\nVivienda propuesta = 0,75 × puntuación(coste) + 0,25 × puntuación(relación)\nIVP general = 0,25 × (sanidad + educación + transporte + vivienda)',language='text')
  st.write('Referencias fijas sobre los 155 municipios, por tipología y con sus valores observados. Un indicador constante recibe 50; una fuente sin observaciones no recibe puntuación. Si falta un indicador con peso interno positivo, falta la dimensión Vivienda completa. No se ajustan los pesos según el municipio. Una personalización sin vivienda puede calcularse, con alcance parcial explícito, usando el motor de las cuatro dimensiones.')
  st.write('Se preparan 15 escenarios por modalidad: pesos internos de coste 0, 50, 75, 90 y 100 %, combinados con min–max, min–max acotado P5–P95 y percentiles para los indicadores de vivienda. Las otras tres dimensiones permanecen fijas para aislar el cambio residencial.')
  st.dataframe(data['sensibilidad'].loc[data['sensibilidad'].perfil.eq(profile)],hide_index=True,width='stretch')
 st.warning('La relación agregada mezcla alquileres observados de inquilinos con ingresos de todos los hogares, y depende de la superficie de las viviendas. Además, la propuesta utiliza un año anterior y mantiene cierto solapamiento con el coste. La revisión mantiene el IVP principal: no hay evidencia suficiente para sustituirlo con esta combinación.')
 with st.expander('Fuentes nuevas y papel de cada una'):
  for key,source in data['fuentes'].items():
   st.link_button(source['titulo'],source['pagina'])
  st.write('Atlas del INE: se estudia en la propuesta. IPVA, tasaciones y Censo 2021: contexto. La compra no se mezcla con el alquiler; falta cobertura para un subíndice de compra comparable entre los 155 municipios.')
 base=Path(root)/'data/processed/housing_expansion'
 st.download_button('Descargar comparación de los 155 municipios · CSV',table.to_csv(index=False).encode('utf-8-sig'),file_name='HabitaMadrid_comparacion_vivienda_'+profile+'.csv',mime='text/csv',key='housing_proposal_csv',on_click='ignore')
 st.download_button('Descargar escenarios de sensibilidad · CSV',(base/'sensibilidad_escenarios.csv').read_bytes(),file_name='HabitaMadrid_sensibilidad_vivienda.csv',mime='text/csv',key='housing_sensitivity_csv',on_click='ignore')
