"""Controles y ranking de la fase 9 sobre el motor verificado del IVP."""
import hashlib
import json

import streamlit as st

from src.app_explorer import build_explorer_frame, filter_explorer, ranking_view
from src.app_presenter import DIMENSION_LABELS, PROFILE_LABELS, esc, number
from src.app_selection import apply_ranking_selection
from src.app_navigation import SHEET, request_navigation
from src.ivp_calculation import DIMENSIONS


MODE_LABELS = {"general": "General · misma importancia", "personalizado": "Personalizado · tus prioridades"}


def _restore_general():
    if st.session_state.get("modo_indice") == "general":
        for dimension in DIMENSIONS:
            st.session_state["peso_" + dimension] = 25


def _reset_priorities():
    st.session_state["modo_indice"] = "general"
    _restore_general()


def _reset_filters():
    st.session_state["filtro_poblacion"] = (0, 50000)
    st.session_state["filtro_nombre"] = ""
    st.session_state["filtro_solo_indice"] = False
    for key in ("alquiler_max", "sanidad_km", "educacion_km", "transporte_km"):
        st.session_state["filtro_" + key] = None


def render_preferences(data, profile):
    """Devuelve escenario completo, subconjunto y modo; nunca escribe datasets."""
    for dimension in DIMENSIONS:
        st.session_state.setdefault("peso_" + dimension, 25)
    st.session_state.setdefault("filtro_poblacion", (0, 50000))
    with st.expander("Prioridades y filtros", expanded=False):
        st.radio("Índice para explorar", ["general", "personalizado"],
                 format_func=lambda key: MODE_LABELS[key], key="modo_indice",
                 horizontal=True, on_change=_restore_general)
        mode = st.session_state["modo_indice"]
        columns = st.columns(4)
        for column, dimension in zip(columns, DIMENSIONS):
            with column:
                st.slider(DIMENSION_LABELS[dimension], 0, 100,
                          key="peso_" + dimension, disabled=mode == "general",
                          help="Cuanto mayor sea el número, más influye esta categoría en tu resultado. Con cero deja de contar.")
        st.caption("Elige Personalizado para dar más importancia a unas categorías que a otras. Los porcentajes suman 100 %. En General, cada categoría cuenta un 25 %.")
        st.button("Restablecer prioridades", key="restablecer_prioridades", on_click=_reset_priorities)
        st.markdown("**Acota los resultados**")
        st.slider("Población · habitantes (2025)", 0, 50000, key="filtro_poblacion")
        left, right = st.columns(2)
        with left:
            st.text_input("Buscar en los resultados", key="filtro_nombre", placeholder="Nombre o código INE")
            st.number_input("Alquiler máximo · €/m²/mes (2024)", min_value=0.0, max_value=1000.0,
                            value=None, step=0.25, key="filtro_alquiler_max", placeholder="Sin límite")
            st.number_input("Atención primaria a un máximo de… km", min_value=0.0, max_value=200.0,
                            value=None, step=0.5, key="filtro_sanidad_km", placeholder="Sin límite")
        with right:
            st.number_input("Centro educativo a un máximo de… km", min_value=0.0, max_value=200.0,
                            value=None, step=0.5, key="filtro_educacion_km", placeholder="Sin límite")
            st.number_input("Parada de bus interurbano a un máximo de… km", min_value=0.0, max_value=200.0,
                            value=None, step=0.25, key="filtro_transporte_km", placeholder="Sin límite")
            st.checkbox("Solo municipios con índice en el modo elegido", key="filtro_solo_indice")
        st.caption("Se incluyen los valores iguales al límite. El alquiler es declarado, para la tipología elegida. Las distancias son en línea recta desde el núcleo principal; el bus procede de un catálogo parcial. Un filtro activo exige un dato publicado.")
        st.button("Quitar filtros", key="quitar_filtros", on_click=_reset_filters)

    full, filtered, mode = build_scenario(data, profile)
    normalized = full.attrs["pesos_normalizados"]
    weights_text = " · ".join(DIMENSION_LABELS[d] + " " + number(100 * normalized[d], 1) + " %" for d in DIMENSIONS)
    score_column = "ivp_personalizado" if mode == "personalizado" else "ivp_general"
    st.html(f'<div class="explorer-status"><strong>{len(filtered)} de 155 municipios</strong>'
            f'<span>{int(filtered[score_column].notna().sum())} con índice · {esc(MODE_LABELS[mode])} · {esc(PROFILE_LABELS[profile])}</span></div>')
    if mode == "personalizado":
        st.caption("Importancia de cada categoría: " + weights_text)
        if len(full.attrs["dimensiones_activas"]) < 4:
            st.warning("Índice personalizado de alcance parcial: " + str(len(full.attrs["dimensiones_activas"]))
                       + " dimensiones activas. Las dimensiones con peso cero quedan fuera; los datos ausentes no se rellenan.")
    if filtered.empty:
        st.info("No hay municipios que cumplan estos filtros. Amplía los límites o pulsa Quitar filtros.")
    return full, filtered, mode


def build_scenario(data, profile):
    """Compute the same scenario without mounting controls in every view."""
    defaults = {'modo_indice':'general', 'filtro_poblacion':(0,50000),
                'filtro_nombre':'', 'filtro_solo_indice':False}
    for dimension in DIMENSIONS:
        defaults['peso_'+dimension] = 25
    for limit in ('alquiler_max','sanidad_km','educacion_km','transporte_km'):
        defaults['filtro_'+limit] = None
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)
    mode = st.session_state["modo_indice"]
    weights = {dimension: st.session_state["peso_" + dimension] for dimension in DIMENSIONS}
    if not any(weights.values()):
        st.error("Activa al menos una prioridad para calcular el IVP personalizado, o vuelve al modo General.")
        st.stop()
    full = build_explorer_frame(data, profile, weights)
    filtered = filter_explorer(
        full, population_range=st.session_state["filtro_poblacion"],
        max_rent=st.session_state["filtro_alquiler_max"],
        max_health_distance_m=_meters("sanidad"), max_education_distance_m=_meters("educacion"),
        max_transport_distance_m=_meters("transporte"), query=st.session_state["filtro_nombre"],
        only_with_index=st.session_state["filtro_solo_indice"], mode=mode,
    )
    return full, filtered, mode


def _meters(dimension):
    value = st.session_state["filtro_" + dimension + "_km"]
    return None if value is None else value * 1000


def render_ranking(filtered, full, profile, mode):
    ranking = ranking_view(filtered, mode)
    st.markdown("### Encuentra los municipios que encajan contigo")
    st.caption("Pulsa una fila para abrir su ficha. El puesto global usa los 155 municipios de referencia; el puesto en resultados solo cuenta los que cumplen tus filtros. Los empates comparten puesto, antes de redondear.")
    if ranking.empty:
        st.info("Sin resultados con estos criterios. Modifica los filtros del panel superior.")
        return
    indices = ["ivp_personalizado", "ivp_general"] if mode == "personalizado" else ["ivp_general", "ivp_personalizado"]
    display = ranking[["municipio", *indices, "posicion_filtrada", "posicion_global", "codigo_ine",
                       "poblacion", "alquiler_m2_mes", "fortaleza", "debilidad"]].copy()
    display["Alcance personalizado"] = ranking["alcance_personalizado"].map({
        "cuatro_dimensiones": "4 dimensiones", "parcial_dimensiones_seleccionadas": "Parcial · " + str(len(full.attrs["dimensiones_activas"])) + " dimensiones"})
    display["Estado del índice elegido"] = ranking["estado_activo"].map(lambda value: "Con índice" if value == "disponible" else "Sin índice · falta dato o revisión")
    display = display.rename(columns={"posicion_filtrada": "Puesto en resultados", "posicion_global": "Puesto global",
        "municipio": "Municipio", "codigo_ine": "INE", "ivp_general": "IVP general",
        "ivp_personalizado": "IVP personalizado", "poblacion": "Habitantes (2025)",
        "alquiler_m2_mes": "Alquiler €/m²/mes (2024)", "fortaleza": "Mayor puntuación relativa", "debilidad": "Menor puntuación relativa"})
    revision = st.session_state.get("_ranking_revision", 0)
    signature = hashlib.sha256((ranking[["codigo_ine", "valor_activo"]].to_json() + mode + profile).encode()).hexdigest()[:12]
    event_key = f"ranking_municipios_{signature}_{revision}"
    ordered_codes = tuple(ranking["codigo_ine"])

    def select_row():
        if apply_ranking_selection(st.session_state, st.query_params, ordered_codes, event_key=event_key):
            request_navigation(st.session_state, SHEET, st.session_state["municipio_codigo"])
        st.session_state["_ranking_revision"] = st.session_state.get("_ranking_revision", 0) + 1

    st.dataframe(display, hide_index=True, width="stretch", height=440,
                 column_config={"Municipio": st.column_config.TextColumn(width="medium", pinned=True),
                                "IVP general": st.column_config.NumberColumn(format="%.2f"),
                                "IVP personalizado": st.column_config.NumberColumn(format="%.2f"),
                                "Alquiler €/m²/mes (2024)": st.column_config.NumberColumn(format="%.2f")},
                 key=event_key, on_select=select_row, selection_mode="single-row")
    st.caption("Estas columnas muestran en qué categorías obtiene mayor y menor puntuación cada municipio. En tu recomendación solo cuentan las categorías que hayas activado. Las puntuaciones no garantizan plazas, horarios ni calidad del servicio. Si falta información para calcular el IVP, no asignamos puesto.")
    export = ranking.copy()
    export["modo_activo"] = mode
    export["filtros_json"] = json.dumps(filtered.attrs.get("filtros", {}), ensure_ascii=False)
    export["alquiler_anio"] = 2024
    export["consulta_geografica"] = "2026-10-15"
    export["normalizacion"] = full.attrs["normalizacion"]
    export["anclas_fijas"] = full.attrs["anclas_fijas"]
    export["metodologia_version"] = full.attrs["metodologia_version"]
    st.download_button("Descargar este ranking", export.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"HabitaMadrid_ranking_{profile}_{mode}.csv", mime="text/csv", on_click="ignore")
