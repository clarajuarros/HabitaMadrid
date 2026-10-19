"""Offline exploration of the fixed IVP snapshot, without fitting new anchors.

Weights apply to the four saved min–max dimension scores before filtering.
Filters only select municipalities; they never impute observations, refit a
normalization or replace the ranking against the complete eligible universe.
"""
from __future__ import annotations

import json
import math
from numbers import Real
import re
import unicodedata

import numpy as np
import pandas as pd

from src.integration_common import IntegrationError
from src.ivp_calculation import DIMENSIONS, calculate_index, normalize_weights


PROFILES = ("VC", "VU")
DIMENSION_LABELS = {
    "sanidad": "Sanidad", "educacion": "Educación",
    "transporte": "Transporte", "vivienda": "Vivienda",
}
ACTIVE_COLUMNS = {
    "general": ("ivp_general", "posicion_general", "estado_general"),
    "personalizado": ("ivp_personalizado", "posicion_personalizada", "estado_personalizado"),
}
FULL_SCOPE = "cuatro_dimensiones"
PARTIAL_SCOPE = "parcial_dimensiones_seleccionadas"
TRANSPORT_OFFER_ID = "oferta_conectividad_programada"
TRANSPORT_OFFER_FIELDS = (
    "expediciones_dia", "destinos_directos_dia", "expediciones_laborable",
    "expediciones_sabado", "expediciones_domingo", "estado_cobertura",
    "nota_cobertura", "inicio_semana", "fin_semana", "puntuacion_frecuencia",
    "puntuacion_conectividad",
)


def uses_transport_offer(data: dict) -> bool:
    """Keep the published distance edition separate from the candidate offer edition."""
    return data.get("configuracion", {}).get("indicadores_comunes", {}).get("transporte") == TRANSPORT_OFFER_ID


def transport_offer_rows(data: dict, codes) -> pd.DataFrame:
    """Align the optional prepared offer without imputing coverage or scores."""
    table = data.get("transporte_oferta")
    if not isinstance(table, pd.DataFrame) or not {"codigo_ine", *TRANSPORT_OFFER_FIELDS}.issubset(table):
        raise IntegrationError("La edición de transporte programado necesita su tabla de oferta y cobertura.")
    if table["codigo_ine"].duplicated().any() or set(table["codigo_ine"]) != set(codes):
        raise IntegrationError("La oferta de transporte no coincide con el universo municipal.")
    return table.set_index("codigo_ine").reindex(list(codes)).copy()


def _plain(value: str) -> str:
    return "".join(character for character in unicodedata.normalize("NFKD", value.casefold())
                   if not unicodedata.combining(character))


def _mode(mode: str) -> str:
    if not isinstance(mode, str):
        raise IntegrationError("El modo debe ser general o personalizado.")
    mode = "personalizado" if mode == "personal" else mode
    if mode not in ACTIVE_COLUMNS:
        raise IntegrationError("El modo debe ser general o personalizado.")
    return mode


def _measure(value, name: str) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise IntegrationError(f"{name} debe ser un número real no negativo.")
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise IntegrationError(f"{name} no es representable.") from exc
    if not math.isfinite(number) or number < 0:
        raise IntegrationError(f"{name} debe ser finito y no negativo.")
    return number


def _numeric(values: pd.Series, name: str, *, nullable=True) -> pd.Series:
    numbers = []
    for value in values:
        if value is None or value is pd.NA or (isinstance(value, Real) and math.isnan(float(value))):
            if not nullable:
                raise IntegrationError(f"Falta un valor de {name}.")
            numbers.append(np.nan)
        else:
            numbers.append(_measure(value, name))
    return pd.Series(numbers, index=values.index, dtype=float)


def _profile_rows(data: dict, name: str, profile: str, codes: pd.Index) -> pd.DataFrame:
    table = data[name]
    if not isinstance(table, pd.DataFrame) or not {"codigo_ine", "perfil_vivienda"}.issubset(table):
        raise IntegrationError(f"Tabla incompleta: {name}.")
    selected = table.loc[table["perfil_vivienda"].eq(profile)]
    if "metodo" in selected:
        selected = selected.loc[selected["metodo"].eq("minmax")]
    if selected["codigo_ine"].duplicated().any() or set(selected["codigo_ine"]) != set(codes):
        raise IntegrationError(f"Universo municipal incompleto o duplicado: {name}.")
    return selected.set_index("codigo_ine").reindex(codes).copy()


def build_explorer_frame(data: dict, profile: str, weights=None) -> pd.DataFrame:
    """Build all 155 eligible rows with general and personalized IVP side by side.

    A zero weight is an explicit partial scenario. A positive-weight missing
    measure or blocked dimension prevents the personalized score, using the
    same calculation engine as the recorded phase-six general edition.
    """
    if profile not in PROFILES:
        raise IntegrationError("La tipología de vivienda debe ser VC o VU.")
    master = data["municipios"]
    required = {"codigo_ine", "municipio", "poblacion", "poblacion_anio"}
    if not isinstance(master, pd.DataFrame) or not required.issubset(master):
        raise IntegrationError("El maestro municipal está incompleto.")
    if (len(master) != 155 or master["codigo_ine"].duplicated().any()
            or not master["codigo_ine"].map(lambda code: isinstance(code, str)
                                          and re.fullmatch(r"28\d{3}", code) is not None).all()):
        raise IntegrationError("Se necesitan los 155 códigos municipales elegibles, sin duplicados.")
    if not master["municipio"].map(lambda name: isinstance(name, str) and bool(name.strip())).all():
        raise IntegrationError("Nombre municipal inválido.")
    frame = master[["codigo_ine", "municipio", "poblacion", "poblacion_anio"]].copy().set_index("codigo_ine")
    frame["poblacion"] = _numeric(frame["poblacion"], "Población", nullable=False)
    if frame["poblacion"].gt(50000).any():
        raise IntegrationError("El explorador solo admite municipios de hasta 50.000 habitantes.")
    codes = frame.index
    original = _profile_rows(data, "ivp_general", profile, codes)
    dimensions = _profile_rows(data, "dimensiones", profile, codes)
    config = data["configuracion"]
    raw_weights = config.get("pesos_generales", {dimension: 0.25 for dimension in DIMENSIONS}) if weights is None else weights
    normalized = normalize_weights(raw_weights)
    blocked = config.get("bloqueos_dimensiones", [])
    scores = dimensions[list(DIMENSIONS)].copy()
    calculated = calculate_index(scores, raw_weights, blocked_dimensions=blocked)
    frame["perfil_vivienda"] = profile
    frame["ivp_general"] = original["valor_indice"]
    frame["posicion_general"] = original["posicion"].astype("Int64")
    frame["estado_general"] = original["estado_indice"]
    frame["alcance_general"] = original["alcance"] if "alcance" in original else FULL_SCOPE
    frame["ivp_personalizado"] = calculated["valor_indice"]
    frame["posicion_personalizada"] = calculated["posicion"]
    frame["estado_personalizado"] = calculated["estado_indice"]
    active = [dimension for dimension in DIMENSIONS if normalized[dimension] > 0]
    frame["alcance_personalizado"] = FULL_SCOPE if len(active) == 4 else PARTIAL_SCOPE
    frame["dimensiones_activas_json"] = json.dumps(active, ensure_ascii=False)
    frame["dimensiones_faltantes_json"] = calculated["dimensiones_faltantes_json"]
    frame["dimensiones_bloqueadas_json"] = calculated["dimensiones_bloqueadas_json"]
    frame["bloqueadas"] = calculated["dimensiones_bloqueadas_json"]
    frame["peso_disponible"] = calculated["peso_disponible"]
    frame["pesos_json"] = json.dumps(normalized, ensure_ascii=False, allow_nan=False)
    for dimension in DIMENSIONS:
        frame[f"puntuacion_{dimension}"] = scores[dimension]
    identifiers = {**config["indicadores_comunes"], "vivienda": config["perfiles"][profile]["indicador"]}
    observations = data["observaciones"]
    column_by_dimension = {
        "sanidad": "distancia_sanidad_m", "educacion": "distancia_educacion_m",
        "transporte": "distancia_transporte_m", "vivienda": "alquiler_m2_mes",
    }
    scheduled_transport = uses_transport_offer(data)
    if scheduled_transport:
        # The new observation is a score, never a distance in metres. Keep the
        # former stop-distance filter as independent auxiliary context.
        column_by_dimension["transporte"] = TRANSPORT_OFFER_ID
    for dimension, column in column_by_dimension.items():
        selected = observations.loc[observations["indicador_id"].eq(identifiers[dimension])]
        if selected["codigo_ine"].duplicated().any() or set(selected["codigo_ine"]) != set(codes):
            raise IntegrationError(f"Observaciones incompletas o duplicadas: {dimension}.")
        values = selected.set_index("codigo_ine")["valor"].reindex(codes)
        frame[column] = _numeric(values, column)
    if scheduled_transport:
        context = data.get("contexto", pd.DataFrame())
        if {"codigo_ine", "indicador_id", "valor"}.issubset(context):
            stop_distance = context.loc[context["indicador_id"].eq("distancia_parada_bus_parcial_m")]
            if stop_distance["codigo_ine"].duplicated().any():
                raise IntegrationError("Distancias auxiliares de paradas duplicadas.")
            frame["distancia_transporte_m"] = _numeric(stop_distance.set_index("codigo_ine")["valor"].reindex(codes), "distancia_transporte_m")
        else:
            frame["distancia_transporte_m"] = np.nan
        offer = transport_offer_rows(data, codes)
        for field in TRANSPORT_OFFER_FIELDS:
            frame[field] = offer[field]
    frame = frame.reset_index()
    frame.attrs = {
        "pesos_normalizados": normalized, "perfil_vivienda": profile,
        "universo_municipios": len(frame), "normalizacion": "minmax", "anclas_fijas": True,
        "metodologia_version": config["metodologia_version"],
        "dimensiones_activas": active,
        "alcance_personalizado": FULL_SCOPE if len(active) == 4 else PARTIAL_SCOPE,
        "bloqueos_dimensiones": list(blocked),
        "transporte_oferta_programada": scheduled_transport,
    }
    return frame


def filter_explorer(frame: pd.DataFrame, *, population_range=None, max_rent=None,
                    max_health_distance_m=None, max_education_distance_m=None,
                    max_transport_distance_m=None, query="", only_with_index=False,
                    mode="general") -> pd.DataFrame:
    """Select rows with inclusive limits, retaining the full-universe scores/ranks.

    Without a limit for a measure, its missing observations remain selectable.
    An active maximum requires an observed value. ``posicion_filtrada`` shares
    minimum ranks for unrounded ties among the remaining municipalities.
    """
    mode = _mode(mode)
    if not isinstance(only_with_index, (bool, np.bool_)):
        raise IntegrationError("only_with_index debe ser booleano.")
    if not isinstance(query, str):
        raise IntegrationError("La búsqueda debe ser texto.")
    criteria = {
        "population_range": None, "max_rent": None, "max_health_distance_m": None,
        "max_education_distance_m": None, "max_transport_distance_m": None,
        "query": query.strip(), "only_with_index": bool(only_with_index), "mode": mode,
    }
    mask = pd.Series(True, index=frame.index)
    if population_range is not None:
        if (not isinstance(population_range, (tuple, list)) or len(population_range) != 2):
            raise IntegrationError("El rango de población debe contener mínimo y máximo.")
        lower, upper = (_measure(value, "Límite de población") for value in population_range)
        if lower > upper:
            raise IntegrationError("El rango de población está invertido.")
        mask &= frame["poblacion"].between(lower, upper, inclusive="both")
        criteria["population_range"] = [lower, upper]
    for value, column, name, key in (
        (max_rent, "alquiler_m2_mes", "Alquiler máximo", "max_rent"),
        (max_health_distance_m, "distancia_sanidad_m", "Distancia sanitaria máxima", "max_health_distance_m"),
        (max_education_distance_m, "distancia_educacion_m", "Distancia educativa máxima", "max_education_distance_m"),
        (max_transport_distance_m, "distancia_transporte_m", "Distancia de transporte máxima", "max_transport_distance_m"),
    ):
        if value is not None:
            limit = _measure(value, name)
            mask &= frame[column].notna() & frame[column].le(limit)
            criteria[key] = limit
    query = _plain(query.strip())
    if query:
        mask &= frame["municipio"].map(_plain).str.contains(query, regex=False) | frame["codigo_ine"].str.contains(query, regex=False)
    value_column = ACTIVE_COLUMNS[mode][0]
    if only_with_index:
        mask &= frame[value_column].notna()
    filtered = frame.loc[mask].copy()
    filtered["posicion_filtrada"] = filtered[value_column].rank(method="min", ascending=False).astype("Int64")
    filtered.attrs.update({"modo": mode, "municipios_filtrados": len(filtered), "filtros": criteria})
    return filtered


def ranking_view(filtered: pd.DataFrame, mode="general") -> pd.DataFrame:
    """Order a filtered view; strengths/weaknesses compare its dimension scores.

    Labels describe relative scores within a municipality, not absolute service
    quality. General mode includes four dimensions; personalized mode only
    includes dimensions with a positive selected weight. Tied dimensions are
    listed together; missing dimensions never become a weakness of zero.
    """
    mode = _mode(mode)
    value_column, rank_column, state_column = ACTIVE_COLUMNS[mode]
    result = filtered.copy()
    result["valor_activo"] = result[value_column]
    result["posicion_global"] = result[rank_column]
    result["estado_activo"] = result[state_column]
    result["posicion_filtrada"] = result[value_column].rank(method="min", ascending=False).astype("Int64")
    included_dimensions = DIMENSIONS
    if mode == "personalizado":
        normalized = normalize_weights(filtered.attrs.get("pesos_normalizados", {}))
        included_dimensions = tuple(dimension for dimension in DIMENSIONS if normalized[dimension] > 0)

    def relative(row, choose_max):
        values = {dimension: row[f"puntuacion_{dimension}"] for dimension in included_dimensions
                  if pd.notna(row[f"puntuacion_{dimension}"])}
        if not values:
            return "Sin dato"
        target = (max if choose_max else min)(values.values())
        return ", ".join(DIMENSION_LABELS[dimension] for dimension, value in values.items() if value == target)

    # Construct explicitly to preserve the empty-view schema in pandas as well.
    result["fortaleza"] = pd.Series([relative(row, True) for _, row in result.iterrows()], index=result.index, dtype=str)
    result["debilidad"] = pd.Series([relative(row, False) for _, row in result.iterrows()], index=result.index, dtype=str)
    result["_municipio_orden"] = result["municipio"].map(_plain)
    result = result.sort_values(["valor_activo", "_municipio_orden", "codigo_ine"],
                                ascending=[False, True, True], na_position="last", kind="stable")
    result = result.drop(columns="_municipio_orden").reset_index(drop=True)
    result.attrs.update({"modo": mode, "municipios_filtrados": len(result),
                         "fortaleza_debilidad": "Extremos de las puntuaciones disponibles, no calidad absoluta."})
    return result
