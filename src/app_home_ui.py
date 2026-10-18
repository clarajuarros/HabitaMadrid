"""Neutral home, general top five and accessible municipal introductions."""
import streamlit as st
from src.app_explorer import ranking_view
from src.app_identity import municipality_photo
from src.app_presenter import esc, number, PROFILE_LABELS
from src.app_context import description, transport_lines, official_url


def top_five(frame):
    complete = frame.estado_general.eq('disponible') & frame.ivp_general.notna()
    for dim in ('sanidad','educacion','transporte','vivienda'):
        complete &= frame['puntuacion_'+dim].notna()
    return ranking_view(frame.loc[complete].copy(), 'general').head(5)


def render_top_five(root, frame, profile, choose, housing_change=None):
    with st.container(key='home_top5'):
        st.html('<div class="home-section-title"><h2>Los 5 municipios mejor posicionados</h2>'
                '<details class="ranking-help"><summary aria-label="Cómo se calcula este ranking">ⓘ</summary>'
                '<p>Este ranking compara la proximidad a centros sanitarios, centros educativos, transporte público y el coste del alquiler. Cada categoría tiene la misma importancia. Puedes personalizar estas prioridades en Mi municipio ideal.</p></details></div>')
        if housing_change:
            st.selectbox('Modalidad de vivienda del ranking', ['VU','VC'],
                         format_func=PROFILE_LABELS.get, key='home_housing',
                         index=['VU','VC'].index(profile), on_change=housing_change,
                         help='Cada modalidad tiene su propia referencia de alquiler y su clasificación.')
        st.write('Municipios mejor posicionados según el IVP general, considerando sanidad, educación, transporte y vivienda con la misma importancia.')
        st.caption('Vivienda: '+PROFILE_LABELS[profile]+'. Referencia general, sin aplicar tus filtros o prioridades. Una orientación para comparar, no una recomendación absoluta.')
        for col, row in zip(st.columns(5, gap='small'), top_five(frame).to_dict('records')):
            with col, st.container(border=True):
                photo = municipality_photo(root,row['codigo_ine'],row['municipio'])
                photo = photo.replace('<figcaption>', '<figcaption><details><summary>Autoría y licencia</summary><div>')
                photo = photo.replace('</figcaption>', '</div></details></figcaption>')
                st.html('<div class="top-photo">'+photo+'</div>')
                context = description(root, row['codigo_ine'])
                st.html('<div class="top-summary"><span class="top-position">Puesto '+number(row['posicion_general'])+'</span>'
                        '<h3>'+esc(row['municipio'])+'</h3><strong>'+number(row['ivp_general'],1)+'<small> / 100</small></strong>'
                        '<p>'+esc(context.get('short','Información municipal basada en los datos de esta edición.'))+'</p></div>')
                st.button('Abrir ficha', key='top5_'+row['codigo_ine'], help='Consultar '+row['municipio'],
                          width='stretch', on_click=choose, args=(row['codigo_ine'],))
        with st.expander('Sobre esta selección'):
            st.write('Se incluyen municipios con datos en las cuatro categorías. Los empates comparten puesto; para mostrar cinco tarjetas se usa el orden alfabético y después el código INE, igual que en el ranking completo.')


def render_description(root, code):
    row = description(root, code)
    with st.container(border=True, key='municipal_description'):
        st.markdown('### Conoce el municipio')
        st.write(row.get('description','No disponemos de información suficiente para describir este municipio.'))
        sources = row.get('sources', [])
        if sources:
            with st.expander('Fuentes de esta descripción · '+row.get('updated','')):
                for source in sources:
                    st.link_button(source['label'], source['url'])
                st.caption('Texto de HabitaMadrid basado en información comprobable. Las cifras conservan el año de su fuente.')


def render_public_transport(root, code):
    metadata, row = transport_lines(root, code)
    st.markdown('### Transporte público')
    lines = row.get('lines', [])
    if not lines:
        st.info('Información no disponible: no hemos podido verificar las líneas de este municipio. Esto no significa que no tenga transporte público.')
    else:
        for mode in dict.fromkeys(line['mode'] for line in lines):
            st.markdown('**'+mode+'**')
            cards = []
            for line in lines:
                if line['mode'] != mode:
                    continue
                direct = line.get('verified',False) and official_url(line.get('url',''))
                url = line['url'] if direct else metadata.get('finder_url','https://crtm.es/tu-transporte-publico/info-por-municipio/lineas/')
                label = 'Consultar horarios y recorrido ↗' if direct else 'Buscar esta línea en el CRTM ↗'
                cards.append('<article class="transport-line"><div><strong>Línea '+esc(line['line'])+'</strong><span>'+esc(line['name'])+'</span></div>'
                             '<a href="'+esc(url)+'" target="_blank" rel="noopener noreferrer">'+label+'</a></article>')
            st.html('<div class="transport-lines">'+''.join(cards)+'</div>')
    st.caption('Fuente: CRTM, consulta por municipio · '+metadata.get('updated','Fecha no disponible')+'. Las líneas pueden dar servicio a distintos núcleos del término municipal; consulta sus paradas. Los modos que no figuran no se presentan como inexistentes. Comprueba avisos y horarios oficiales antes del viaje. Este listado no modifica el indicador de proximidad del IVP.')
    if official_url(row.get('source_url','')):
        st.link_button('Ver la consulta oficial del municipio',row['source_url'])



def render_welcome(municipalities, navigate):
    st.html('<section class="welcome-hero" aria-label="Bienvenida a HabitaMadrid">'
            '<div><p class="eyebrow">Tu vida, tu municipio</p>'
            '<h1>Encuentra el municipio que mejor <em>encaja contigo.</em></h1>'
            '<p class="welcome-intro">Explora los municipios de la Comunidad de Madrid, descubre sus servicios '
            'y compara opciones para encontrar el lugar que mejor se adapte a tus necesidades.</p></div>'
            '<aside class="welcome-note"><span>Un lugar para cada forma de vivir</span>'
            '<strong>'+str(municipalities)+'</strong><p>municipios para explorar<br>con tus prioridades como punto de partida.</p></aside></section>')
    with st.container(key='welcome_actions', horizontal=True):
        st.button('Explorar el mapa', key='home_explore', type='primary', on_click=navigate, args=('mapa',))
        st.button('Encontrar mi municipio ideal', key='home_ideal', on_click=navigate, args=('ideal',))
    st.html('<div class="welcome-trust"><span class="edition-dot"></span>'
            'Datos abiertos · Sanidad, educación, transporte y vivienda</div>')
    st.html('<div class="home-section-title"><h2>Una mirada más cercana a cada municipio</h2></div>')
    features = [
        ('01', 'Explora el mapa', 'Sitúa tus opciones y descubre de un vistazo sus indicadores.', 'Abrir mapa', 'mapa'),
        ('02', 'Conoce el lugar', 'Fotografías, servicios, transporte, vivienda y población en su ficha.', 'Buscar un municipio', 'fichas'),
        ('03', 'Empieza por ti', 'Responde unas preguntas y encuentra opciones según lo que te importa.', 'Ir al cuestionario', 'ideal'),
        ('04', 'Compara tus opciones', 'Consulta diferencias entre dos o tres municipios con datos y gráficos.', 'Comparar municipios', 'comparar'),
    ]
    with st.container(key='home_features'):
        for column, (index, title, description, label, destination) in zip(st.columns(4, gap='medium'), features):
            with column:
                st.html('<article class="home-feature"><span>'+index+'</span><h3>'+title+'</h3><p>'+description+'</p></article>')
                st.button(label, key='feature_'+destination, type='tertiary', on_click=navigate, args=(destination,))
    with st.container(key='home_ivp'):
        st.html('<section class="ivp-intro"><div><p class="eyebrow">Cuatro aspectos, una referencia</p>'
                '<h2>¿Qué es el IVP?</h2></div><div><p>El Índice de Vida Ponderado permite comparar municipios '
                'teniendo en cuenta <strong>sanidad, educación, transporte y vivienda</strong>, '
                'dando la misma importancia a cada uno.</p><p class="ivp-secondary">También puedes cambiar estas prioridades '
                'para descubrir qué municipios se adaptan mejor a ti. Es una herramienta orientativa: '
                'no mide por sí sola toda la calidad de vida.</p></div></section>')
        st.button('Cómo se calcula y fuentes →', key='home_methodology', type='tertiary', on_click=navigate, args=('fuentes',))


def render_home_sources(navigate):
    with st.container(key='home_sources'):
        st.html('<div class="home-source-note"><div><h3>Información que puedes contrastar</h3>'
                '<p>Reunimos datos abiertos de fuentes oficiales para facilitar la consulta y comparación. '
                'Cada dato conserva su procedencia y su año.</p></div>'
                '<span>Un proyecto independiente<br>con información pública</span></div>')
        st.button('Consultar las fuentes →', key='home_sources_link', type='tertiary', on_click=navigate, args=('fuentes',))
