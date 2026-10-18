"""Pure Plotly views over the published HabitaMadrid data tables.

These helpers deliberately do not load files, cache data, calculate an IVP,
or replace missing observations. Streamlit controls the inputs and rendering.
"""

from __future__ import annotations

import math

import pandas as pd
import plotly.graph_objects as go


BACKGROUND = "#f5f7f2"
GREEN = "#173d35"
ACCENT = "#ccf15b"
MUTED = "#64766d"
GRID = "#dde4dc"
FONT = "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"

DIMENSIONS = (
    ("sanidad", "Sanidad"),
    ("educacion", "Educación"),
    ("transporte", "Transporte"),
    ("vivienda", "Vivienda"),
)
HOUSING_LABELS = {"VC": "Colectiva", "VU": "Unifamiliar o rural"}
METHOD_LABELS = {
    "minmax": "Min–max",
    "percentil": "Percentiles",
    "winsor_p05_p95": "Min–max acotado P5–P95",
}


def _figure(title: str, *, height: int = 310) -> go.Figure:
    figure = go.Figure()
    figure.update_layout(
        title={"text": title, "font": {"size": 17}, "x": 0, "xanchor": "left"},
        height=height,
        paper_bgcolor=BACKGROUND,
        plot_bgcolor=BACKGROUND,
        font={"family": FONT, "color": GREEN, "size": 12},
        margin={"l": 10, "r": 20, "t": 65, "b": 48},
        showlegend=False,
        separators=",.",
        hoverlabel={"bgcolor": GREEN, "font_color": BACKGROUND, "font_family": FONT},
    )
    figure.update_xaxes(showgrid=False, zeroline=False, automargin=True)
    figure.update_yaxes(gridcolor=GRID, zeroline=False, automargin=True)
    return figure


def _empty(title: str, message: str) -> go.Figure:
    figure = _figure(title)
    figure.update_xaxes(visible=False)
    figure.update_yaxes(visible=False)
    figure.add_annotation(
        text=message,
        x=0.5,
        y=0.5,
        xref="paper",
        yref="paper",
        showarrow=False,
        font={"color": MUTED, "size": 14},
        align="center",
    )
    return figure


def _number(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _municipal_rows(frame: pd.DataFrame, code: str, required: set[str]) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    missing_columns = required.difference(frame.columns)
    if missing_columns:
        raise ValueError(f"Faltan columnas del gráfico: {', '.join(sorted(missing_columns))}")
    codes = frame["codigo_ine"].astype("string").str.strip().str.zfill(5)
    return frame.loc[codes.eq(str(code).strip().zfill(5))].copy()


def _annual_series(frame: pd.DataFrame, value_column: str) -> tuple[list[int], list[float | None]]:
    """Insert explicit gaps only within the observed annual date range."""
    years = pd.to_numeric(frame["anio"], errors="coerce")
    if years.isna().any() or (years % 1 != 0).any():
        raise ValueError("El histórico debe contener años enteros válidos")
    series_frame = pd.DataFrame(
        {"anio": years.astype(int), "valor": frame[value_column].map(_number)},
    )
    if series_frame["anio"].duplicated().any():
        raise ValueError("El histórico tiene más de una observación por año")
    series_frame = series_frame.set_index("anio").sort_index()
    annual = list(range(int(series_frame.index.min()), int(series_frame.index.max()) + 1))
    values = [_number(value) for value in series_frame.reindex(annual)["valor"]]
    return annual, values


def dimension_chart(row: pd.Series) -> go.Figure:
    """Show the four dimension scores, preserving unavailable scores as gaps."""
    figure = _figure("Puntuaciones por dimensión", height=300)
    names = [label for _, label in DIMENSIONS]
    values = [_number(row.get(f"puntuacion_{dimension}")) for dimension, _ in DIMENSIONS]
    figure.add_trace(
        go.Bar(
            x=values,
            y=names,
            orientation="h",
            width=0.48,
            marker={"color": [GREEN, GREEN, GREEN, "#59856b"], "line": {"width": 0}},
            text=[f"{value:.1f}" if value is not None else "" for value in values],
            textposition="outside",
            textfont={"color": GREEN},
            cliponaxis=False,
            hovertemplate="%{y}: %{x:.1f} / 100<extra></extra>",
        )
    )
    for name, value in zip(names, values):
        if value is None:
            figure.add_annotation(
                x=0,
                y=name,
                text="Sin dato",
                showarrow=False,
                xanchor="left",
                xshift=3,
                font={"color": MUTED, "size": 12},
            )
    figure.update_xaxes(range=[0, 108], tickvals=[0, 25, 50, 75, 100], title="Puntuación · 0–100")
    figure.update_yaxes(autorange="reversed", showgrid=False, categoryorder="array", categoryarray=names)
    return figure


def population_chart(history: pd.DataFrame, codigo_ine: str) -> go.Figure:
    """Plot total municipal padrón population; absent years break the line."""
    title = "Evolución de la población"
    selected = _municipal_rows(history, codigo_ine, {"codigo_ine", "anio", "poblacion"})
    if selected.empty:
        return _empty(title, "Sin histórico de población para este municipio")
    years, values = _annual_series(selected, "poblacion")
    if not any(value is not None for value in values):
        return _empty(title, "Sin valores de población disponibles")
    figure = _figure(title)
    figure.add_trace(
        go.Scatter(
            x=years,
            y=values,
            mode="lines+markers",
            connectgaps=False,
            line={"color": GREEN, "width": 3},
            marker={"size": 5, "color": GREEN},
            hovertemplate="Padrón %{x:.0f}<br>%{y:,.0f} habitantes<extra></extra>",
            name="Población total",
        )
    )
    figure.update_xaxes(title="Año del padrón", tickformat="d")
    figure.update_yaxes(title="Habitantes", rangemode="tozero", tickformat=",.0f")
    return figure


def rent_history_chart(history: pd.DataFrame, codigo_ine: str, typology: str) -> go.Figure:
    """Plot one declared-rent typology without mixing VC and VU observations."""
    if typology not in HOUSING_LABELS:
        raise ValueError("La tipología de vivienda debe ser VC o VU")
    title = f"Alquiler declarado · {HOUSING_LABELS[typology]}"
    selected = _municipal_rows(
        history, codigo_ine, {"codigo_ine", "anio", "tipo", "alquiler_m2_mes_mediana"},
    )
    if not selected.empty:
        selected = selected.loc[selected["tipo"].eq(typology)]
    if selected.empty:
        return _empty(title, "Sin histórico SERPAVI para esta tipología")
    years, values = _annual_series(selected, "alquiler_m2_mes_mediana")
    if not any(value is not None for value in values):
        return _empty(title, "SERPAVI no publica valores de esta tipología")
    figure = _figure(title)
    figure.add_trace(
        go.Scatter(
            x=years,
            y=values,
            mode="lines+markers",
            connectgaps=False,
            line={"color": GREEN, "width": 3},
            marker={"size": 7, "color": GREEN, "line": {"color": ACCENT, "width": 2}},
            hovertemplate=(
                "SERPAVI %{x:.0f}<br>Mediana: %{y:.2f} €/m²/mes"
                f"<br>{HOUSING_LABELS[typology]}<extra></extra>"
            ),
            name=HOUSING_LABELS[typology],
        )
    )
    figure.update_xaxes(title="Año de referencia · SERPAVI", tickformat="d")
    figure.update_yaxes(title="Mediana · €/m²/mes", rangemode="tozero")
    return figure


def sensitivity_chart(scenarios: pd.DataFrame, codigo_ine: str, profile: str) -> go.Figure:
    """Show observed ranking changes across methods and weight scenarios.

    Boxes describe the finite scenarios that were actually calculated. They
    are not confidence intervals, estimates of uncertainty or probabilities.
    """
    if profile not in HOUSING_LABELS:
        raise ValueError("El perfil de vivienda debe ser VC o VU")
    title = "Posición en los escenarios de sensibilidad"
    selected = _municipal_rows(
        scenarios,
        codigo_ine,
        {"codigo_ine", "perfil_vivienda", "metodo", "escenario_pesos", "posicion", "estado_indice"},
    )
    if not selected.empty:
        selected = selected.loc[
            selected["perfil_vivienda"].eq(profile) & selected["estado_indice"].eq("disponible")
        ].copy()
        selected["posicion"] = selected["posicion"].map(_number)
        selected = selected.loc[selected["posicion"].notna()]
    if selected.empty:
        return _empty(title, "Sin posición disponible para este municipio y tipología")
    figure = _figure(title, height=350)
    methods = [method for method in METHOD_LABELS if method in set(selected["metodo"])]
    methods.extend(sorted(set(selected["metodo"]).difference(METHOD_LABELS)))
    for method in methods:
        method_rows = selected.loc[selected["metodo"].eq(method)].sort_values("escenario_pesos")
        label = METHOD_LABELS.get(method, str(method))
        figure.add_trace(
            go.Box(
                y=method_rows["posicion"].tolist(),
                name=label,
                customdata=method_rows["escenario_pesos"].tolist(),
                boxpoints="all",
                jitter=0.18,
                pointpos=0,
                line={"color": GREEN, "width": 2},
                fillcolor="rgba(204,241,91,0.45)",
                marker={"color": GREEN, "size": 5},
                hovertemplate=(
                    f"{label}<br>Posición: %{{y:.0f}}"
                    "<br>Pesos: %{customdata}<extra></extra>"
                ),
            )
        )
    figure.update_xaxes(title="Método de normalización")
    figure.update_yaxes(title="Posición · 1 es la primera", autorange="reversed", tickformat="d")
    figure.update_layout(margin={"l": 10, "r": 20, "t": 65, "b": 95})
    figure.add_annotation(
        text=f"{len(selected)} escenarios calculados · variación metodológica, sin interpretación probabilística",
        x=0,
        y=-0.30,
        xref="paper",
        yref="paper",
        xanchor="left",
        showarrow=False,
        font={"color": MUTED, "size": 11},
    )
    return figure
