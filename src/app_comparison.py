"""Comparaciones de 2–3 municipios sobre las tablas preparadas, sin imputación.

Los conteos son locales; las tres distancias conservan las observaciones
seleccionadas por el IVP, cuyos destinos pueden estar en otro municipio. Este
adaptador no modifica índices, referencias, pesos, fuentes o archivos.
"""
from __future__ import annotations

from collections.abc import Iterable
import html
import json
import math
import textwrap

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from src.app_charts import BACKGROUND, FONT, GREEN, GRID, MUTED
from src.app_data import DIMENSIONS, PROFILES, municipal_view
from src.ivp_calculation import normalize_weights


LABELS = {"sanidad": "Sanidad", "educacion": "Educación", "transporte": "Transporte", "vivienda": "Vivienda"}
MUNICIPAL_COLORS = ("#173d35", "#7b9c43", "#bb865b")
DIMENSION_COLORS = ("#173d35", "#59856b", "#91ac59", "#ccf15b")
HOUSING_YEAR = 2024


def _finite(value):
    if value is None or pd.isna(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _one(frame, label):
    if len(frame) > 1:
        raise ValueError(f"Registros duplicados en {label}.")
    return frame.iloc[0] if len(frame) else pd.Series(dtype=object)


def _column_metadata(data, profile):
    def source_period(source):
        dates = data["fuentes"].loc[data["fuentes"]["fuente"].eq(source), "fecha_descarga_utc"]
        return "Descarga " + str(dates.iloc[0])[:10] if len(dates) else "Fecha de vigencia no publicada"

    health_id = data["configuracion"]["indicadores_comunes"]["sanidad"]
    health_periods = data["observaciones"].loc[data["observaciones"]["indicador_id"].eq(health_id), "periodo"]
    health_period = ", ".join(sorted(set(health_periods.astype(str)))).replace("consulta_", "Consulta ")
    columns = {
        "codigo_ine": ("Código INE", "código", "", "Maestro municipal"),
        "municipio": ("Municipio", "", "", "Maestro municipal"),
        "poblacion": ("Población", "habitantes", "2025", "Padrón municipal"),
        "ivp_general": ("IVP general", "puntos / 100", "Fuentes de fechas distintas", "IVP general · min–max · pesos iguales"),
        "ivp_personalizado": ("IVP con tus pesos", "puntos / 100", "Fuentes de fechas distintas", "IVP personalizado · referencias fijas"),
        "alquiler_m2_mes": ("Alquiler declarado · mediana", "€/m²/mes", str(HOUSING_YEAR), "MIVAU · SERPAVI"),
        "alquiler_mes": ("Alquiler mensual declarado · mediana", "€/mes", str(HOUSING_YEAR), "MIVAU · SERPAVI"),
        "testigos_vivienda": ("Testigos de vivienda publicados", "testigos", str(HOUSING_YEAR), "MIVAU · SERPAVI"),
        "centros_salud_locales": ("Centros de salud y consultorios locales", "centros", health_period, "CM · directorio de atención primaria revisado"),
        "centros_educativos_locales_alta": ("Centros educativos locales en ALTA", "centros", source_period("centros_educativos"), "CM · catálogo educativo, todas las tipologías"),
        "paradas_bus_locales": ("Paradas locales de autobús interurbano", "paradas", source_period("crtm_interurbanos_gtfs"), "CRTM · cobertura parcial"),
        "crecimiento_2020_2025_pct": ("Crecimiento de población 2020–2025", "%", "2020–2025", "Padrón municipal"),
        "renta_disponible_per_capita_2023": ("Renta disponible por habitante", "€/habitante", "2023", "CM · renta disponible municipal"),
    }
    for dimension in DIMENSIONS:
        columns[f"puntuacion_{dimension}"] = (LABELS[dimension], "puntos / 100", "Fuentes de fechas distintas", "Normalización min–max de la fase 6")
    for dimension in DIMENSIONS[:3]:
        indicator = data["configuracion"]["indicadores_comunes"][dimension]
        observations = data["observaciones"].loc[data["observaciones"]["indicador_id"].eq(indicator)]
        periods = sorted(set(observations["periodo"].dropna().astype(str)))
        sources = sorted({source for value in observations["fuentes_json"] for source in json.loads(value)})
        columns[f"distancia_{dimension}_m"] = (
            f"Distancia IVP · {LABELS[dimension].lower()}", "m en línea recta", ", ".join(periods), ", ".join(sources),
        )
    return {key: {"etiqueta": label, "unidad": unit, "periodo": period, "fuente": source,
                  **({"perfil_vivienda": profile} if key in {"alquiler_m2_mes", "alquiler_mes", "testigos_vivienda", "puntuacion_vivienda"} else {})}
            for key, (label, unit, period, source) in columns.items()}


def build_comparison(data, services, codes, profile, explorer_frame=None):
    """Devuelve filas municipales en el orden elegido, con ausencias intactas.

    ``explorer_frame`` aporta el índice personalizado que ya calculó el motor;
    no se vuelve a calcular ni se sustituye por el general si falta. Sus filas
    pueden cubrir un subconjunto, pero no pueden duplicar un INE ni mezclar el
    perfil de vivienda. La tabla conserva los 2–3 municipios elegidos aunque no
    dispongan de alquiler o índice.
    """
    if profile not in PROFILES:
        raise ValueError("La tipología de vivienda debe ser VC o VU.")
    if isinstance(codes, (str, bytes)) or not isinstance(codes, Iterable):
        raise ValueError("Elige 2–3 códigos municipales únicos.")
    codes = list(codes)
    if not 2 <= len(codes) <= 3 or any(not isinstance(code, str) for code in codes) or len(set(codes)) != len(codes):
        raise ValueError("Elige 2–3 códigos municipales únicos.")
    eligible = set(data["municipios"]["codigo_ine"])
    if not set(codes).issubset(eligible):
        raise ValueError("Municipio desconocido o fuera del ámbito de hasta 50.000 habitantes.")
    if explorer_frame is not None:
        required = {"codigo_ine", "ivp_personalizado"}
        if not isinstance(explorer_frame, pd.DataFrame) or not required.issubset(explorer_frame.columns):
            raise ValueError("El explorador debe aportar código e IVP personalizado.")
        if explorer_frame["codigo_ine"].duplicated().any():
            raise ValueError("Códigos duplicados en el explorador personalizado.")
        if "perfil_vivienda" in explorer_frame and not explorer_frame["perfil_vivienda"].eq(profile).all():
            raise ValueError("El perfil del explorador no coincide con la comparación.")
        personalized = explorer_frame.set_index("codigo_ine")["ivp_personalizado"]
    else:
        personalized = pd.Series(dtype=float)
    counts = {kind: services[kind].groupby("codigo_ine").size() for kind in ("sanidad", "educacion", "transporte")}
    rows = []
    for code in codes:
        view = municipal_view(data, code, profile)
        master, index = view["municipio"], view["index_row"]
        row = {"codigo_ine": code, "municipio": master["municipio"], "poblacion": master["poblacion"],
               "ivp_general": index["valor_indice"], "ivp_personalizado": personalized.get(code, np.nan)}
        row.update({f"puntuacion_{d}": index[f"puntuacion_{d}"] for d in DIMENSIONS})
        history = view["vivienda_historico"]
        housing = _one(history.loc[history["anio"].eq(HOUSING_YEAR)], "vivienda 2024")
        row.update({target: housing.get(original, np.nan) for target, original in {
            "alquiler_m2_mes": "alquiler_m2_mes_mediana", "alquiler_mes": "alquiler_mes_mediana", "testigos_vivienda": "testigos",
        }.items()})
        row.update({"centros_salud_locales": int(counts["sanidad"].get(code, 0)),
                    "centros_educativos_locales_alta": int(counts["educacion"].get(code, 0)),
                    "paradas_bus_locales": int(counts["transporte"].get(code, 0))})
        for dimension in DIMENSIONS[:3]:
            indicator = data["configuracion"]["indicadores_comunes"][dimension]
            observation = _one(view["observaciones"].loc[view["observaciones"]["indicador_id"].eq(indicator)], "observaciones del IVP")
            row[f"distancia_{dimension}_m"] = observation.get("valor", np.nan)
        for field in ("crecimiento_2020_2025_pct", "renta_disponible_per_capita_2023"):
            row[field] = view["contexto_demografico"].get(field, np.nan)
        rows.append(row)
    result = pd.DataFrame(rows)
    result.attrs = {
        "perfil_vivienda": profile, "columnas": _column_metadata(data, profile),
        "normalizacion": "Min–max de la fase 6; referencias fijas sobre los 155 municipios, sin recalcular tras seleccionar.",
        "limitaciones": [
            "Índice orientativo propio de proximidad y coste; no mide calidad de vida oficial.",
            "Las distancias IVP son rectas desde el origen municipal, pueden llegar a otros municipios y no representan tiempos de viaje.",
            "El conteo educativo local incluye todas las tipologías en ALTA; la distancia IVP utiliza únicamente las diez tipologías seleccionadas.",
            "Sanidad cuenta centros de salud y consultorios del directorio revisado; no acredita horarios, capacidad o atención asignada.",
            "Las paradas de autobús proceden de una fuente parcial interurbana; no miden frecuencia ni servicio actual.",
            "El alquiler declarado SERPAVI de 2024 mantiene VC y VU separadas; no es una oferta actual ni se sustituye una tipología por otra.",
            "Los testigos son el recuento publicado; un precio ausente permanece ausente aunque el recuento sea cero.",
            "La fecha de descarga de un catálogo no acredita su vigencia, un horario o una disponibilidad actuales.",
        ],
    }
    return result


def _figure(title, height=370):
    figure = go.Figure()
    figure.update_layout(title={"text": title, "font": {"size": 17}, "x": 0}, height=height,
                          paper_bgcolor=BACKGROUND, plot_bgcolor=BACKGROUND,
                          font={"family": FONT, "color": GREEN, "size": 12}, separators=",.",
                          margin={"l": 10, "r": 20, "t": 80, "b": 75},
                          legend={"orientation": "h", "y": 1.15, "x": 0},
                          hoverlabel={"bgcolor": GREEN, "font_color": BACKGROUND, "font_family": FONT})
    figure.update_xaxes(zeroline=False, showgrid=False, automargin=True)
    figure.update_yaxes(zeroline=False, gridcolor=GRID, automargin=True)
    return figure


def _notes(figure, lines):
    """Wrap explanatory labels so missing data stays readable on small screens."""
    wrapped = [part for line in lines for part in textwrap.wrap(line, width=45)]
    figure.add_annotation(text="<br>".join(html.escape(part) for part in wrapped), x=0, y=-0.25,
                          xref="paper", yref="paper", xanchor="left", showarrow=False, align="left",
                          font={"color": MUTED, "size": 11})
    figure.update_layout(margin={"b": 70 + 15 * len(wrapped)})


def _chart_frame(frame):
    required = {"codigo_ine", "municipio"} | {f"puntuacion_{d}" for d in DIMENSIONS}
    if not isinstance(frame, pd.DataFrame) or not required.issubset(frame.columns):
        raise ValueError("Faltan columnas de la comparación.")
    if not 2 <= len(frame) <= 3 or frame["codigo_ine"].duplicated().any():
        raise ValueError("El gráfico requiere 2–3 municipios únicos.")


def comparison_chart(frame):
    """Barras agrupadas de las cuatro puntuaciones, sin convertir ausencias a 0."""
    _chart_frame(frame)
    figure = _figure("Compara las cuatro dimensiones")
    categories = [LABELS[d] for d in DIMENSIONS]
    for number, row in enumerate(frame.to_dict("records")):
        name = html.escape(str(row["municipio"]))
        values = [_finite(row[f"puntuacion_{d}"]) for d in DIMENSIONS]
        figure.add_trace(go.Bar(name=name, x=categories, y=values,
                                 marker_color=MUNICIPAL_COLORS[number],
                                 text=["Sin dato" if v is None else f"{v:.1f}" for v in values],
                                 textposition="outside", cliponaxis=False,
                                 hovertemplate="%{x}: %{y:.1f} / 100<extra>" + name + "</extra>"))
    missing = ["Sin dato · " + str(row["municipio"]) + ": " + ", ".join(LABELS[d] for d in DIMENSIONS if _finite(row[f"puntuacion_{d}"]) is None)
               for row in frame.to_dict("records") if any(_finite(row[f"puntuacion_{d}"]) is None for d in DIMENSIONS)]
    if missing:
        _notes(figure, missing)
    figure.update_layout(barmode="group")
    figure.update_xaxes(categoryorder="array", categoryarray=categories)
    figure.update_yaxes(range=[0, 108], tickvals=[0, 25, 50, 75, 100], title="Puntuación · 0–100")
    return figure


def contribution_chart(frame, weights):
    """Desglosa únicamente índices calculables con los pesos elegidos.

    Cada segmento vale puntuación × peso global normalizado. Las dimensiones
    con peso cero quedan excluidas de la suma incluso si falta su observación.
    Nunca dibuja la suma incompleta de un municipio sin índice como si fuera un
    IVP. El título y las notas identifican el alcance parcial al excluir ejes.
    """
    _chart_frame(frame)
    weights = normalize_weights(weights)
    active = [d for d in DIMENSIONS if weights[d] > 0]
    partial = len(active) != len(DIMENSIONS)
    title = "Contribuciones al IVP<br>Personalizado" + (" · alcance parcial" if partial else "")
    figure = _figure(title, height=350)
    # An explicit break keeps both complete and partial titles readable inside
    # the narrow Plotly canvas on mobile; the legend sits below both lines.
    figure.update_layout(margin={"t": 115})
    rows = frame.to_dict("records")
    available = [row for row in rows if all(_finite(row[f"puntuacion_{d}"]) is not None for d in active)]
    names = [html.escape(str(row["municipio"])) for row in available]
    if available:
        for dimension, color in zip(DIMENSIONS, DIMENSION_COLORS):
            if dimension not in active:
                continue
            figure.add_trace(go.Bar(name=LABELS[dimension], x=names,
                                     y=[float(row[f"puntuacion_{dimension}"]) * weights[dimension] for row in available],
                                     marker_color=color,
                                     hovertemplate="%{x}<br>Contribución: %{y:.2f} puntos<extra>" + LABELS[dimension] + "</extra>"))
    else:
        figure.add_annotation(text="Sin índice calculable con estos pesos", x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False)
    notes = []
    available_codes = {row["codigo_ine"] for row in available}
    absent = [str(row["municipio"]) for row in rows if row["codigo_ine"] not in available_codes]
    if absent:
        notes.append("Sin índice por datos ausentes: " + ", ".join(absent))
    if partial:
        notes.append("Alcance parcial · peso 0: " + ", ".join(LABELS[d] for d in DIMENSIONS if d not in active))
    if notes:
        _notes(figure, notes)
    figure.update_layout(barmode="stack")
    figure.update_yaxes(range=[0, 108], tickvals=[0, 25, 50, 75, 100], title="Contribución ponderada · puntos")
    return figure
