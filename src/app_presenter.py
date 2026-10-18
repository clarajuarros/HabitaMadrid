"""Texto y formato de la ficha; los datos ausentes nunca se convierten en cero."""
import html
import math
from urllib.parse import urlparse

import pandas as pd

PROFILE_LABELS = {"VC": "Vivienda colectiva", "VU": "Vivienda unifamiliar o rural"}
DIMENSION_LABELS = {"sanidad": "Sanidad", "educacion": "Educación", "transporte": "Transporte", "vivienda": "Vivienda"}


def esc(value):
    return html.escape(str(value), quote=True)


def finite(value):
    try:
        return value is not None and not isinstance(value, bool) and math.isfinite(float(value))
    except (ValueError, TypeError):
        return False


def number(value, decimals=0, missing="No hay datos disponibles"):
    if not finite(value):
        return missing
    return f"{float(value):,.{decimals}f}".translate(str.maketrans({",": ".", ".": ","}))


def distance(value):
    if not finite(value):
        return "Sin coordenadas verificadas"
    numeric = float(value)
    return number(numeric / 1000, 2) + " km" if numeric >= 1000 else number(numeric) + " m"


def safe_link(url):
    if not isinstance(url, str):
        return ""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return ""
    return url.strip() if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def metric_card(label, value, detail, unit="", accent=False):
    missing_class = " is-missing" if value == "No hay datos disponibles" else ""
    return (f'<div class="metric-card{" accent" if accent else ""}{missing_class}">'
            f'<div class="metric-label">{esc(label)}</div>'
            f'<div class="metric-value">{esc(value)}<small>{esc(unit)}</small></div>'
            f'<div class="metric-detail">{esc(detail)}</div></div>')


def service_card(row):
    name = str(row.get("nombre", "")).strip()
    name = name if name else "Nombre no publicado"
    pieces = [str(row.get(key, "")).strip() for key in ["municipio", "modalidad", "titularidad", "direccion"]]
    pieces = [piece for piece in pieces if piece and piece.lower() not in {"nan", "none"}]
    lines = str(row.get("lineas", "")).strip()
    if lines and lines.lower() != "nan":
        pieces.append("Líneas del feed parcial: " + lines)
    link = safe_link(row.get("url", ""))
    geometry_link = safe_link(row.get("url_fuente_geometria", ""))
    geometry_note = ""
    if geometry_link and geometry_link != link:
        source_label = ("Ubicación contrastada con otra fuente oficial." if bool(row.get("reparacion_aplicada", False))
                        else "Fuente oficial de la ubicación.")
        geometry_note = (f'<div class="service-small">{source_label} '
                         f'<a href="{esc(geometry_link)}" target="_blank" rel="noopener noreferrer">Fuente de la ubicación ↗</a></div>')
        alerts = row.get("alertas_geometria_json", "[]")
        if isinstance(alerts, str) and "Direcci" in alerts:
            geometry_note += '<div class="service-small">Las fuentes publican direcciones diferentes; consulta la revisión sanitaria.</div>'
    location_note = " · Sin posición verificable" if not finite(row.get("distancia_m")) else ""
    return (f'<div class="service-row"><div class="service-top">'
            f'<span class="service-name">{esc(name)}</span>'
            f'<span class="service-distance">{esc(distance(row.get("distancia_m"))) if finite(row.get("distancia_m")) else ""}</span></div>'
            f'<div class="service-meta">{esc(" · ".join(pieces))}{esc(location_note)}</div>'
            + (f'<a class="service-link" href="{esc(link)}" target="_blank" rel="noopener noreferrer">Ficha de la fuente ↗</a>' if link else "")
            + geometry_note
            + '</div>')


def raw_item(label, value, caption):
    return (f'<div class="raw-item"><div class="raw-label">{esc(label)}</div>'
            f'<div class="raw-value">{esc(value)}</div><div class="raw-caption">{esc(caption)}</div></div>')


def selected_value(observations, identifier):
    subset = observations.loc[observations.indicador_id.eq(identifier)]
    return subset.iloc[0].valor if len(subset) == 1 else float("nan")
