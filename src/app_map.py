"""Offline municipal choropleth, independent of Streamlit and the data pipelines.

Geometry is read from the immutable phase-four snapshot. All visual measures
use the principal phase-six method; missing values never acquire a zero score.
"""
from __future__ import annotations

from copy import deepcopy
from collections.abc import Mapping
import hashlib
import html
import json
import math
from pathlib import Path
import re

import numpy as np
import pandas as pd
import pydeck as pdk
from pydeck.bindings.json_tools import default_serialize
from shapely.geometry import shape

from src.app_data import AppDataError
from src.app_explorer import transport_offer_rows, uses_transport_offer


MAP_INDICATORS = {
    "ivp": "IVP general",
    "sanidad": "Sanidad · puntuación de proximidad",
    "educacion": "Educación · puntuación de proximidad",
    "transporte": "Transporte · puntuación",
    "vivienda": "Vivienda · puntuación de alquiler",
    "alquiler_m2": "Alquiler declarado por m²",
    "poblacion": "Población municipal",
}
PROFILE_LABELS = {"VC": "Vivienda colectiva", "VU": "Vivienda unifamiliar o rural"}
COLOR_LOW = [211, 91, 80, 235]
COLOR_MID = [244, 217, 107, 235]
COLOR_HIGH = [44, 139, 99, 235]
COLOR_MISSING = [222, 225, 229, 235]
COLOR_EXCLUDED = [192, 201, 207, 220]
COLOR_SELECTED = [109, 146, 35, 255]
COLOR_FILTERED = [226, 231, 226, 180]
_CODE = re.compile(r"28\d{3}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_GEO = Path("data/processed/geografia")
_MUN = Path("data/processed/municipios")


class CompactDeck(pdk.Deck):
    """Keep PyDeck's JSON semantics while omitting presentation whitespace.

    Streamlit sends ``to_json()`` to DeckGL. The pinned PyDeck 0.9.3 serializer
    uses this same default serializer, but pretty prints every coordinate.
    Compact JSON preserves all vertices, precision, layers and accessors.
    """

    def to_json(self):
        return json.dumps(self, sort_keys=True, default=default_serialize,
                          separators=(",", ":"), ensure_ascii=False)


def _json(path: Path) -> dict:
    try:
        result = json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (OSError, ValueError) as exc:
        raise AppDataError(f"No se puede leer el mapa preparado: {path.name}") from exc
    if not isinstance(result, dict):
        raise AppDataError(f"Objeto JSON de mapa inválido: {path.name}")
    return result


def _verify(path: Path, record: dict) -> int:
    if not isinstance(record, dict) or not isinstance(record.get("sha256"), str) or not _HASH.fullmatch(record["sha256"]):
        raise AppDataError(f"Huella del mapa inválida: {path.name}")
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
    except OSError as exc:
        raise AppDataError(f"Falta un archivo geográfico preparado: {path.name}") from exc
    if digest != record["sha256"] or ("bytes" in record and size != record["bytes"]):
        raise AppDataError(f"Huella distinta o geometría caducada: {path.name}")
    return size


def _features(value: dict, name: str, geometry_types: set[str]) -> list[dict]:
    if value.get("type") != "FeatureCollection" or not isinstance(value.get("features"), list):
        raise AppDataError(f"Colección GeoJSON inválida: {name}")
    # RFC 7946 has longitude/latitude coordinates and no alternative CRS.
    if "crs" in value:
        raise AppDataError(f"El mapa requiere GeoJSON WGS84 sin CRS alternativo: {name}")
    for feature in value["features"]:
        if not isinstance(feature, dict) or feature.get("type") != "Feature" or not isinstance(feature.get("properties"), dict):
            raise AppDataError(f"Entidad GeoJSON inválida: {name}")
        try:
            geometry = shape(feature["geometry"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AppDataError(f"Geometría inválida: {name}") from exc
        if geometry.geom_type not in geometry_types or geometry.is_empty or not geometry.is_valid or geometry.has_z:
            raise AppDataError(f"Geometría no válida para el mapa: {name}")
        bounds = np.asarray(geometry.bounds, dtype=float)
        if not np.isfinite(bounds).all() or not (-180 <= bounds[0] <= bounds[2] <= 180 and -90 <= bounds[1] <= bounds[3] <= 90):
            raise AppDataError(f"Coordenadas fuera de WGS84: {name}")
        # This application depicts Madrid; axis inversions and other territories
        # must fail even if their coordinates happen to be valid WGS84 numbers.
        if not (-5.0 < bounds[0] <= bounds[2] < -2.5 and 39.5 < bounds[1] <= bounds[3] < 41.5):
            raise AppDataError(f"Coordenadas fuera de la Comunidad de Madrid: {name}")
    return value["features"]


def load_map_data(root: str | Path) -> dict:
    """Verify phase-two/four hashes and load 179 terms, shared areas and origins.

    The 4.3 MB boundary geometry is already suitable for a local web view;
    no simplification, repair, reprojection or source mutation happens here.
    """
    root = Path(root).resolve()
    paths = {2: root / _MUN / "validacion.json", 4: root / _GEO / "validacion_geografia.json"}
    from src.runtime_snapshot import MANIFEST, read_snapshot
    if (root / MANIFEST).exists():
        manifests, records = read_snapshot(root)
        total_bytes = 0
        ancestry_count = 0
    else:
        manifests = {phase: _json(path) for phase, path in paths.items()}
        for phase, manifest in manifests.items():
            if manifest.get("fase") != phase or not isinstance(manifest.get("archivos"), dict):
                raise AppDataError(f"Manifiesto del mapa inválido: fase {phase}")
        ancestry = manifests[4].get("entradas", [])
        phase2_records = [row for row in ancestry if isinstance(row, dict) and str(row.get("path", "")).endswith("/data/processed/municipios/validacion.json")]
        if len(phase2_records) != 1:
            raise AppDataError("El mapa no declara una procedencia municipal única")
        total_bytes = _verify(paths[2], phase2_records[0])
        ancestry_count = 1
    consumed = [(2, "maestro_municipios.csv"), (4, "limites_municipales.geojson"),
                (4, "jurisdicciones_compartidas.geojson"), (4, "origenes_municipales.geojson")]
    for phase, filename in consumed:
        record = manifests[phase]["archivos"].get(filename)
        total_bytes += _verify(paths[phase].parent / filename, record)
    try:
        master = pd.read_csv(root / _MUN / "maestro_municipios.csv", dtype=str, keep_default_na=False, encoding="utf-8-sig")
    except (OSError, ValueError) as exc:
        raise AppDataError("Maestro municipal del mapa ilegible") from exc
    required = {"codigo_ine", "municipio", "poblacion", "poblacion_anio", "elegible_hasta_50000"}
    if not required.issubset(master) or len(master) != 179 or master["codigo_ine"].duplicated().any() or not master["codigo_ine"].str.fullmatch(r"28\d{3}").all():
        raise AppDataError("El mapa requiere los 179 municipios con códigos únicos")
    for field in ("poblacion", "poblacion_anio"):
        master[field] = pd.to_numeric(master[field], errors="coerce")
        if not np.isfinite(master[field]).all() or not master[field].eq(master[field].round()).all():
            raise AppDataError(f"Medida municipal inválida en el mapa: {field}")
        master[field] = master[field].astype(int)
    if not master["poblacion_anio"].eq(2025).all() or master["poblacion"].lt(0).any() or not master["elegible_hasta_50000"].isin(["True", "False"]).all():
        raise AppDataError("Padrón o elegibilidad del mapa inválidos")
    master["elegible_hasta_50000"] = master["elegible_hasta_50000"].eq("True")
    if not master["elegible_hasta_50000"].equals(master["poblacion"].le(50000)) or master["elegible_hasta_50000"].sum() != 155:
        raise AppDataError("El mapa no respeta el ámbito inclusivo de 50.000 habitantes")
    geojson = _json(root / _GEO / "limites_municipales.geojson")
    shared = _json(root / _GEO / "jurisdicciones_compartidas.geojson")
    origin_json = _json(root / _GEO / "origenes_municipales.geojson")
    features = _features(geojson, "términos municipales", {"Polygon", "MultiPolygon"})
    shared_features = _features(shared, "jurisdicciones compartidas", {"Polygon", "MultiPolygon"})
    origins = _features(origin_json, "orígenes municipales", {"Point"})
    codes = [feature["properties"].get("codigo_ine") for feature in features]
    if len(codes) != 179 or len(set(codes)) != 179 or set(codes) != set(master["codigo_ine"]):
        raise AppDataError("Las geometrías no corresponden al maestro de 179 municipios")
    by_code = master.set_index("codigo_ine")
    for feature in features:
        props = feature["properties"]
        row = by_code.loc[props["codigo_ine"]]
        if props.get("municipio") != row["municipio"] or props.get("poblacion") != row["poblacion"] or props.get("elegible_hasta_50000") != row["elegible_hasta_50000"]:
            raise AppDataError("Atributos geográficos incompatibles con el padrón")
    shared_ids = [feature["properties"].get("area_compartida_id") for feature in shared_features]
    if len(shared_ids) != 2 or len(set(shared_ids)) != 2 or not all(isinstance(value, str) and value for value in shared_ids):
        raise AppDataError("Deben conservarse las dos jurisdicciones compartidas")
    eligible = set(master.loc[master["elegible_hasta_50000"], "codigo_ine"])
    origin_codes = [feature["properties"].get("codigo_ine") for feature in origins]
    if len(origin_codes) != 155 or len(set(origin_codes)) != 155 or set(origin_codes) != eligible:
        raise AppDataError("Los 155 orígenes elegibles deben ser únicos")
    origin_rows = []
    for feature in origins:
        props = feature["properties"]
        longitude, latitude = feature["geometry"]["coordinates"]
        if not math.isclose(longitude, props.get("longitud", math.nan), abs_tol=1e-9) or not math.isclose(latitude, props.get("latitud", math.nan), abs_tol=1e-9):
            raise AppDataError("Coordenadas incongruentes en un origen municipal")
        origin_rows.append({**props, "longitud": longitude, "latitud": latitude})
    # Derived camera metadata belongs to the verified in-memory snapshot. It
    # avoids rebuilding 179 Shapely polygons for every interactive rerun.
    # No geometry is simplified or written back to the prepared files.
    municipal_bounds = {feature["properties"]["codigo_ine"]: list(shape(feature["geometry"]).bounds)
                        for feature in features}
    boxes = np.asarray(list(municipal_bounds.values()), dtype=float)
    region_bounds = [float(boxes[:, 0].min()), float(boxes[:, 1].min()),
                     float(boxes[:, 2].max()), float(boxes[:, 3].max())]
    return {"geojson": geojson, "shared": shared, "origenes": pd.DataFrame(origin_rows), "maestro": master,
            "bounds_region": region_bounds, "bounds_municipios": municipal_bounds,
            "verificacion": {"archivos_verificados": len(consumed) + ancestry_count, "bytes_verificados": total_bytes,
                              "municipios": 179, "municipios_elegibles": 155, "areas_compartidas": 2,
                              "crs": "EPSG:4326", "geometrias_simplificadas": False,
                              "bytes_limites": manifests[4]["archivos"]["limites_municipales.geojson"]["bytes"]}}


def _number(value: float, decimals: int = 1) -> str:
    # Locale-independent Spanish formatting, suitable for plain-text tooltips.
    return f"{value:,.{decimals}f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _colors(values: pd.Series, minimum: float | None, maximum: float | None, *, reverse: bool) -> list[list[int]]:
    result = []
    for value in values:
        if pd.isna(value):
            result.append(COLOR_MISSING.copy())
            continue
        t = .5 if minimum == maximum else (value - minimum) / (maximum - minimum)
        t = min(1.0, max(0.0, 1 - t if reverse else t))
        start, end = (COLOR_LOW, COLOR_MID) if t <= .5 else (COLOR_MID, COLOR_HIGH)
        fraction = t * 2 if t <= .5 else (t - .5) * 2
        result.append([round(low + fraction * (high - low)) for low, high in zip(start, end)])
    return result


def build_map_frame(map_data: dict, app_data: dict, profile: str, indicator: str, *,
                    explorer_frame: pd.DataFrame | None = None, mode: str = "general",
                    visible_codes=None) -> pd.DataFrame:
    """Join the complete geography to a fixed eligible scoring universe.

    The frame is never filtered by selection or camera; excluded terms remain
    context. Housing VC/VU is an explicit profile, with no missing-data fallback.
    """
    if profile not in PROFILE_LABELS or indicator not in MAP_INDICATORS or mode not in {"general", "personalizado"}:
        raise AppDataError("Perfil o indicador del mapa desconocido")
    master = map_data["maestro"]
    frame = master[["codigo_ine", "municipio", "poblacion", "poblacion_anio", "elegible_hasta_50000"]].copy()
    frame = frame.rename(columns={"elegible_hasta_50000": "elegible"})
    eligible = set(frame.loc[frame["elegible"], "codigo_ine"])
    if eligible != set(app_data["municipios"]["codigo_ine"]):
        raise AppDataError("El ámbito del mapa no coincide con los indicadores")
    frame["codigo"] = frame["codigo_ine"]
    frame["perfil_vivienda"] = profile
    frame["indicador"] = indicator
    unit, period = "puntos / 100", "Fuentes de distintos períodos · consulta geográfica 2026"
    indicator_label = MAP_INDICATORS[indicator]
    scheduled_transport = uses_transport_offer(app_data)
    offers = transport_offer_rows(app_data, eligible) if scheduled_transport else None
    partial = False
    if indicator == "ivp":
        if mode == "personalizado":
            if explorer_frame is None or not {"codigo_ine", "ivp_personalizado", "estado_personalizado", "perfil_vivienda", "alcance_personalizado"}.issubset(explorer_frame):
                raise AppDataError("El mapa personalizado requiere el escenario completo")
            source = explorer_frame.copy()
            if not source["perfil_vivienda"].eq(profile).all():
                raise AppDataError("La tipología del mapa no coincide con las prioridades")
            column = "ivp_personalizado"
            details = source.set_index("codigo_ine")["estado_personalizado"].to_dict()
            partial = source["alcance_personalizado"].eq("parcial_dimensiones_seleccionadas").any()
            indicator_label = "IVP personalizado" + (" · alcance parcial" if partial else "")
        else:
            source = app_data["ivp_general"]
            source = source.loc[source["perfil_vivienda"].eq(profile)].copy()
            column = "valor_indice"
            details = source.set_index("codigo_ine")["estado_indice"].to_dict()
    elif indicator in {"sanidad", "educacion", "transporte", "vivienda"}:
        source = app_data["dimensiones"]
        source = source.loc[source["perfil_vivienda"].eq(profile)].copy()
        if "metodo" in source:
            source = source.loc[source["metodo"].eq("minmax")].copy()
        column, details = indicator, {}
        if indicator == "vivienda":
            period = "SERPAVI · 2024"
        elif indicator == "transporte" and scheduled_transport:
            indicator_label = "Transporte · oferta y conectividad programada"
            period = "Semana " + str(offers["inicio_semana"].iloc[0]) + " a " + str(offers["fin_semana"].iloc[0])
            details = {}
            for code, offer in offers.iterrows():
                trips, destinations = offer["expediciones_dia"], offer["destinos_directos_dia"]
                figures = (("Sin cifra de expediciones" if pd.isna(trips) else _number(float(trips), 1) + " expediciones/día")
                           + "; " + ("sin cifra de destinos" if pd.isna(destinations) else _number(float(destinations), 1) + " destinos directos/día"))
                details[code] = figures + ". Cobertura: " + str(offer["estado_cobertura"]).replace("_", " ") + ". " + str(offer["nota_cobertura"])
        else:
            period = "Catálogo geográfico · consulta 2026-10-15"
            if indicator == "transporte":
                indicator_label = "Transporte · puntuación de proximidad"
    elif indicator == "alquiler_m2":
        source = app_data["observaciones"]
        indicator_id = app_data["configuracion"]["perfiles"][profile]["indicador"]
        source = source.loc[source["indicador_id"].eq(indicator_id)].copy()
        column, details = "valor", source.set_index("codigo_ine")["estado"].to_dict()
        unit, period = "€/m²/mes", "SERPAVI · 2024"
    else:
        source = frame.loc[frame["elegible"], ["codigo_ine", "poblacion"]].copy()
        column, details = "poblacion", {}
        unit, period = "habitantes", "Padrón · 1 de enero de 2025"
    if source["codigo_ine"].duplicated().any() or set(source["codigo_ine"]) != eligible:
        raise AppDataError("Medida geográfica incompleta o con códigos duplicados")
    values = pd.to_numeric(source.set_index("codigo_ine")[column], errors="raise")
    if np.isinf(values.to_numpy(dtype=float)).any() or (indicator in {"ivp", "sanidad", "educacion", "transporte", "vivienda"} and not values.dropna().between(0, 100).all()):
        raise AppDataError("Medida del mapa fuera de su escala")
    if values.dropna().lt(0).any():
        raise AppDataError("Una medida del mapa no puede ser negativa")
    frame["valor"] = frame["codigo_ine"].map(values)
    frame.loc[~frame["elegible"], "valor"] = np.nan
    frame["estado"] = np.where(~frame["elegible"], "fuera_ambito", np.where(frame["valor"].isna(), "sin_dato", "disponible"))
    frame["detalle"] = frame["codigo_ine"].map(details).fillna("")
    frame.loc[frame["estado"].eq("sin_dato") & frame["detalle"].eq(""), "detalle"] = "Dato no disponible para esta tipología e indicador"
    frame.loc[frame["estado"].eq("fuera_ambito"), "detalle"] = "Municipio de más de 50.000 habitantes · contexto geográfico"
    frame["unidad"], frame["periodo"] = unit, period
    score = indicator in {"ivp", "sanidad", "educacion", "transporte", "vivienda"}
    finite = frame["valor"].dropna()
    minimum = 0.0 if score else (float(finite.min()) if not finite.empty else None)
    maximum = 100.0 if score else (float(finite.max()) if not finite.empty else None)
    frame["color_rgba"] = _colors(frame["valor"], minimum, maximum, reverse=indicator == "alquiler_m2")
    for index in frame.index[~frame["elegible"]]:
        frame.at[index, "color_rgba"] = COLOR_EXCLUDED.copy()
    tooltips = []
    tooltip_values = []
    for row in frame.itertuples():
        if row.estado == "disponible":
            measure = f"{_number(row.valor, 0 if indicator == 'poblacion' else 2)} {unit}"
            status = "Disponible"
        elif row.estado == "fuera_ambito":
            measure, status = "Sin valor en esta comparación", "Fuera del ámbito de hasta 50.000 habitantes"
        else:
            measure, status = "Sin datos", "Dato ausente; no equivale a cero"
        display_value = (f"{_number(row.valor, 0 if indicator == 'poblacion' else (1 if score else 2))} {'/ 100' if score else unit}"
                         if row.estado == "disponible" else "Sin datos")
        tooltip_values.append(display_value)
        tooltips.append(f"{row.municipio} · INE {row.codigo_ine}\nPoblación: {_number(row.poblacion, 0)} habitantes (2025)\n"
                        f"Tipología: {PROFILE_LABELS[profile]}\n{indicator_label}: {measure}\n"
                        f"Período: {period}\nEstado: {status}" + (f"\nDetalle: {row.detalle}" if row.detalle else ""))
    frame["tooltiptext"] = tooltips
    frame["tooltipvalue"] = tooltip_values
    frame["tooltiplabel"] = indicator_label
    frame["tooltipdetail"] = frame["detalle"].map(html.escape) if indicator == "transporte" and scheduled_transport else ""
    interpretation = ("Mayor puntuación = resultado más favorable en este indicador del modelo; no mide por sí sola la calidad de vida."
                      if score else ("Menor alquiler declarado = menor coste observado. No son precios de oferta actuales ni una medida global de calidad de vida."
                                     if indicator == "alquiler_m2" else "Mayor intensidad = más habitantes. La población no indica mayor calidad de vida."))
    if partial:
        interpretation += " Alcance parcial: solo intervienen las dimensiones con peso positivo."
    if indicator == "transporte" and scheduled_transport:
        interpretation = "Transporte combina, con la misma importancia, la oferta de expediciones intermunicipales y los destinos municipales directos de media diaria. Semana del 19 al 25 de octubre de 2026. Son horarios programados, sin transbordos ni tiempo real. La cobertura se indica por municipio; una carencia de datos no es cero servicio."
    frame["estado_indicador"] = frame["estado"]
    if visible_codes is None:
        visible = eligible
    else:
        visible = set(visible_codes)
        if not visible.issubset(eligible):
            raise AppDataError("Los filtros del mapa contienen municipios fuera del ámbito")
    frame["cumple_filtros"] = frame["codigo_ine"].isin(visible) & frame["elegible"]
    outside = frame["elegible"] & ~frame["cumple_filtros"]
    frame.loc[outside, "estado"] = "fuera_filtros"
    for index in frame.index[outside]:
        frame.at[index, "color_rgba"] = COLOR_FILTERED.copy()
        frame.at[index, "tooltiptext"] += "\nNo cumple los filtros actuales; conserva su valor original."
    frame.attrs["leyenda"] = {"indicador": indicator, "label": indicator_label, "unidad": unit,
                              "perfil": PROFILE_LABELS[profile], "periodo": period, "minimo": minimum, "maximo": maximum,
                              "color_minimo": (COLOR_HIGH if indicator == "alquiler_m2" else COLOR_LOW).copy(),
                              "color_maximo": (COLOR_LOW if indicator == "alquiler_m2" else COLOR_HIGH).copy(),
                              "color_ausencia": COLOR_MISSING.copy(), "color_excluido": COLOR_EXCLUDED.copy(),
                              "interpretacion": interpretation, "metodo": "minmax" if score else "valor_original",
                              "modo": mode, "alcance_parcial": bool(partial),
                              "color_filtrado": COLOR_FILTERED.copy(), "municipios_visibles": len(visible)}
    frame.attrs["transporte_oferta_programada"] = scheduled_transport
    return frame


def map_legend(frame: pd.DataFrame) -> dict:
    """Return the frame's complete, selection-independent legend metadata."""
    if not isinstance(frame.attrs.get("leyenda"), dict):
        raise AppDataError("La matriz del mapa no declara una leyenda")
    return deepcopy(frame.attrs["leyenda"])


def _camera(map_data: dict, selected_code: str | None, focus: str) -> pdk.ViewState:
    if focus not in {"region", "municipio"}:
        raise AppDataError("Encuadre geográfico desconocido")
    geometries = map_data["geojson"]["features"]
    if focus == "municipio":
        origins = map_data["origenes"]
        match = origins.loc[origins["codigo_ine"].eq(selected_code)]
        if len(match) != 1:
            raise AppDataError("El encuadre municipal requiere un origen elegible")
        bounds = map_data.get("bounds_municipios", {}).get(selected_code)
        if bounds is None:
            # Keep the pure adapter compatible with smaller, independently
            # constructed map fixtures that predate the derived metadata.
            target = next((feature for feature in geometries if feature["properties"]["codigo_ine"] == selected_code), None)
            if target is None:
                raise AppDataError("El municipio seleccionado no tiene geometría")
            bounds = shape(target["geometry"]).bounds
        longitude, latitude = float(match.iloc[0]["longitud"]), float(match.iloc[0]["latitud"])
    else:
        bounds = map_data.get("bounds_region")
        if bounds is None:
            boxes = np.asarray([shape(feature["geometry"]).bounds for feature in geometries])
            bounds = (boxes[:, 0].min(), boxes[:, 1].min(), boxes[:, 2].max(), boxes[:, 3].max())
        longitude, latitude = (bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2
    # Streamlit does not expose the browser viewport width to this Python
    # adapter. Fit the same conservative 320 × 540 viewport for mobile and
    # desktop, retaining pan/zoom on both. Horizontal padding is 10%, vertical
    # padding 25%; the municipal camera centres the actual model origin.
    def mercator(lat: float) -> float:
        return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    longitude_span = max(abs(bounds[2] - longitude), abs(bounds[0] - longitude)) * 2
    mercator_span = max(abs(mercator(bounds[3]) - mercator(latitude)), abs(mercator(bounds[1]) - mercator(latitude))) * 2
    zoom_x = math.log2(320 * .90 * 360 / (512 * max(longitude_span, 1e-6)))
    zoom_y = math.log2(540 * .75 * 2 * math.pi / (512 * max(mercator_span, 1e-7)))
    zoom = min(zoom_x, zoom_y, 11)
    return pdk.ViewState(longitude=longitude, latitude=latitude, zoom=zoom, min_zoom=5, max_zoom=11,
                         pitch=0, bearing=0, min_pitch=0, max_pitch=0, transition_duration=0)


def _copy_polygon_collection(collection):
    """Copy verified numeric coordinate arrays without visiting every scalar.

    All lists stay independent; metadata still uses deepcopy. No vertices or
    coordinate precision are changed, and the immutable source stays isolated.
    """
    memo = {}
    for feature in collection["features"]:
        geometry = feature["geometry"]
        if geometry["type"] not in {"Polygon", "MultiPolygon"}:
            continue
        coords = geometry["coordinates"]
        polygons = [coords] if geometry["type"] == "Polygon" else coords
        copied = [[[point.copy() for point in ring] for ring in polygon] for polygon in polygons]
        memo[id(coords)] = copied[0] if geometry["type"] == "Polygon" else copied
    return deepcopy(collection, memo)


def build_deck(map_data: dict, frame: pd.DataFrame, selected_code: str | None, *, focus: str = "region", camera=None, layer_id="municipios") -> pdk.Deck:
    """Build a planar, pickable GeoJSON view with no network basemap provider."""
    if frame["codigo_ine"].duplicated().any() or set(frame["codigo_ine"]) != set(map_data["maestro"]["codigo_ine"]):
        raise AppDataError("La matriz del mapa debe mantener los 179 municipios únicos")
    eligible = set(frame.loc[frame["elegible"], "codigo_ine"])
    if selected_code is not None and selected_code not in eligible:
        raise AppDataError("El municipio seleccionado está fuera del ámbito")
    lookups = frame.set_index("codigo_ine").to_dict("index")
    geojson = _copy_polygon_collection(map_data["geojson"])
    for feature in geojson["features"]:
        code = feature["properties"]["codigo_ine"]
        row = lookups[code]
        feature["id"] = code
        feature["properties"] = {"codigo_ine": code, "tooltiptext": row["tooltiptext"],
                                  "tooltipname": row["municipio"], "tooltiplabel": row["tooltiplabel"], "tooltipvalue": row["tooltipvalue"],
                                  "tooltipdetail": row.get("tooltipdetail", ""),
                                  "color_rgba": row["color_rgba"],
                                  "line_rgba": COLOR_SELECTED.copy() if code == selected_code else [248, 248, 239, 255],
                                  "line_width": 3 if code == selected_code else 1}
    # PyDeck treats unquoted strings as accessors; enum units need an explicit
    # literal to avoid serializing @@=pixels and inflating polygon outlines.
    municipalities = pdk.Layer("GeoJsonLayer", geojson, id=layer_id, pickable=True, auto_highlight=True,
                               filled=True, stroked=True, extruded=False, get_fill_color="properties.color_rgba",
                               get_line_color="properties.line_rgba", get_line_width="properties.line_width",
                               line_width_units='"pixels"', line_width_min_pixels=1, line_width_max_pixels=3,
                               highlight_color=[222, 235, 149, 90],
                               # Keep the IVP palette in the fill sublayer. Streamlit
                               # changes the parent accessor opacity for native picks.
                               _sub_layer_props={"polygons-fill": {"getFillColor": "@@=properties.color_rgba"}})
    shared = pdk.Layer("GeoJsonLayer", _copy_polygon_collection(map_data["shared"]), id="jurisdicciones_compartidas", pickable=False,
                      filled=True, stroked=True, extruded=False, get_fill_color=[172, 184, 168, 235],
                      get_line_color=[102, 119, 107, 255], get_line_width=1, line_width_units='"pixels"',
                      line_width_min_pixels=1, line_width_max_pixels=1,
                      _sub_layer_props={"polygons-fill": {"getFillColor": [172, 184, 168, 235]}})
    view = pdk.View(type="MapView", id="default-view", controller={"dragPan": True, "scrollZoom": True, "doubleClickZoom": False,
                                               "touchZoom": True, "dragRotate": False, "touchRotate": False}, repeat=False)
    transport_detail = ('<div style="font-size:11px;line-height:1.4;margin-top:7px">{tooltipdetail}</div>'
                        if frame.attrs.get("transporte_oferta_programada") and frame.attrs.get("leyenda", {}).get("indicador") == "transporte" else "")
    # Preserve PyDeck's explicit default-style sentinel. In Streamlit 1.65 an
    # omitted mapStyle (the result of passing None) activates its CARTO fallback;
    # the sentinel explicitly disables that fallback when no provider is used.
    # This leaves only the local DeckGL geometry, with no Mapbox/CARTO tiles.
    return CompactDeck(layers=[municipalities, shared], views=[view], initial_view_state=camera if camera is not None else _camera(map_data, selected_code, focus),
                    map_provider=None, map_style="__MAP_STYLE__", tooltip={"html": '<strong style="display:block;font-size:14px;margin-bottom:5px">{tooltipname}</strong><span style="font-size:11px;opacity:.82">{tooltiplabel}</span><div style="font-size:17px;font-weight:650;margin-top:2px">{tooltipvalue}</div>' + transport_detail, "style": {"fontFamily": "Inter, system-ui, sans-serif", "backgroundColor": "#173d35", "color": "#ffffff", "padding": "12px 16px", "borderRadius": "12px", "border": "1px solid #ffffff22", "boxShadow": "0 4px 16px #173d3520", "boxSizing": "border-box", "overflowWrap": "anywhere", "pointerEvents": "none", "transform": "none", "left": "auto", "top": "auto", "right": "12px", "bottom": "12px", "width": "max-content", "maxWidth": "min(260px, calc(100% - 24px))"}}, height=580,
                    parameters={"clearColor": [0.957, 0.961, 0.937, 1]}, description="HabitaMadrid · términos municipales")


def selection_code(event: object, eligible_codes: set[str] | list[str], *, layer_id="municipios") -> str | None:
    """Resolve a single trusted layer/feature identity; never infer row indices.

    A Streamlit selection snapshot is UI input, not permission to select an
    excluded municipality. Ambiguous selections and stale/malformed data fail
    closed. GeoJSON feature IDs generated above agree with their INE property.
    """
    if not isinstance(event, Mapping):
        return None
    selection = event.get("selection")
    if not isinstance(selection, Mapping):
        return None
    objects = selection.get("objects")
    if not isinstance(objects, Mapping) or set(objects) != {layer_id}:
        return None
    picked = objects[layer_id]
    if not isinstance(picked, list) or len(picked) != 1 or not isinstance(picked[0], Mapping):
        return None
    feature = picked[0]
    properties = feature.get("properties")
    if feature.get("type") != "Feature" or not isinstance(properties, Mapping):
        return None
    code = properties.get("codigo_ine")
    if not isinstance(code, str) or not _CODE.fullmatch(code) or code not in set(eligible_codes):
        return None
    if "id" in feature and feature["id"] != code:
        return None
    return code
