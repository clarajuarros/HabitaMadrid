"""Small, dated demographic cards using only the verified local INE snapshot."""
import streamlit as st
from src.ine_municipal import context_signature,load_context
from src.app_presenter import raw_item,number


@st.cache_data(show_spinner=False,max_entries=2)
def _context(root,signature):
    return load_context(root)


def render_ine_context(root,code,section_heading):
    try:
        frame,manifest,sources=_context(str(root),context_signature(root))
        row=frame.loc[code]
    except (OSError,ValueError,KeyError):
        st.error('Los datos demográficos del INE necesitan una revisión de integridad. No se muestran cifras sin verificar.')
        return
    year=int(row.censo_anio)
    section_heading('Quién vive aquí', 'INE · Censo anual · '+str(year))
    st.html('<div class="raw-grid ine-context-grid">'+''.join(raw_item(label,number(row[field+'_pct'],1)+' %',number(row[field])+' personas · '+str(year)) for field,label in [('menores_16','Menores de 16 años'),('entre_16_64','De 16 a 64 años'),('mayores_65','65 años o más')])+'</div>')
    st.caption('La edad se calcula sobre las '+number(row.poblacion_censo)+' personas del Censo anual. El padrón municipal y el censo son operaciones distintas y pueden presentar diferencias; cada cifra conserva su fuente.')
    section_heading('Hogares y viviendas', 'INE · Censo de Población y Viviendas · 2021')
    st.html('<div class="raw-grid ine-context-grid">'+raw_item('Hogares',number(row.hogares_2021,1),'2021 · '+number(row.hogares_unipersonales_2021,1)+' de una persona')+raw_item('Viviendas principales',number(row.principales_2021,1),'2021 · residencia habitual')+raw_item('Viviendas no principales',number(row.no_principales_2021,1),'2021 · no acredita disponibilidad para alquilar')+'</div>')
    st.caption('Las cifras censales se conservan tal como las publica el INE. Los decimales pueden responder al tratamiento estadístico de confidencialidad. Esta información describe el municipio y no modifica el IVP.')
    with st.expander('Fuentes y fechas de esta revisión municipal'):
        st.write('Padrón '+str(int(row.padron_anio))+': **'+number(row.poblacion_ine)+' habitantes**, contrastado con el maestro de HabitaMadrid. Revisión de los 179 municipios; '+str(manifest['elegibles'])+' cumplen el límite de 50.000 habitantes.')
        for key,label in [('padron','Padrón municipal'),('edades','Estructura por edad'),('hogares','Hogares'),('viviendas','Viviendas')]:
            source=sources[key]
            st.link_button(label+' · INE',source['pagina'])
            st.caption('Referencia: '+source['referencia']+' · Publicación de la tabla: '+source['publicacion']+' · Descarga: '+source['descarga_utc'][:10])
