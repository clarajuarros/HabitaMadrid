"""Controles y vistas Streamlit para comparar municipios sin aplicar filtros."""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from src.app_comparison import build_comparison, comparison_chart, contribution_chart
from src.app_data import DIMENSIONS
from src.app_presenter import DIMENSION_LABELS, PROFILE_LABELS, number, raw_item
from src.ivp_calculation import normalize_weights
from src.app_living_data import load_living_data
from src.app_living_ui import render_report_actions


CHART_CONFIG = {"displayModeBar": False, "responsive": True}


def _table(frame, fields):
    """An explicit text table preserves Spanish formatting and missing labels."""
    rows = []
    for field in fields:
        metadata = frame.attrs["columnas"][field]
        label = metadata["etiqueta"]
        detail = " · ".join(value for value in (metadata["unidad"], metadata["periodo"]) if value)
        row = {"Dato y referencia": label + (" · " + detail if detail else "")}
        decimals = 0 if field in {"poblacion", "testigos_vivienda", "centros_salud_locales", "centros_educativos_locales_alta", "paradas_bus_locales", "renta_disponible_per_capita_2023"} else 2
        text_field = field in {"estado_cobertura", "nota_cobertura", "inicio_semana", "fin_semana"}
        row.update({municipality["municipio"]: (str(municipality[field]).replace("_", " ") if text_field and pd.notna(municipality[field])
                                               else number(municipality[field], decimals)) for municipality in frame.to_dict("records")})
        rows.append(row)
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                 height=min(430, 38 + 36 * len(rows)),
                 column_config={"Dato y referencia": st.column_config.TextColumn(width="medium"),
                                **{name: st.column_config.TextColumn(width="small") for name in frame["municipio"]}})


def _download(frame, profile, mode, weights, personal_weights, scope, personal_scope, methodology):
    export = frame.copy()
    export["perfil_vivienda"] = profile
    export["modo_indice"] = mode
    export["pesos_json"] = json.dumps(weights, ensure_ascii=False, allow_nan=False)
    export["alcance"] = scope
    export["pesos_personalizados_json"] = json.dumps(personal_weights, ensure_ascii=False, allow_nan=False)
    export["alcance_personalizado"] = personal_scope
    export["normalizacion"] = "minmax"
    export["anclas_fijas"] = True
    export["metodologia_version"] = methodology
    export["unidades_json"] = json.dumps({key: meta["unidad"] for key, meta in frame.attrs["columnas"].items()}, ensure_ascii=False)
    export["periodos_json"] = json.dumps({key: meta["periodo"] for key, meta in frame.attrs["columnas"].items()}, ensure_ascii=False)
    return export.to_csv(index=False).encode("utf-8-sig")


def render_comparison(data, services, explorer_frame, profile, selected_code, mode, choose=None):
    """Render the comparison using the complete, unfiltered explorer snapshot.

    The comparison selection has independent state; adding the municipality of
    the main sheet never changes the search, housing profile or ranking filters.
    """
    mode = str(mode).casefold()
    mode = "personalizado" if mode == "personal" else mode
    if mode not in {"general", "personalizado"}:
        raise ValueError("El modo debe ser general o personalizado.")
    municipalities = data["municipios"].sort_values(["municipio", "codigo_ine"], kind="stable")
    names = municipalities.set_index("codigo_ine")["municipio"].to_dict()
    allowed = list(names)
    defaults = []
    key = "comparar_codigos"
    if key not in st.session_state:
        st.session_state[key] = defaults
    existing = st.session_state[key]
    cleaned = list(dict.fromkeys(code for code in existing if code in names))[:3]
    if cleaned != existing:
        st.session_state[key] = cleaned

    def add_current():
        selected = list(st.session_state.get(key, []))
        if selected_code in names and selected_code not in selected and len(selected) < 3:
            st.session_state[key] = selected + [selected_code]

    st.html('<div class="section-heading"><h3>Pon tus opciones frente a frente</h3><span class="section-tag">2 o 3 municipios</span></div>')
    st.caption("La comparación es independiente de los filtros del ranking. Puedes elegir cualquiera de los 155 municipios; se mantiene la tipología de vivienda de la ficha.")
    st.button("Añadir municipio de la ficha", key="comparar_anadir_ficha", on_click=add_current,
              disabled=selected_code not in names or selected_code in cleaned or len(cleaned) >= 3,
              help="Añade la ficha actual si queda espacio, sin cambiar los municipios ya elegidos.")
    codes = st.multiselect("Municipios para comparar", options=allowed, format_func=names.get,
                           key=key, max_selections=3, placeholder="Elige al menos dos municipios")
    if len(codes) < 2:
        st.info("Elige al menos dos municipios para ver la comparación. Puedes seleccionar hasta tres.")
        return

    frame = build_comparison(data, services, codes, profile, explorer_frame)
    personal_weights = normalize_weights(explorer_frame.attrs["pesos_normalizados"])
    personal_scope = "parcial_dimensiones_seleccionadas" if any(value == 0 for value in personal_weights.values()) else "cuatro_dimensiones"
    weights = personal_weights if mode == "personalizado" else {dimension: 0.25 for dimension in DIMENSIONS}
    scope = personal_scope if mode == "personalizado" else "cuatro_dimensiones"
    partial = scope != "cuatro_dimensiones"
    st.caption(PROFILE_LABELS[profile] + " · " + ("IVP personalizado" if mode == "personalizado" else "IVP general · pesos iguales"))
    if partial:
        excluded = ", ".join(DIMENSION_LABELS[dimension] for dimension, value in weights.items() if value == 0)
        st.warning("Alcance parcial: has excluido " + excluded + " con peso cero. Este escenario no representa el IVP general de cuatro dimensiones.")

    for column, row in zip(st.columns(len(frame), gap="medium"), frame.to_dict("records")):
        with column, st.container(border=True):
            st.markdown("**" + row["municipio"] + "**")
            st.caption("INE " + row["codigo_ine"])
            st.html(raw_item("IVP general · 25 % por dimensión", number(row["ivp_general"], 1), "Escala 0–100 · cuatro dimensiones"))
            if mode == "personalizado":
                st.html(raw_item("IVP con tus pesos", number(row["ivp_personalizado"], 1), "Escala 0–100 · " + ("alcance parcial" if partial else "cuatro dimensiones")))
            st.caption("Habitantes (2025): " + number(row["poblacion"]))
            if choose is not None:
                st.button("Abrir ficha de " + row["municipio"], key="compare_open_" + row["codigo_ine"],
                          on_click=choose, args=(row["codigo_ine"],), width="stretch")

    st.plotly_chart(comparison_chart(frame), width="stretch", theme=None, config=CHART_CONFIG,
                    key="comparar_dimensiones", alt="Barras agrupadas de las cuatro puntuaciones para los municipios elegidos; los valores ausentes se indican como sin dato.")
    st.caption("Cada barra usa una escala común de 0 a 100. Cambiar la importancia de las categorías modifica tu IVP personalizado, pero conserva los datos y las puntuaciones de cada categoría. Puedes ver las cifras en la tabla inferior.")
    if mode == "personalizado":
        st.plotly_chart(contribution_chart(frame, weights), width="stretch", theme=None, config=CHART_CONFIG,
                        key="comparar_contribuciones", alt="Descomposición del IVP personalizado en puntuación por peso; solo se apilan municipios con todas las dimensiones activas disponibles.")
        st.caption("Contribución = puntuación × peso normalizado. No se suman datos incompletos ni se redistribuye el peso de un dato ausente.")

    st.markdown("**Índices, puntuaciones y vivienda**")
    st.caption("Desliza las tablas horizontalmente para consultar todas las columnas; puedes ampliar o redimensionar cada tabla.")
    fields = ["ivp_general"] + (["ivp_personalizado"] if mode == "personalizado" else [])
    fields += [f"puntuacion_{dimension}" for dimension in DIMENSIONS] + ["alquiler_m2_mes", "alquiler_mes", "testigos_vivienda"]
    _table(frame, fields)
    st.caption("SERPAVI 2024 describe alquiler declarado de vivienda habitual, no anuncios actuales. VC y VU conservan sus estadísticas separadas. Sin dato no equivale a cero.")

    st.markdown("**Servicios dentro de cada municipio**")
    _table(frame, ["centros_salud_locales", "centros_educativos_locales_alta", "paradas_bus_locales"])
    st.caption("Sanidad: centros de salud y consultorios del directorio revisado. Educación: todas las tipologías en ALTA, incluidos centros sin posición operativa. Autobús interurbano: fuente parcial.")

    scheduled_transport = frame.attrs.get("transporte_oferta_programada", False)
    if scheduled_transport:
        st.markdown("**Transporte programado y conexiones directas**")
        _table(frame, ["expediciones_dia", "destinos_directos_dia", "expediciones_laborable", "expediciones_sabado", "expediciones_domingo"])
        st.caption("Media diaria de expediciones intermunicipales y destinos municipales accesibles sin cambiar de vehículo. Semana del 19 al 25 de octubre de 2026. Cada viaje se cuenta una sola vez por municipio. Son horarios programados, sin transbordos ni seguimiento en tiempo real.")
        _table(frame, ["estado_cobertura", "nota_cobertura"])
        st.caption("Las cifras de las fuentes disponibles pueden ser parciales. Cuando la cobertura no permite comparar con rigor, transporte queda sin puntuación; una ausencia de datos no se convierte en cero servicio.")
    st.markdown("**Distancias de acceso a servicios**" if scheduled_transport else "**Distancias que utiliza el IVP**")
    _table(frame, [f"distancia_{dimension}_m" for dimension in (DIMENSIONS[:2] if scheduled_transport else DIMENSIONS[:3])])
    st.caption("Son metros en línea recta desde el núcleo principal; el destino puede estar fuera del municipio. Educación utiliza las diez tipologías seleccionadas por el modelo. No son tiempos de viaje ni prueban horarios, plazas o capacidad.")
    if scheduled_transport:
        _table(frame, ["distancia_transporte_m"])
        st.caption("La distancia a una parada interurbana se mantiene como contexto auxiliar para el filtro de proximidad. No interviene en la puntuación de transporte.")

    st.markdown("**Población y renta de contexto**")
    _table(frame, ["poblacion", "crecimiento_2020_2025_pct", "renta_disponible_per_capita_2023"])
    st.caption("La población y la renta aportan contexto; no se añaden como nuevas dimensiones del IVP.")
    st.download_button("Descargar comparación · CSV", data=_download(frame, profile, mode, weights, personal_weights, scope,
                        personal_scope, data["configuracion"].get("metodologia_version", "")),
                       file_name="HabitaMadrid_comparacion_" + profile + ".csv", mime="text/csv", key="comparar_descarga", on_click="ignore")
    render_report_actions(data["root"], data, explorer_frame, mode, codes, load_living_data(data["root"]), key="comparison")
    with st.expander("Fuentes, fechas y límites de esta comparación"):
        st.caption(frame.attrs["normalizacion"])
        for note in frame.attrs["limitaciones"]:
            st.write("• " + note)
        metadata = pd.DataFrame([{"Dato": value["etiqueta"], "Unidad": value["unidad"], "Referencia": value["periodo"], "Fuente": value["fuente"]}
                                 for value in frame.attrs["columnas"].values()])
        st.dataframe(metadata, hide_index=True, width="stretch")
