"""HabitaMadrid — interfaz local sobre las ediciones verificadas de las fases 1–6."""
from pathlib import Path
import json
import logging

import pandas as pd
import streamlit as st

from src.app_charts import (dimension_chart, population_chart,
                            rent_history_chart)
from src.app_data import load_app_data, municipal_view, search_options, snapshot_signature
from src.app_presenter import (DIMENSION_LABELS, PROFILE_LABELS, distance, esc,
                               finite, metric_card, number, raw_item, safe_link,
                               selected_value, service_card)
from src.app_services import load_service_tables, service_listing
from src.app_service_map import render_service_map
from src.app_map import MAP_INDICATORS, build_deck, build_map_frame, load_map_data, map_legend
from src.app_selection import apply_map_selection
from src.app_navigation import (SHEET, HOME, VIEW, VIEWS, request_navigation, target_attributes,
                                render_navigation, change_view, retain_view_state, render_site_navigation)
from src.app_state import reset_application, close_sheet
from src.app_housing_ui import render_extended_housing, render_housing_review
from src.app_home_ui import (render_top_five, render_description, render_public_transport,
                             render_welcome, render_home_sources)
from src.app_identity import brand_symbol, municipality_photo
from src.app_explore_ui import render_preferences, render_ranking
from src.app_compare_ui import render_comparison
from src.integration_common import IntegrationError
from src.app_living_data import load_living_data
from src.app_share import restore_plan
from src.app_living_ui import (render_assistant_entry, render_candidates, render_evidence,
                               render_fit, render_connectivity, render_transport, render_report_actions)

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger("habitamadrid")
CHART_CONFIG = {"displayModeBar": False, "responsive": True}

st.set_page_config(page_title="HabitaMadrid · Encuentra tu lugar", page_icon="🏡",
                   layout="wide", initial_sidebar_state="collapsed")
st.html("<style>" + (ROOT / "assets/app.css").read_text(encoding="utf-8") + "</style>")


@st.cache_data(show_spinner=False, max_entries=2)
def cached_data(root, signature):
    return load_app_data(root)


@st.cache_data(show_spinner=False, max_entries=2)
def cached_services(root, signature):
    return load_service_tables(root)


@st.cache_data(show_spinner=False, max_entries=2)
def cached_map(root, signature):
    return load_map_data(root)


def section_heading(title, detail=""):
    st.html(f'<div class="section-heading"><h3>{esc(title)}</h3><span class="section-tag">{esc(detail)}</span></div>')


def plot(figure, key, alt):
    st.plotly_chart(figure, width="stretch", theme=None, config=CHART_CONFIG, key=key, alt=alt)


def initialize_selection(data):
    """Comparte una única selección entre widgets y enlaces de la ficha."""
    municipalities = data["municipios"]
    codes = municipalities.codigo_ine.tolist()
    incoming = (st.query_params.get("municipio", ""), st.query_params.get("vivienda", ""))
    if "municipio_codigo" not in st.session_state or incoming != st.session_state.get("_last_query"):
        st.session_state["municipio_codigo"] = incoming[0] if incoming[0] in codes else None
        st.session_state["perfil_vivienda"] = incoming[1] if incoming[1] in PROFILE_LABELS else "VU"
        st.session_state["busqueda_nombre"] = ""
        if incoming[0] in codes:
            st.session_state["_active_view"] = "fichas"
    return codes


def choose_municipality(code):
    focus_search()
    st.session_state["municipio_codigo"] = code
    st.session_state["busqueda_nombre"] = ""
    request_navigation(st.session_state, SHEET, code)


def render_header(data):
    attributes = target_attributes(st.session_state, HOME)
    st.html(f'''<header {attributes} class="home-header"><div class="brand-lockup">{brand_symbol()}<div><div class="brand">Habita<span>Madrid</span></div>
      <p>Compara municipios para encontrar tu lugar en la Comunidad de Madrid</p></div></div>
      <span class="home-badge">{len(data['municipios'])} municipios · Datos abiertos</span></header>''')


def focus_search():
    # Clear native picking after a search without remounting the map or moving its camera.
    st.session_state["_mapa_pick_revision"] = st.session_state.get("_mapa_pick_revision", 0) + 1


def search_municipality():
    focus_search()
    code = st.session_state["municipio_codigo"]
    if code is None:
        close_sheet(st.session_state, st.query_params)
    else:
        request_navigation(st.session_state, SHEET, code)


def render_selector(data, codes):
    names = data["municipios"].set_index("codigo_ine").municipio.to_dict()
    with st.container(key="search_panel"):
        search, housing, sheet = st.columns([2.4, 1.4, .8], gap="medium", vertical_alignment="bottom")
        with search:
            st.selectbox("Explora un municipio", options=codes, format_func=lambda code: names[code],
                         key="municipio_codigo", index=None, placeholder="Escribe el nombre de un municipio…", on_change=search_municipality,
                         help="Escribe para ver sugerencias. Al elegir un municipio irás a su ficha; el mapa conserva tu zoom.")
        with housing:
            st.selectbox("Tipo de vivienda", options=["VU", "VC"],
                         format_func=lambda profile: PROFILE_LABELS[profile], key="perfil_vivienda",
                         help="Colectiva: pisos y apartamentos. Unifamiliar o rural: casas y chalets. Alquiler declarado de 2024; cada perfil se calcula por separado.")
        with sheet:
            if st.session_state.get("municipio_codigo"):
                st.button('Abrir ficha', key='selector_open_sheet', width='stretch',
                          on_click=choose_municipality, args=(st.session_state['municipio_codigo'],))
            else:
                st.caption("Elige un municipio en el buscador o el mapa")
    code, profile = st.session_state["municipio_codigo"], st.session_state["perfil_vivienda"]
    if code and st.query_params.get("municipio") != code:
        st.query_params["municipio"] = code
    elif not code:
        st.query_params.pop("municipio", None)
    if st.query_params.get("vivienda") != profile:
        st.query_params["vivienda"] = profile
    st.session_state["_last_query"] = (code or "", profile)
    return code, profile


def render_municipality_heading(view, explorer_frame=None, mode="general"):
    municipality, index = view["municipio"], view["index_row"]
    profile = view["perfil_vivienda"]
    general = ('<div class="heading-score"><span>IVP general</span><strong>'
               + esc(number(index.get("valor_indice"), 1, missing="Sin datos")) + '</strong><small>'
               + ('Puesto ' + number(index.get("posicion")) + ' de ' + number(index.get("municipios_con_indice"))
                  if finite(index.get("valor_indice")) else 'Sin puesto · falta información')
               + '</small></div>')
    personal = ""
    if mode == "personalizado" and explorer_frame is not None:
        row = explorer_frame.set_index("codigo_ine").loc[municipality["codigo_ine"]]
        scope = "Alcance parcial" if row.alcance_personalizado != "cuatro_dimensiones" else "Cuatro categorías"
        personal = ('<div class="heading-score personal"><span>IVP personalizado</span><strong>'
                    + esc(number(row.ivp_personalizado, 1)) + '</strong><small>'
                    + esc(scope) + ' · Puesto ' + esc(number(row.posicion_personalizada, missing="—"))
                    + '</small></div>')
    attributes = target_attributes(st.session_state, SHEET, municipality["codigo_ine"])
    st.html(f'''<section {attributes} class="municipal-cover" aria-label="Ficha del municipio">{municipality_photo(ROOT, municipality["codigo_ine"], municipality["municipio"])}<div class="municipal-heading"><div><h2>{esc(municipality['municipio'])}</h2>
      <p>Comunidad de Madrid · INE {esc(municipality['codigo_ine'])} · {number(municipality['poblacion'])} habitantes · 2025</p>
      <span class="profile-pill">{esc(PROFILE_LABELS[profile])}</span></div>
      <div class="heading-scores">{general}{personal}</div></div></section>''')


def render_municipality_header(view):
    municipality, index = view["municipio"], view["index_row"]
    profile = view["perfil_vivienda"]
    housing_id = "vivienda_alquiler_" + profile.lower() + "_m2_mediana"
    housing = selected_value(view["observaciones"], housing_id)
    available = finite(index.get("valor_indice"))
    cards = [metric_card("Índice de Vida Ponderado", number(index.get("valor_indice"), 1),
                         "Índice orientativo · pesos iguales", "/ 100" if available else "", accent=True),
             metric_card("Posición en este perfil", number(index.get("posicion"), missing="—"),
                         f"Entre {number(index.get('municipios_con_indice'))} municipios con índice", ""),
             metric_card("Población", number(municipality["poblacion"]),
                         "Habitantes · padrón a 1 de enero de 2025"),
             metric_card("Alquiler de referencia", number(housing, 2),
                         "Mediana declarada · SERPAVI 2024", "€/m²/mes" if finite(housing) else "")]
    st.html('<div class="metric-grid">' + "".join(cards) + '</div>')
    if not available:
        missing = json.loads(index.get("dimensiones_faltantes_json", "[]"))
        blocked = json.loads(index.get("dimensiones_bloqueadas_json", "[]"))
        reason = "Falta información de " + ", ".join(DIMENSION_LABELS[key].lower() for key in missing) if missing else "Hay una dimensión pendiente de revisión"
        if blocked:
            reason += "; revisión pendiente de " + ", ".join(DIMENSION_LABELS[key].lower() for key in blocked)
        st.html(f'<div class="missing-strip">{esc(reason)} para esta tipología. El municipio conserva su ficha, pero no recibe IVP ni posición. No se rellena con la otra tipología.</div>')


def render_map(data, map_data, view, explorer_frame, filtered, mode):
    profile = view["perfil_vivienda"] if view else st.session_state["perfil_vivienda"]
    selected_code = view["municipio"]["codigo_ine"] if view else None
    revision_key = "_mapa_municipios_revision"
    event_key = "mapa_municipios_" + str(st.session_state.get(revision_key, 0))

    def reset_map_view():
        st.session_state[revision_key] = st.session_state.get(revision_key, 0) + 1

    with st.expander("Opciones del mapa · indicador y encuadre", expanded=False):
        controls, framing = st.columns([1.35, 1], gap="large", vertical_alignment="bottom")
        with controls:
            indicator = st.selectbox("Colorear el mapa por", list(MAP_INDICATORS),
                                     format_func=lambda value: "IVP · índice elegido" if value == "ivp" else MAP_INDICATORS[value], key="indicador_mapa")
        with framing:
            focus = st.radio("Encuadre del mapa", ["region", "municipio"], horizontal=True, key="encuadre_mapa", on_change=reset_map_view,
                             disabled=selected_code is None,
                             format_func=lambda value: "Toda la Comunidad" if value == "region" else "Municipio seleccionado")
            st.button("Restablecer vista", key="restablecer_vista_mapa", on_click=reset_map_view)
    visible_codes = tuple(filtered["codigo_ine"])
    frame = build_map_frame(map_data, data, profile, indicator,
                            explorer_frame=explorer_frame, mode=mode, visible_codes=visible_codes)
    legend = map_legend(frame)
    allowed_codes = tuple(data["municipios"]["codigo_ine"])

    layer_id = "municipios_" + str(st.session_state.get("_mapa_pick_revision", 0))

    def select_on_map():
        apply_map_selection(st.session_state, st.query_params, allowed_codes, event_key=event_key, layer_id=layer_id)

    # A camera belongs to an explicit framing command, never to selection.
    camera_key = (st.session_state.get(revision_key, 0), focus)
    if st.session_state.get("_mapa_camera_key") != camera_key:
        st.session_state["_mapa_camera"] = None
        st.session_state["_mapa_camera_key"] = camera_key
    deck = build_deck(map_data, frame, selected_code, focus=focus,
                      camera=st.session_state.get("_mapa_camera"), layer_id=layer_id)
    st.session_state["_mapa_camera"] = deck.initial_view_state

    with st.container(border=True, key="map_panel"):
        selected_rows = frame.loc[frame.codigo_ine.eq(selected_code)]
        selected = selected_rows.iloc[0] if len(selected_rows) else None
        detail = '<span>Selecciona un municipio para consultar sus datos</span>'
        if selected is not None:
            score_label = number(selected.valor, 0 if indicator == "poblacion" else 1, missing="Sin datos")
            detail = f'<span>{esc(selected.municipio)} · {esc(legend["label"])}: {esc(score_label)}</span>'
        st.html(f'''<div class="map-topline"><div><strong>Explora la Comunidad de Madrid</strong>
          <span>Pasa el cursor para consultar · Haz clic para abrir su ficha</span></div>{detail}</div>''')
        st.pydeck_chart(deck,
                        key=event_key, width="stretch", height=440,
                        selection_mode="single-object", on_select=select_on_map,
                        alt="Límites reales de Madrid. Municipios coloreados por su IVP; sin datos en gris. Haz clic en un municipio para abrir su ficha.")
        if selected_code:
            st.button('Abrir la ficha de '+str(selected.municipio), key='map_open_sheet',
                      on_click=choose_municipality, args=(selected_code,))
        color = lambda value: "rgb(" + ",".join(str(int(c)) for c in value[:3]) + ")"
        minimum = number(legend["minimo"], 0 if indicator == "poblacion" else 0)
        maximum = number(legend["maximo"], 0 if indicator == "poblacion" else 0)
        st.html(f'''<div class="map-legend"><div class="map-scale" style="background:linear-gradient(to right,{color(legend['color_minimo'])},#f4d96b,{color(legend['color_maximo'])})"></div>
          <div class="map-scale-labels"><span>{esc(minimum)} · Bajo</span><span>{esc(legend['label'])} · {esc(legend['unidad'])}</span><span>{esc(maximum)} · Alto</span></div>
          <div class="map-categories"><span><i style="background:{color(legend['color_ausencia'])}"></i>Sin datos</span>
          <span><i style="background:{color(legend['color_excluido'])}"></i>Más de 50.000 habitantes</span>
          <span><i style="background:{color(legend['color_filtrado'])}"></i>No cumple filtros</span>
          <span><i style="background:#acb8a8"></i>Jurisdicción compartida</span>
          <span><i class="map-selected-swatch"></i>Seleccionado</span></div></div>''')
    if selected is not None and not selected.cumple_filtros:
        st.info("La ficha seleccionada no cumple los filtros actuales; se conserva para consulta.")
    st.caption(legend["interpretacion"] + " Límites oficiales. Amplía con la rueda o dos dedos y arrastra para desplazarte.")
    with st.expander("Consultar los valores del mapa en una tabla"):
        table = frame[["municipio", "codigo_ine", "poblacion", "valor", "estado"]].rename(columns={
            "municipio": "Municipio", "codigo_ine": "INE", "poblacion": "Habitantes (2025)",
            "valor": legend["label"] + " · " + legend["unidad"], "estado": "Estado"})
        table["Estado"] = table.Estado.map({"disponible": "Con dato", "sin_dato": "Sin dato", "fuera_ambito": "Fuera del ámbito", "fuera_filtros": "No cumple filtros"})
        table["Cumple filtros"] = frame["cumple_filtros"]
        st.dataframe(table, hide_index=True, width="stretch")


def render_summary(view):
    index, obs = view["index_row"], view["observaciones"]
    left, right = st.columns([1.25, 1], gap="large")
    with left:
        with st.container(border=True):
            section_heading("Cuatro perspectivas, un municipio", "Puntuaciones de 0 a 100")
            plot(dimension_chart(pd.Series(index)), "dimensions", "Puntuación de sanidad, educación, transporte y vivienda; los datos ausentes se indican expresamente.")
            st.caption("Cada categoría tiene la misma importancia: un 25 %. Una puntuación alta expresa una posición relativa en esta edición, no garantiza calidad o disponibilidad del servicio.")
    with right:
        with st.container(border=True):
            section_heading("Lo que hay detrás del índice", "Datos originales")
            items = [raw_item("Atención primaria más próxima", distance(selected_value(obs, "distancia_atencion_primaria_directorio_m")), "Centro de salud o consultorio · directorio revisado"),
                     raw_item("Centro educativo más próximo", distance(selected_value(obs, "distancia_centro_educativo_general_m")), "Tipologías generales seleccionadas · todas las titularidades"),
                     raw_item("Parada de autobús más próxima", distance(selected_value(obs, "distancia_parada_bus_parcial_m")), "Autobuses interurbanos · catálogo parcial"),
                     raw_item("Alquiler de esta tipología", number(selected_value(obs, "vivienda_alquiler_" + view["perfil_vivienda"].lower() + "_m2_mediana"), 2), "EUR/m²/mes · declarado en 2024")]
            st.html('<div class="raw-grid">' + "".join(items) + '</div>')
            st.html('<div class="quiet-strip">Las distancias son en línea recta desde un punto representativo del núcleo principal. No equivalen a trayectos ni minutos de viaje.</div>')


def render_service_group(tables, code, kind, title):
    result = service_listing(tables, code, kind, nearby_limit=5)
    st.subheader(title)
    st.caption(result["coverage_note"])
    if kind == "educacion":
        st.caption("El listado reúne los centros educativos registrados como activos. Para calcular el IVP se elige el centro más próximo entre diez tipos de enseñanza; el listado incluye otros tipos también.")
    local, nearby = st.columns(2, gap="large")
    for column, key, heading in [(local, "local", "En el municipio"), (nearby, "nearby", "En otros municipios cercanos")]:
        frame = result[key]
        with column:
            with st.container(border=True):
                section_heading(heading, f"{len(frame)} registros" if key == "local" else "Hasta 5 destinos")
                if frame.empty:
                    absence = {"educacion": "No hay centros educativos registrados en este municipio en el catálogo de alta consultado.",
                               "sanidad": "No hay centros de salud ni consultorios registrados en este municipio en el directorio consultado.",
                               "hospitales": "Información no disponible: el catálogo parcial no permite confirmar si hay hospitales en el municipio.",
                               "transporte": "Información no disponible: este catálogo parcial no permite confirmar las paradas del municipio."}
                    message = "No hay datos disponibles. " + (absence[kind] if key == "local" else "No hay otros destinos con posición verificable en esta selección.")
                    st.html('<div class="empty-state">' + esc(message) + ' El catálogo no demuestra ausencia real de servicios.</div>')
                else:
                    st.html("".join(service_card(row) for _, row in frame.head(5).iterrows()))
                    if len(frame) > 5:
                        with st.expander(f"Ver los {len(frame)} registros locales"):
                            visible = frame[["nombre", "modalidad", "titularidad", "direccion", "lineas", "distancia_m"]].rename(
                                columns={"nombre": "Nombre publicado", "modalidad": "Tipo", "titularidad": "Titularidad", "direccion": "Dirección", "lineas": "Líneas", "distancia_m": "Línea recta (m)"})
                            st.dataframe(visible, hide_index=True, width="stretch",
                                         column_config={"Línea recta (m)": st.column_config.NumberColumn(format="%.0f m")})
    st.write("")


def render_services(tables, view, map_data):
    st.html('<div class="quiet-strip">Primero los servicios concretos. Los cercanos pueden estar en municipios de más de 50.000 habitantes. Las distancias no acreditan horarios, frecuencia, plazas o acceso efectivo.</div>')
    code = view["municipio"]["codigo_ine"]
    render_service_map(tables, map_data, code)
    render_public_transport(ROOT, code)
    with st.expander("Horarios descargados de autobuses · consulta por fecha y parada"):
        render_transport(ROOT, load_living_data(ROOT), code)
    render_service_group(tables, code, "sanidad", "Salud · centros de salud y consultorios")
    render_service_group(tables, code, "hospitales", "Hospitales · catálogo geográfico parcial")
    render_service_group(tables, code, "educacion", "Educación · centros registrados")
    render_service_group(tables, code, "transporte", "Transporte · paradas de autobús interurbano")



def render_housing(view):
    profile = view["perfil_vivienda"]
    history = view["vivienda_historico"]
    current = history.loc[history.anio.eq(2024)]
    row = current.iloc[0] if len(current) == 1 else pd.Series(dtype=object)
    if not finite(row.get("alquiler_m2_mes_mediana")):
        st.info("No hay datos de alquiler disponibles para este municipio y tipología en 2024. Los años anteriores publicados se conservan en el gráfico.")
    left, right = st.columns([1.5, 1], gap="large")
    with left:
        with st.container(border=True):
            section_heading("La evolución del alquiler", PROFILE_LABELS[profile])
            plot(rent_history_chart(history, view["municipio"]["codigo_ine"], profile), "rent_history",
                 "Evolución histórica de la mediana del alquiler declarado por metro cuadrado para la tipología seleccionada; los años sin información se muestran como huecos.")
    with right:
        with st.container(border=True):
            section_heading("Referencia de 2024", "MIVAU · SERPAVI")
            values = [raw_item("Mediana por superficie", number(row.get("alquiler_m2_mes_mediana"), 2) + (" €/m²/mes" if finite(row.get("alquiler_m2_mes_mediana")) else ""), "Medida utilizada en vivienda dentro del IVP"),
                      raw_item("Mediana mensual", number(row.get("alquiler_mes_mediana")) + (" €/mes" if finite(row.get("alquiler_mes_mediana")) else ""), "Contexto: depende de las superficies observadas"),
                      raw_item("Percentil 25 / percentil 75", number(row.get("alquiler_m2_mes_p25"), 2) + " / " + number(row.get("alquiler_m2_mes_p75"), 2), "EUR/m²/mes · no es un intervalo de confianza"),
                      raw_item("Testigos publicados", number(row.get("testigos")), "Observaciones de la estadística; no viviendas disponibles")]
            st.html('<div class="raw-grid">' + "".join(values) + '</div>')
    st.caption("Son rentas declaradas de vivienda habitual, no anuncios actuales. No describen la oferta disponible ni el esfuerzo económico de un hogar. Las tipologías colectiva y unifamiliar/rural se mantienen separadas.")
    render_extended_housing(ROOT, view['municipio']['codigo_ine'], profile)


def render_population(view):
    context = view["contexto_demografico"]
    left, right = st.columns([1.5, 1], gap="large")
    with left:
        with st.container(border=True):
            section_heading("Cómo ha cambiado la población", "Padrón municipal")
            plot(population_chart(view["poblacion_historico"], view["municipio"]["codigo_ine"]), "population",
                 "Evolución del padrón municipal por año, conservando los años sin información.")
    with right:
        with st.container(border=True):
            section_heading("Un poco más de contexto", "Cada dato conserva su año")
            change = context.get("crecimiento_2020_2025_pct")
            values = [raw_item("Cambio de población 2020–2025", number(change, 1) + (" %" if finite(change) else ""), "Variación acumulada entre padrones, no crecimiento anual"),
                      raw_item("Renta disponible per cápita · 2023", number(context.get("renta_disponible_per_capita_2023")) + " €" if finite(context.get("renta_disponible_per_capita_2023")) else "Sin dato", "EUR por persona y año; no equivale al salario o renta del hogar"),
                      raw_item("Saldo migratorio externo a la CM · 2024", number(context.get("saldo_migratorio_externo_cm_2024")), "Entradas menos salidas a otras CCAA y al extranjero"),
                      raw_item("Ese saldo por 1.000 habitantes", number(context.get("saldo_migratorio_externo_cm_2024_por1000"), 1), "Denominador: padrón a 1 de enero de 2024")]
            st.html('<div class="raw-grid">' + "".join(values) + '</div>')
    st.caption("El saldo mostrado es parcial: no incluye el balance entre municipios madrileños. No equivale a la variación total de población. Estas medidas son contexto y no aumentan ni reducen el IVP.")


def render_methodology(data, view=None):
    with st.expander('Comparar IVP vigente y propuesta ampliada de vivienda'):
        render_housing_review(ROOT, view['perfil_vivienda'] if view else st.session_state.get('perfil_vivienda','VU'))
    section_heading("Un índice que puedes revisar", "IVP propio · edición 1.0")
    st.write("El IVP combina proximidad a atención primaria, centros educativos seleccionados, paradas del feed parcial de autobús y coste del alquiler de la tipología elegida. En el índice general cada dimensión pesa un 25 %. El personalizado utiliza los pesos efectivos del panel de prioridades.")
    st.caption("La escala principal compara cada indicador con sus extremos observados: 0 es el extremo menos favorable y 100 el más favorable dentro de esta edición. No representa un umbral de suficiencia. Los pesos iguales no garantizan igual influencia de cada variable.")
    with st.expander("Fórmula, ausencias y posiciones"):
        st.code("Puntuación = 100 × (máximo de referencia − valor) / (máximo − mínimo)\nIVP = 0,25 × sanidad + 0,25 × educación + 0,25 × transporte + 0,25 × vivienda", language="text")
        st.write("Cada tipología utiliza sus referencias de alquiler guardadas; no se recalculan al cambiar de municipio. Si falta una dimensión con peso positivo, no hay índice: no se redistribuyen sus pesos. Los empates comparten puesto y las posiciones se calculan antes de redondear.")
        st.write("VC y VU tienen distintas referencias de alquiler y distinta cobertura. Sus puestos pertenecen a clasificaciones separadas. El IVP no es una medición oficial de calidad de vida y permite compensaciones entre dimensiones.")
    with st.expander("Qué contrasta la sensibilidad"):
        st.write("Tres normalizaciones —min–max, recorte en los percentiles 5 y 95, y rangos percentiles— se combinan con los pesos iguales y doce transferencias de diez puntos porcentuales entre dimensiones: 39 escenarios por tipología.")
        st.write("El rango de puestos describe solo estos escenarios. Las frecuencias top 10 no son probabilidades ni garantías de estabilidad. Esta sensibilidad corresponde al índice general.")
    with st.expander("Revisión de las ubicaciones sanitarias"):
        st.write("Se han revisado 431 registros del directorio institucional: 425 destinos están en los 179 municipios madrileños y seis quedan fuera del ámbito. Dos puntos se corrigen por identidad oficial usando fuentes institucionales independientes, conservando las coordenadas originales.")
        st.write("Soto del Real utiliza la capa IDEM enlazada a su ficha. Cenicientos utiliza el centro 2409 de Libre Elección enlazado desde su ficha; los portales mantienen direcciones diferentes y no se presume que sean el mismo inmueble. El directorio no garantiza horarios, apertura actual o asignación asistencial.")
    with st.expander("Conectividad y horarios complementarios"):
        st.write("Cobertura SETELECO de junio de 2025: porcentaje de hogares por municipio. Disponible en el resumen; se conserva la fuente y hoja original del archivo oficial.")
        st.link_button("Fuente oficial de conectividad", "https://digital.gob.es/telecomunicaciones-infraestructuras-digitales/areas-interes/banda-ancha/informacion-cobertura")
        st.write("Horarios de autobuses interurbanos CRTM descargados el 15/10/2026, versión 20260910. Cada consulta aplica el calendario semanal y las altas/bajas de fechas excepcionales. Se pueden comparar fechas laborables y de fin de semana desde Servicios cercanos.")
        st.link_button("Fuente oficial de transporte", "https://datos.crtm.es/")
    section_heading("Datos y procedencia", "Consulta: 15 de octubre de 2026")
    source_lookup = data["fuentes"].set_index("fuente")
    def source_url(identifier):
        return safe_link(source_lookup.loc[identifier, "url"]) if identifier in source_lookup.index else ""
    sources = [
        ("Población y contexto", "Padrón 2025; renta per cápita 2023; saldo externo a la CM 2024.", source_url("padron_por_sexo")),
        ("Atención primaria", "Directorio oficial revisado de centros de salud y consultorios. Coordenadas publicadas; consulta 2026.", source_url("cm_directorio_atencion_primaria")),
        ("Vivienda", "SERPAVI 2011–2024 del MIVAU. Alquiler declarado; tipologías separadas.", source_url("MIVAU_SERPAVI_2011_2024")),
    ]
    for title, description, url in sources:
        st.html(f'<div class="source-card"><strong>{esc(title)}</strong><p>{esc(description)}</p><a href="{esc(url)}" target="_blank" rel="noopener noreferrer">Consultar la fuente institucional ↗</a></div>')
    with st.expander("Fuentes utilizadas y enlaces de descarga"):
        sources_table = data["fuentes"].copy()
        columns = [c for c in ["fuente", "url", "fecha_descarga_utc", "licencia"] if c in sources_table]
        display = sources_table[columns].rename(columns={"fuente": "Fuente", "url": "Enlace", "fecha_descarga_utc": "Fecha de descarga", "licencia": "Condiciones"})
        st.dataframe(display, hide_index=True, width="stretch", column_config={"Enlace": st.column_config.LinkColumn()})
    if view is None:
        return
    st.write("")
    with st.expander("Cómo puede cambiar el puesto al cambiar las reglas"):
        sensitivity = view["sensibilidad"]
        if finite(sensitivity.get("posicion_min")):
            st.write("En los 39 escenarios comprobados, su puesto va del "
                     + number(sensitivity['posicion_min']) + " al " + number(sensitivity['posicion_max']) + ".")
            st.caption("Se ensayan distintas formas de combinar los datos. Es una comparación de métodos, no una probabilidad ni una garantía.")
        else:
            st.info("No hay información suficiente para comparar sus puestos en esta modalidad.")

    download = view["observaciones"][["codigo_ine", "indicador_id", "valor", "anio", "periodo", "estado"]].copy()
    definitions = data["catalogo_seleccionado"][["indicador_id", "nombre", "unidad"]]
    download = download.merge(definitions, on="indicador_id", how="left", validate="many_to_one")
    st.download_button("Descargar los indicadores de esta ficha", download.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"HabitaMadrid_{view['municipio']['codigo_ine']}_{view['perfil_vivienda']}.csv", mime="text/csv", on_click="ignore")



def render_sheet(data, services, map_data, living, view, explorer_frame, filtered, profile, code, mode):
    render_municipality_heading(view, explorer_frame, mode)
    st.button("Cerrar ficha", key="close_municipal_sheet", on_click=close_sheet,
              args=(st.session_state, st.query_params), help="Vuelve a la portada y conserva tus filtros y prioridades.")
    render_description(ROOT, code)
    st.caption("El IVP combina cercanía a sanidad, educación y transporte con el alquiler, en una escala de 0 a 100. Una cifra más alta indica un resultado más favorable en estos datos; no mide por sí sola toda la calidad de vida. Si falta un dato necesario, no asignamos puntuación.")
    render_evidence(data, explorer_frame, code, profile, mode)
    tabs = st.tabs(["Resumen", "Servicios cercanos", "Vivienda", "Población", "Ranking", "Comparar", "Cómo se calcula y fuentes"],
                   key="vistas_municipales")
    with tabs[4]:
        render_ranking(filtered, explorer_frame, profile, mode)
    with tabs[5]:
        render_comparison(data, services, explorer_frame, profile, code, mode, choose_municipality)
    with tabs[0]:
        render_municipality_header(view)
        render_fit(explorer_frame, code, mode)
        render_report_actions(ROOT, data, explorer_frame, mode, [code], living, key="sheet")
        render_public_transport(ROOT, code)
        render_connectivity(living, code)
        if mode == "personalizado":
            personal = explorer_frame.set_index("codigo_ine").loc[code]
            scope = "4 dimensiones" if personal.alcance_personalizado == "cuatro_dimensiones" else "Alcance parcial · " + str(len(explorer_frame.attrs["dimensiones_activas"])) + " dimensiones"
            st.html('<div class="personal-summary"><strong>IVP personalizado: ' + esc(number(personal.ivp_personalizado, 2))
                    + '</strong><span>' + esc(scope) + ' · Puesto global: ' + esc(number(personal.posicion_personalizada, missing="—")) + '</span></div>')
            st.caption("El IVP general da la misma importancia a las cuatro categorías. Tu puntuación personalizada cambia según las prioridades elegidas; los datos originales se conservan.")
        render_summary(view)
    with tabs[1]:
        render_services(services, view, map_data)
    with tabs[2]:
        render_housing(view)
    with tabs[3]:
        render_population(view)
    with tabs[6]:
        render_methodology(data, view)

def main():
    try:
        signature = snapshot_signature(ROOT)
        with st.spinner("Preparando los datos de Madrid…"):
            data = cached_data(str(ROOT), signature)
            services = cached_services(str(ROOT), signature)
            map_data = cached_map(str(ROOT), signature)
    except (IntegrationError, OSError, ValueError, KeyError):
        LOG.exception("No se ha podido cargar la edición verificada de HabitaMadrid")
        st.error("Los datos de esta edición no están disponibles o necesitan revisión. La ficha se mostrará cuando el conjunto vuelva a estar validado.")
        st.stop()
    retain_view_state(st.session_state)
    codes = initialize_selection(data)
    try:
        if restore_plan(st.query_params.get("plan", ""), st.session_state, codes):
            st.session_state['_active_view'] = 'fichas'
    except (ValueError, TypeError):
        st.warning("El enlace compartido no es válido. Puedes explorar los municipios con los controles de la página.")
    st.session_state.setdefault('_active_view', 'inicio')
    active = st.session_state['_active_view']
    render_site_navigation(reset_application, st.query_params)
    attributes = target_attributes(st.session_state, VIEW)
    st.html('<div '+attributes+' class="view-start"></div>')
    # Keep the existing map mounted at a stable path, even while hidden. Native
    # deck.gl retains its client camera across view changes and sheet searches.
    # CSS uses our own container keys, never generated Streamlit class names.
    st.html('<style>.st-key-map_view{'+('display:block;' if active == 'mapa' else 'display:none;')+'}'
            '.st-key-selector_view{'+('display:block;' if active in ('mapa','fichas','comparar') else 'display:none;')+'}</style>')
    with st.container(key='page_heading'):
        if active == 'inicio':
            render_header(data)
        else:
            st.html('<div class="view-heading"><p class="eyebrow">HabitaMadrid</p><h1>'+esc(VIEWS[active])+'</h1></div>')
    with st.container(key='selector_view'):
        code, profile = render_selector(data, codes)
    living = load_living_data(ROOT)
    view = municipal_view(data, code, profile) if code else None
    with st.container(key='map_view'):
        with st.container(key='map_preferences'):
            explorer_frame, filtered, mode = render_preferences(data, profile)
        with st.container(key='persistent_municipal_map'):
            render_map(data, map_data, view, explorer_frame, filtered, mode)
        if active == 'mapa':
            with st.expander('Consultar el ranking completo'):
                render_ranking(filtered, explorer_frame, profile, mode)
    if active == 'inicio':
        render_welcome(len(data['municipios']), change_view)
        render_top_five(ROOT, explorer_frame, profile, choose_municipality, home_housing_changed)
        render_home_sources(change_view)
    elif active == 'fichas':
        if code:
            render_sheet(data, services, map_data, living, view, explorer_frame, filtered, profile, code, mode)
        else:
            st.html('<div class="sheet-empty"><h2>Cada municipio tiene su historia</h2>'
                    '<p>Busca uno por su nombre para conocer sus servicios, vivienda, transporte y población.</p></div>')
            st.button('Prefiero explorar el mapa', key='sheet_go_map', on_click=change_view, args=('mapa',))
    elif active == 'ideal':
        st.session_state['_assistant_open'] = True
        render_assistant_entry()
        render_candidates(ROOT, data, explorer_frame, filtered, mode, choose_municipality, living)
    elif active == 'comparar':
        render_comparison(data, services, explorer_frame, profile, code, mode, choose_municipality)
    elif active == 'fuentes':
        render_methodology(data, view)
    st.html('<div class="footer-line"><span>HabitaMadrid · Datos abiertos para decisiones informadas.<br>Proyecto independiente · Índice orientativo propio.<br><a href="https://www.crtm.es/" target="_blank" rel="noopener noreferrer">Powered by CRTM</a> · Datos de transporte elaborados a partir del CRTM.</span>'
            '<span>Comunidad de Madrid · Municipios de hasta 50.000 habitantes<br>Edición de datos · 15 de octubre de 2026</span></div>')
    render_navigation()


def home_housing_changed():
    st.session_state['perfil_vivienda'] = st.session_state['home_housing']


if __name__ == "__main__":
    main()
