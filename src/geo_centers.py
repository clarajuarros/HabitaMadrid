"""Geografía de directorios: CRS documentado, conflictos visibles y sin ajustes.

El contrato de evidencia distingue un CRS confirmado por una fuente oficial de
una conjetura por rango numérico. Un punto exterior nunca se arrastra al polígono
municipal. Las coordenadas y los atributos de fase 3 permanecen en la tabla.
"""
import json
import math
from pathlib import Path

import geopandas as gpd
import pandas as pd
from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError, ProjError
from shapely.geometry import Point

from src.geo_common import assign_points, require_metric_crs
from src.integration_common import IntegrationError, make_issue
from src.municipalities import normalized_name

METRIC_CRS = "EPSG:25830"
# Solo etiqueta diagnóstica: tampoco se aceptan discrepancias menores de 100 m.
BOUNDARY_DIAGNOSTIC_METERS = 100.0
HOSPITAL_TYPES = {
    "hospital general", "hospital especializado", "hospital de media y larga estancia",
    "hospital de salud mental y tratamiento de toxicomanias",
}
CONSULTORIO_TYPES = {"consultorio de atencion primaria", "consultorio local"}


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def center_category(source, center_type):
    """La categoría deriva del tipo publicado, nunca de ofertas o del nombre."""
    if source == "educacion":
        return "educacion"
    value = normalized_name(center_type)
    if value == "centro de salud":
        return "centro_salud"
    if value in CONSULTORIO_TYPES:
        return "consultorio"
    if value in HOSPITAL_TYPES:
        return "hospital"
    return "sanidad_otros"


def _coordinate_pair(row):
    conflicts = row.get("atributos_conflictivos_json", "{}") or "{}"
    try:
        conflicts = json.loads(conflicts)
    except (TypeError, ValueError) as exc:
        raise IntegrationError("JSON de atributos conflictivos sanitario inválido.") from exc
    if {"coordenada_x_original", "coordenada_y_original"} & set(conflicts):
        return None, "coordenadas_conflictivas"
    x, y = [str(row.get(field, "")).strip() for field in
            ("coordenada_x_original", "coordenada_y_original")]
    if not x and not y:
        return None, "coordenadas_ausentes"
    if not x or not y:
        return None, "coordenadas_incompletas"
    try:
        pair = (float(x.replace(",", ".")), float(y.replace(",", ".")))
    except (ValueError, OverflowError):
        return None, "coordenadas_no_numericas"
    if not all(math.isfinite(value) for value in pair):
        return None, "coordenadas_no_finitas"
    if pair in {(0.0, 0.0), (-109.0, -208.0)}:
        return None, "coordenadas_centinela"
    return pair, "presentes"


def _confirmed_crs(evidence):
    """No basta con que figure un número EPSG: requiere confirmación y evidencia."""
    if evidence.get("confirmed") is not True:
        return None
    if not evidence.get("url") or not evidence.get("sha256") or not evidence.get("evidence"):
        raise IntegrationError("CRS confirmado sin URL, hash y explicación de evidencia.")
    try:
        return CRS.from_epsg(int(evidence["epsg"]))
    except (KeyError, ValueError, TypeError, CRSError) as exc:
        raise IntegrationError("EPSG confirmado no válido.") from exc


def _read_centers(path):
    frame = pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    required = {"registro_id", "codigo_ine", "coordenada_x_original",
                "coordenada_y_original", "tipo_centro_original", "fuente"}
    if missing := required - set(frame):
        raise IntegrationError(f"{path.name}: faltan campos {sorted(missing)}")
    if frame.registro_id.eq("").any() or not frame.registro_id.is_unique:
        raise IntegrationError(f"{path.name}: se necesitan identificadores únicos y no vacíos.")
    return frame


def _prepare_one(frame, source, evidence, municipalities, shared_areas, known_codes):
    output = frame.copy()
    crs = _confirmed_crs(evidence)
    transformer = Transformer.from_crs(crs, METRIC_CRS, always_xy=True) if crs else None
    lonlat = Transformer.from_crs(METRIC_CRS, "EPSG:4326", always_xy=True)
    geometry, statuses, issues = [], [], []
    for row in frame.to_dict("records"):
        pair, state = _coordinate_pair(row)
        point = None
        if pair is not None:
            if crs is None:
                state = "crs_no_confirmado"
            else:
                try:
                    x, y = transformer.transform(*pair, errcheck=True)
                    if not all(math.isfinite(value) for value in (x, y)):
                        raise ValueError("resultado no finito")
                    # La transformación identidad también debe rechazar valores
                    # finitos que no representan una localización terrestre.
                    longitude, latitude = lonlat.transform(x, y, errcheck=True)
                    if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
                        raise ValueError("localización fuera del dominio terrestre")
                    point = Point(x, y)
                except (ValueError, OverflowError, RuntimeError, ProjError):
                    state = "transformacion_fallida"
        geometry.append(point)
        statuses.append(state)
    geo = gpd.GeoDataFrame(output.copy(), geometry=geometry, crs=METRIC_CRS)
    assigned = assign_points(geo[["registro_id", "geometry"]], municipalities, shared_areas)
    # Orden y correspondencia se validan mediante identificador, no por posición.
    assigned = assigned.set_index("registro_id").reindex(output.registro_id)
    for field in assigned.columns:
        output[field] = assigned[field].to_numpy()
    output["crs_original_confirmado"] = crs.to_string() if crs else ""
    output["crs_evidencia_url"] = evidence.get("url", "")
    output["crs_evidencia_sha256"] = evidence.get("sha256", "")
    output["categoria_servicio"] = [center_category(source, value)
                                    for value in output.tipo_centro_original]
    states, operations, distances, effective_codes = [], [], [], []
    xs, ys, longitudes, latitudes = [], [], [], []
    polygons = municipalities.set_index("codigo_ine").geometry
    for index, row in output.iterrows():
        point, state = geometry[index], statuses[index]
        distance, effective, operational = None, "", False
        declared, spatial = row.codigo_ine, row.codigo_ine_espacial
        if point is not None:
            xs.append(point.x); ys.append(point.y)
            lon, lat = lonlat.transform(point.x, point.y, errcheck=True)
            longitudes.append(lon); latitudes.append(lat)
            spatial_state = row.estado_asignacion_espacial
            if declared and declared not in known_codes:
                state = "municipio_declarado_desconocido"
            elif spatial_state == "municipio_unico":
                if not declared:
                    state, effective, operational = "municipio_derivado_espacial", spatial, True
                elif spatial == declared:
                    state, effective, operational = "coincide_municipio", declared, True
                else:
                    distance = point.distance(polygons.loc[declared])
                    state = ("discrepancia_proxima_limite" if distance <= BOUNDARY_DIAGNOSTIC_METERS
                             else "discrepancia_municipio")
            elif spatial_state == "sin_coincidencia":
                state = "fuera_cartografia"
                if declared in known_codes:
                    distance = point.distance(polygons.loc[declared])
            else:
                state = spatial_state
        else:
            xs.append(None); ys.append(None); longitudes.append(None); latitudes.append(None)
        states.append(state); operations.append(operational)
        distances.append(distance); effective_codes.append(effective)
        if not operational:
            issues.append(make_issue(
                str(row.fuente), state, registro=str(row.registro_id), campo="geografia",
                valor_original=_json({"codigo_ine_declarado": declared,
                                      "codigo_ine_espacial": spatial,
                                      "x": row.coordenada_x_original,
                                      "y": row.coordenada_y_original}),
                detalle="Punto no utilizado para cercanía; no se altera el municipio declarado.",
            ))
    output["x_25830"], output["y_25830"] = xs, ys
    output["longitud"], output["latitud"] = longitudes, latitudes
    output["estado_geolocalizacion"] = states
    output["geografia_operativa"] = operations
    output["distancia_municipio_declarado_m"] = distances
    output["codigo_ine_geografico_operativo"] = effective_codes
    output["metodo_localizacion"] = "coordenadas_originales_crs_documentado" if crs else "sin_crs_documentado"
    valid = output.geografia_operativa
    services = gpd.GeoDataFrame({
        "servicio_id": output.loc[valid, "registro_id"].tolist(),
        "categoria": output.loc[valid, "categoria_servicio"].tolist(),
        "codigo_ine": output.loc[valid, "codigo_ine_geografico_operativo"].tolist(),
        "nombre": output.loc[valid, "nombre_centro"].tolist() if "nombre_centro" in output else [""] * int(valid.sum()),
        "direccion": output.loc[valid, "direccion"].tolist() if "direccion" in output else [""] * int(valid.sum()),
        "fuente": output.loc[valid, "fuente"].tolist(),
        "metodo_localizacion": output.loc[valid, "metodo_localizacion"].tolist(),
    }, geometry=[point for point, use in zip(geometry, operations) if use], crs=METRIC_CRS)
    summary = {"registros": len(output), "crs_confirmado": crs is not None,
               "geografia_operativa": int(valid.sum()),
               "estados": {str(k): int(v) for k, v in output.estado_geolocalizacion.value_counts().items()},
               "categorias": {str(k): int(v) for k, v in output.categoria_servicio.value_counts().items()}}
    return output, services, issues, summary


def prepare_centers_geography(master, integration_dir, municipalities, shared_areas, evidence):
    """Prepara tablas sin escritura ni red; evidencia por educacion/sanidad.

    Cada entrada de evidencia usa confirmed, epsg, url, sha256 y evidence.
    Sanidad sin CRS probado se conserva íntegra pero no produce puntos operativos.
    """
    require_metric_crs(municipalities, "municipios")
    require_metric_crs(shared_areas, "jurisdicciones compartidas")
    known = set(master.codigo_ine)
    if known != set(municipalities.codigo_ine):
        raise IntegrationError("El maestro y la cartografía no cubren los mismos municipios.")
    tables, service_parts, issues, summary = {}, [], [], {}
    for source, filename in [("educacion", "centros_educativos_alta.csv"),
                             ("sanidad", "centros_sanitarios.csv")]:
        frame = _read_centers(Path(integration_dir) / filename)
        output, services, source_issues, source_summary = _prepare_one(
            frame, source, evidence.get(source, {}), municipalities, shared_areas, known)
        tables["centros_educativos_geografia.csv" if source == "educacion" else
               "centros_sanitarios_geografia.csv"] = output
        service_parts.append(services)
        issues.extend(source_issues)
        summary[source] = source_summary
    services = gpd.GeoDataFrame(pd.concat(service_parts, ignore_index=True), crs=METRIC_CRS)
    if not services.servicio_id.is_unique:
        raise IntegrationError("Los identificadores de servicios geográficos deben ser únicos.")
    summary["umbral_diagnostico_limite_m"] = BOUNDARY_DIAGNOSTIC_METERS
    summary["politica_discrepancias"] = "Todas se excluyen de cercanía, incluso a menos de 100 m."
    return {"tables": tables, "services": services, "issues": issues, "summary": summary}
