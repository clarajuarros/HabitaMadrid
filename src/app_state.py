"""Explicit user-state resets. Cached/persistent datasets are never touched."""
from src.app_navigation import HOME, FORM, request_navigation


def clear_assistant(state):
    state.update(assistant_profile=None, assistant_population='Cualquier tamaño',
                 assistant_priorities=[], assistant_budget=None,
                 _assistant_results=False, _assistant_open=True)
    state.pop('_assistant_error', None)
    request_navigation(state, FORM)


def close_sheet(state, query):
    state['municipio_codigo'] = None
    state['busqueda_nombre'] = ''
    state['_mapa_pick_revision'] = state.get('_mapa_pick_revision', 0) + 1
    query.pop('municipio', None)
    # A shared plan must not restore a sheet after explicitly closing it.
    query.pop('plan', None)
    state['_last_query'] = ('', query.get('vivienda', ''))
    request_navigation(state, HOME)


def reset_application(state, query):
    revision = state.get('_mapa_municipios_revision', 0) + 1
    keys = {'municipio_codigo', 'perfil_vivienda', 'modo_indice', 'indicador_mapa',
            'encuadre_mapa', 'busqueda_nombre', 'comparar_codigos', 'vistas_municipales',
            'vistas_inicio', 'home_housing', '_last_query', '_restored_plan', '_navigation_pending'}
    prefixes = ('peso_', 'filtro_', 'assistant_', '_assistant_', '_mapa_',
                '_ranking_', 'ranking_municipios_', 'mapa_municipios_', 'timetable_',
                'servicio_', 'service_', 'mapa_servicios_', 'localizador_')
    for key in list(state):
        if key in keys or key.startswith(prefixes):
            del state[key]
    for key in ('municipio', 'vivienda', 'plan'):
        query.pop(key, None)
    # Assign values as well as removing obsolete keys: a real browser can send
    # the previous values of widgets that keep their identity on this rerun.
    state.update(municipio_codigo=None, perfil_vivienda='VU', modo_indice='general',
                 indicador_mapa='ivp', encuadre_mapa='region', busqueda_nombre='',
                 comparar_codigos=[], filtro_poblacion=(0,50000), filtro_nombre='',
                 filtro_solo_indice=False, _assistant_open=False, _assistant_results=False,
                 assistant_profile=None, assistant_population='Cualquier tamaño',
                 assistant_priorities=[], assistant_budget=None,
                 localizador_servicios_abierto=False, _active_view='inicio', mobile_menu=False)
    for dimension in ('sanidad','educacion','transporte','vivienda'):
        state['peso_'+dimension] = 25
    for limit in ('alquiler_max','sanidad_km','educacion_km','transporte_km'):
        state['filtro_'+limit] = None
    state['_mapa_municipios_revision'] = revision
    request_navigation(state, HOME)
