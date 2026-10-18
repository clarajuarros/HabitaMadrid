"""One-shot navigation, separate from map selection and camera state.

Requests survive the assistant's rerun. The command is emitted at the end of a
successful render, and only accepts targets owned by this application.
"""
from html import escape
import json
from pathlib import Path
from uuid import uuid4

import streamlit as st

SHEET = "ficha-municipal"
RESULTS = "resultados-personalizados"
HOME = "habita-inicio"
FORM = "descubre-tu-municipio"
VIEW = "habita-vista"
PENDING = "_navigation_pending"
SCRIPT = Path(__file__).resolve().parents[1] / "assets/navigation.js"


def request_navigation(state, target, municipality=""):
    if target not in (SHEET, RESULTS, HOME, FORM, VIEW):
        raise ValueError("Unknown navigation target")
    if target == SHEET and not (len(municipality) == 5 and municipality.isascii() and municipality.isdigit()):
        raise ValueError("A municipal sheet requires an INE code")
    destination = {SHEET: 'fichas', RESULTS: 'ideal', FORM: 'ideal', HOME: 'inicio'}.get(target)
    if destination:
        state['_active_view'] = destination
    state[PENDING] = {"id": uuid4().hex, "target": target, "municipality": municipality}


def target_attributes(state, target, municipality=""):
    """Mark the updated section, so an old municipality cannot satisfy a request."""
    pending = state.get(PENDING, {})
    token = pending.get("id", "") if (pending.get("target") == target
            and pending.get("municipality", "") == municipality) else ""
    return (f'id="{escape(target, quote=True)}" tabindex="-1" '
            f'data-habita-navigation="{escape(token, quote=True)}"')


def render_navigation():
    """Call after the sections and their contents have been emitted, never before."""
    request = st.session_state.get(PENDING)
    token = request["id"] if request else ""
    # All values come from the validated internal request, never URL/HTML input.
    command = json.dumps(request).replace("<", "\\u003c")
    script = SCRIPT.read_text(encoding="utf-8").replace("__HABITA_REQUEST__", command)
    st.html(f'<span id="habita-navigation-ready" data-request="{token}" hidden></span>'
            f'<script>{script}</script>', unsafe_allow_javascript=True)
    st.session_state.pop(PENDING, None)


VIEWS = {
    'inicio': 'Inicio', 'mapa': 'Explorar mapa', 'fichas': 'Fichas municipales',
    'ideal': 'Mi municipio ideal', 'comparar': 'Comparar municipios',
    'fuentes': 'Cómo se calcula y fuentes',
}


def change_view(view):
    if view not in VIEWS:
        raise ValueError('Unknown application view')
    st.session_state['mobile_menu'] = False
    st.session_state['_active_view'] = view
    if view == 'ideal':
        st.session_state['_assistant_open'] = True
    request_navigation(st.session_state, VIEW)


def retain_view_state(state):
    """Detach durable inputs from widget cleanup when their view is unmounted.

    Never assign button or read-only chart event states. Streamlit otherwise
    removes widget values belonging to the previous view at the end of a run.
    """
    fields = {'municipio_codigo', 'perfil_vivienda', 'modo_indice', 'indicador_mapa',
              'encuadre_mapa', 'busqueda_nombre', 'comparar_codigos',
              'vistas_municipales', 'localizador_servicios_abierto'}
    prefixes = ('peso_', 'filtro_', 'assistant_', 'timetable_', 'servicio_',
                'service_', 'localizador_')
    for key in list(state):
        if key in fields or key.startswith(prefixes):
            state[key] = state[key]


def render_site_navigation(reset, query):
    from src.app_identity import brand_symbol
    active = st.session_state.get('_active_view', 'inicio')
    with st.container(key='site_navigation', horizontal=True, vertical_alignment='center', gap='small'):
        with st.container(key='nav_identity', width=220):
            st.html('<div class="nav-lockup">'+brand_symbol()+'<span>Habita<strong>Madrid</strong></span></div>')
        with st.container(key='desktop_navigation', horizontal=True, gap='small'):
            for view, label in list(VIEWS.items())[:5]:
                st.button(label, key='nav_'+view, type='primary' if active == view else 'tertiary',
                          on_click=change_view, args=(view,))
        with st.container(key='mobile_navigation', width='content'):
            with st.popover('Menú', key='mobile_menu', width='content', on_change='rerun'):
                st.caption('Estás en: '+VIEWS[active])
                for view, label in VIEWS.items():
                    st.button(label, key='mobile_nav_'+view, width='stretch',
                              type='primary' if active == view else 'tertiary',
                              on_click=change_view, args=(view,))
        st.button('Reiniciar', key='reset_application', type='tertiary',
                  on_click=reset, args=(st.session_state, query),
                  help='Borra la selección, las respuestas, la comparación y los filtros.')
