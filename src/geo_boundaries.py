"""Límites completos y puntos de referencia trazables, sin adjudicar zonas comunes.

Las superficies y operaciones se calculan en ETRS89 / UTM 30N (EPSG:25830).
Un núcleo EIEL sirve para elegir un punto de referencia, no para distribuir la
población del padrón ni para representar el domicilio de cada habitante.
"""
from collections import Counter
import json
import math
from pathlib import Path
import re

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import CRS, Transformer
from shapely import make_valid, transform
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, shape
from shapely.ops import unary_union
from shapely.validation import explain_validity

from src.integration_common import IntegrationError, make_issue

METRIC_CRS = "EPSG:25830"
TERM_DESCRIPTIONS = {"Término Municipal", "Termino Municipal"}
BOUNDARY_FILE = "divisiones_administrativas.geojson"
NUCLEI_FILE = "nucleos_poblacion_eiel_2023.geojson"
OVERLAP_COLUMNS = ["tipo_a", "id_a", "tipo_b", "id_b", "area_m2", "clasificacion"]


def _geojson(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise IntegrationError(f"No se pudo leer el GeoJSON: {path}") from exc
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection" or not isinstance(data.get("features"), list):
        raise IntegrationError(f"Se esperaba FeatureCollection: {path}")
    crs = data.get("crs")
    crs_props = crs.get("properties") if isinstance(crs, dict) else None
    crs_name = crs_props.get("name") if isinstance(crs_props, dict) else None
    try:
        valid_crs = bool(crs_name) and CRS.from_user_input(crs_name).equals(CRS.from_epsg(4326))
    except (ValueError, TypeError):
        valid_crs = False
    if not valid_crs:
        raise IntegrationError(f"{path}: se requiere CRS EPSG:4326 explícito; recibido {crs_name!r}")
    return data["features"]


def _polygon_parts(geometry):
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, (MultiPolygon, GeometryCollection)):
        return [part for item in geometry.geoms for part in _polygon_parts(item)]
    return []


def _project(geometry, transformer):
    # ndarray (n,2) mantiene compatibilidad con Shapely 2.0 y posteriores.
    return transform(geometry, lambda xy: np.column_stack(transformer.transform(xy[:, 0], xy[:, 1])))


def _checked_polygon(feature, source, record, controls, issues, allow_missing=False):
    """Repara solo geometrías inválidas, dejando antes/después y área trazados."""
    try:
        geometry = shape(feature.get("geometry"))
        if not isinstance(geometry, (Polygon, MultiPolygon)) or geometry.is_empty:
            raise ValueError("se requiere Polygon o MultiPolygon no vacío")
        minx, miny, maxx, maxy = geometry.bounds
        if not (-180 <= minx <= maxx <= 180 and -90 <= miny <= maxy <= 90):
            raise ValueError("coordenadas incompatibles con longitud/latitud EPSG:4326")
        transformer = Transformer.from_crs(4326, 25830, always_xy=True)
        metric = _project(geometry, transformer)
        if metric.is_empty or not all(math.isfinite(value) for value in metric.bounds):
            raise ValueError("transformación vacía o no finita")
    except (ValueError, TypeError, AttributeError) as exc:
        if not allow_missing:
            raise IntegrationError(f"{source}, registro {record}: geometría no utilizable ({exc})") from exc
        issues.append(make_issue(source, "geometria_no_utilizable", record, "geometry", detalle=str(exc)))
        controls.append({"fuente": source, "registro": record, "valida_antes": False,
                         "reparada": False, "valida_despues": False, "area_antes_m2": None,
                         "area_despues_m2": None, "cambio_area_m2": None,
                         "detalle": str(exc)})
        return None, False
    before = metric.area
    source_valid, metric_valid = geometry.is_valid, metric.is_valid
    valid_before = source_valid and metric_valid
    detail_parts, methods = [], []
    if not source_valid:
        detail_parts.append("fuente: " + explain_validity(geometry))
        parts = _polygon_parts(make_valid(geometry))
        if not parts:
            raise IntegrationError(f"{source}, registro {record}: reparación sin superficie poligonal")
        metric = _project(unary_union(parts), transformer)
        methods.append("make_valid_EPSG4326")
    if not metric.is_valid:
        detail_parts.append("proyección: " + explain_validity(metric))
        parts = _polygon_parts(make_valid(metric))
        if not parts:
            raise IntegrationError(f"{source}, registro {record}: reparación sin superficie poligonal")
        metric = unary_union(parts)
        methods.append("make_valid_EPSG25830")
    detail = "; ".join(detail_parts)
    if not valid_before:
        issues.append(make_issue(source, "geometria_reparada", record, "geometry", detalle=(
            f"make_valid: {detail}; área antes={before:.6f} m²; "
            f"área después={metric.area:.6f} m²; se conservan componentes poligonales.")))
    if not metric.is_valid or metric.is_empty or metric.area <= 0:
        raise IntegrationError(f"{source}, registro {record}: geometría sin superficie válida")
    controls.append({"fuente": source, "registro": record, "valida_antes": valid_before,
                     "valida_fuente": source_valid, "valida_tras_proyeccion_original": metric_valid,
                     "reparada": not valid_before, "valida_despues": True,
                     "metodo_reparacion": ";".join(methods),
                     "area_antes_m2": before, "area_despues_m2": metric.area,
                     "cambio_area_m2": metric.area - before, "detalle": detail})
    return metric, not valid_before


def _code(value, master_codes, source):
    code = str(value or "")
    if re.fullmatch(r"28[0-9]{3}", code) is None or code not in master_codes:
        raise IntegrationError(f"{source}: código municipal ausente o desconocido {value!r}")
    return code


def _overlaps(municipalities, shared):
    """Mide solapes de superficie; los límites que solo se tocan no son solape."""
    items = [("municipio", row.codigo_ine, row.geometry) for row in municipalities.itertuples()]
    items += [("jurisdiccion_compartida", row.area_compartida_id, row.geometry)
              for row in shared.itertuples()]
    rows = []
    for i, (kind_a, id_a, geom_a) in enumerate(items):
        for kind_b, id_b, geom_b in items[i + 1:]:
            if not geom_a.intersects(geom_b):
                continue
            area = geom_a.intersection(geom_b).area
            if area > 0.000001:
                rows.append({"tipo_a": kind_a, "id_a": id_a, "tipo_b": kind_b,
                             "id_b": id_b, "area_m2": area,
                             "clasificacion": "submetrico" if area < 1 else "superficie_solapada"})
    return pd.DataFrame(rows, columns=OVERLAP_COLUMNS)


def _eligible(value):
    if value is True or str(value).lower() == "true":
        return True
    if value is False or str(value).lower() == "false":
        return False
    raise IntegrationError(f"Bandera de elegibilidad no booleana: {value!r}")


def prepare_boundaries(master, raw_dir):
    """Devuelve capas métricas, auditoría y orígenes; no escribe archivos.

    El maestro establece el universo (179 en producción), sin constantes que
    impidan verificar el contrato con ejemplos pequeños. Se exige un término
    principal por clave; sus enclaves se unen y las zonas compartidas se separan.
    """
    required = {"codigo_ine", "municipio", "elegible_hasta_50000"}
    if not required.issubset(master.columns) or master.empty or not master["codigo_ine"].is_unique:
        raise IntegrationError("Maestro vacío, incompleto o con códigos duplicados")
    codes = set(master["codigo_ine"])
    if any(re.fullmatch(r"28[0-9]{3}", str(code)) is None for code in codes):
        raise IntegrationError("El maestro debe conservar códigos INE de cinco caracteres de Madrid")
    eligible = master["elegible_hasta_50000"].map(_eligible)
    raw_dir = Path(raw_dir)
    features = _geojson(raw_dir / BOUNDARY_FILE)
    issues, controls, part_rows, shared_rows = [], [], [], []
    terms, parts, repaired_codes = {}, {}, set()
    for index, feature in enumerate(features, start=1):
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise IntegrationError(f"{BOUNDARY_FILE}: registro {index} no es un Feature")
        props = feature.get("properties") or {}
        desc = props.get("DS_DESCRIPCION", "")
        code = _code(props.get("CD_INE"), codes, BOUNDARY_FILE)
        geom, repaired = _checked_polygon(feature, BOUNDARY_FILE, str(index), controls, issues)
        if desc in TERM_DESCRIPTIONS or desc == "Enclave":
            if str(props.get("CD_MUNICIPIO", "")) != code[2:]:
                raise IntegrationError(f"{BOUNDARY_FILE}: CD_MUNICIPIO incoherente con {code}")
            if desc in TERM_DESCRIPTIONS:
                if code in terms:
                    raise IntegrationError(f"Más de un término principal para {code}")
                terms[code] = geom
            parts.setdefault(code, []).append(geom)
            if repaired:
                repaired_codes.add(code)
            part_rows.append({"registro_fuente": index, "codigo_ine": code,
                              "tipo": "enclave" if desc == "Enclave" else "termino_principal",
                              "nombre": props.get("DS_NOMBRE", ""),
                              "enclave_nombre": props.get("DS_ENCLAVE") or "",
                              "area_m2": geom.area, "geometria_reparada": repaired})
        elif desc.startswith("Comunidad Jurisdiccional"):
            second = _code(props.get("CD_INE_1"), codes, BOUNDARY_FILE)
            if second == code:
                raise IntegrationError("Una jurisdicción compartida requiere dos municipios distintos")
            pair = sorted((code, second))
            shared_rows.append({"area_compartida_id": f"compartida:{props.get('CD_NATCODE') or index}",
                                "codigo_ine_1": pair[0], "codigo_ine_2": pair[1],
                                "codigos_ine_participantes": ";".join(pair),
                                "nombre": props.get("DS_NOMBRE", ""), "descripcion": desc,
                                "area_m2": geom.area, "geometry": geom})
        else:
            raise IntegrationError(f"Tipo de división administrativa desconocido: {desc!r}")
    if set(terms) != codes:
        raise IntegrationError(f"Cobertura de términos distinta del maestro: faltan {sorted(codes-set(terms))}")
    municipal_rows = []
    part_table = pd.DataFrame(part_rows)
    for _, row in master.sort_values("codigo_ine").iterrows():
        code = row["codigo_ine"]
        united = unary_union(parts[code])
        if not united.is_valid or united.is_empty:
            raise IntegrationError(f"Unión de término y enclaves inválida para {code}")
        record = row.to_dict()
        record.update(geometry=united, partes_fuente=len(parts[code]),
                      enclaves_count=int(((part_table.codigo_ine == code) & (part_table.tipo == "enclave")).sum()),
                      area_m2=united.area, geometria_reparada=code in repaired_codes,
                      area_partes_antes_union_m2=sum(part.area for part in parts[code]))
        municipal_rows.append(record)
    municipalities = gpd.GeoDataFrame(municipal_rows, geometry="geometry", crs=METRIC_CRS)
    shared_columns = ["area_compartida_id", "codigo_ine_1", "codigo_ine_2", "codigos_ine_participantes",
                      "nombre", "descripcion", "area_m2", "geometry"]
    shared = gpd.GeoDataFrame(shared_rows, columns=shared_columns, geometry="geometry", crs=METRIC_CRS)
    if not shared.area_compartida_id.is_unique:
        raise IntegrationError("Identificador de jurisdicción compartida duplicado")
    overlaps = _overlaps(municipalities, shared)
    for row in overlaps.itertuples():
        issues.append(make_issue(BOUNDARY_FILE, "solape_superficial", f"{row.id_a}|{row.id_b}",
                                 "geometry", detalle=f"{row.area_m2:.6f} m²; {row.clasificacion}. No se reasigna territorio."))

    nucleus_rows = []
    seen_nuclei = set()
    for index, feature in enumerate(_geojson(raw_dir / NUCLEI_FILE), start=1):
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise IntegrationError(f"{NUCLEI_FILE}: registro {index} no es un Feature")
        props = feature.get("properties") or {}
        prov, mun = str(props.get("PROV", "")), str(props.get("MUN", ""))
        if re.fullmatch(r"[0-9]{2}", prov) is None or re.fullmatch(r"[0-9]{3}", mun) is None:
            raise IntegrationError(f"{NUCLEI_FILE}, registro {index}: PROV/MUN mal formados")
        code = _code(prov + mun, codes, NUCLEI_FILE)
        ent, nuc = str(props.get("ENT", "")), str(props.get("NUCLEO", ""))
        if re.fullmatch(r"[0-9]{4}", ent) is None or re.fullmatch(r"[0-9]{2}", nuc) is None:
            raise IntegrationError(f"{NUCLEI_FILE}, registro {index}: ENT/NUCLEO mal formados")
        key = f"{code}:{ent}:{nuc}"
        if key in seen_nuclei:
            raise IntegrationError(f"Clave de núcleo duplicada: {key}")
        seen_nuclei.add(key)
        year = str(props.get("FASE", ""))
        if year != "2023":
            raise IntegrationError(f"{NUCLEI_FILE}: año distinto del contrato 2023: {year!r}")
        pop_raw = props.get("PADRON")
        pop = int(pop_raw) if re.fullmatch(r"[0-9]+", str(pop_raw)) else None
        if pop is None:
            issues.append(make_issue(NUCLEI_FILE, "poblacion_nucleo_no_utilizable", key,
                                     "PADRON", pop_raw, "No se emplea este núcleo para elegir el origen."))
        geom, repaired = _checked_polygon(feature, NUCLEI_FILE, key, controls, issues, allow_missing=True)
        nucleus_rows.append({"nucleo_clave": key, "codigo_ine": code,
                             "nucleo_nombre": props.get("NUCLEOS", ""),
                             "municipio_fuente": props.get("MUNICIPIO", ""),
                             "nucleo_poblacion": pop, "nucleo_poblacion_original": pop_raw,
                             "nucleo_anio": 2023, "geometria_reparada": repaired,
                             "geometria_disponible": geom is not None,
                             "area_m2": geom.area if geom is not None else None,
                             "registro_fuente": index, "geometry": geom})
    nuclei_columns = ["nucleo_clave", "codigo_ine", "nucleo_nombre", "municipio_fuente",
                      "nucleo_poblacion", "nucleo_poblacion_original", "nucleo_anio",
                      "geometria_reparada", "geometria_disponible", "area_m2", "registro_fuente", "geometry"]
    nuclei = gpd.GeoDataFrame(nucleus_rows, columns=nuclei_columns, geometry="geometry", crs=METRIC_CRS)
    nuclei["nucleo_poblacion"] = pd.array(nuclei["nucleo_poblacion"], dtype="Int64")
    origins = _origins(master.loc[eligible], municipalities, shared, terms, nuclei, issues)
    control_table = pd.DataFrame(controls)
    summary = {
        "crs_entrada": "EPSG:4326", "crs_calculo": METRIC_CRS,
        "divisiones_fuente": len(features), "terminos_principales": len(terms),
        "enclaves_conservados": int((part_table.tipo == "enclave").sum()),
        "municipios_completos": len(municipalities), "jurisdicciones_compartidas_separadas": len(shared),
        "geometrias_reparadas": int(control_table.reparada.sum()),
        "geometrias_invalidas_fuente_limites": int(((control_table.fuente == BOUNDARY_FILE) & (control_table.valida_fuente == False)).sum()),
        "geometrias_invalidas_fuente_nucleos": int(((control_table.fuente == NUCLEI_FILE) & (control_table.valida_fuente == False)).sum()),
        "cambio_area_reparacion_limites_m2": float(control_table.loc[control_table.fuente == BOUNDARY_FILE, "cambio_area_m2"].sum()),
        "cambio_area_reparacion_nucleos_m2": float(control_table.loc[control_table.fuente == NUCLEI_FILE, "cambio_area_m2"].sum()),
        "area_partes_municipales_m2": float(part_table.area_m2.sum()),
        "area_municipios_unidos_m2": float(municipalities.area_m2.sum()),
        "area_jurisdicciones_compartidas_m2": float(shared.area_m2.sum()),
        "solapes_superficiales": len(overlaps), "area_solapes_m2": float(overlaps.area_m2.sum()),
        "nucleos_eiel": len(nuclei), "origenes_elegibles": len(origins),
        "origenes_por_metodo": dict(Counter(origins.metodo_origen)),
        "regla_origen": "Mayor PADRON EIEL 2023 con geometría utilizable; empate por clave. Punto interior de intersección con área municipal exclusiva.",
        "limitacion_origen": "Punto de referencia del núcleo principal, sin ponderación de población ni representación de todos los domicilios; no son tiempos de viaje.",
    }
    return {"municipalities": municipalities, "shared_areas": shared, "origins": origins,
            "nuclei": nuclei, "tables": {"partes_limites.csv": part_table,
                "control_geometrias.csv": control_table, "solapes_limites.csv": overlaps,
                "nucleos_eiel.csv": nuclei.drop(columns="geometry")},
            "issues": issues, "summary": summary}


def _origins(eligible, municipalities, shared, terms, nuclei, issues):
    rows = []
    lookup = municipalities.set_index("codigo_ine")
    to_lonlat = Transformer.from_crs(25830, 4326, always_xy=True)
    shared_union = unary_union(list(shared.geometry))
    for _, master_row in eligible.sort_values("codigo_ine").iterrows():
        code = master_row.codigo_ine
        own = lookup.loc[code].geometry
        competitors = [geom for other, geom in zip(municipalities.codigo_ine, municipalities.geometry)
                       if other != code and own.intersects(geom)]
        unsafe = unary_union(competitors + ([shared_union] if not shared_union.is_empty else []))
        safe = own.difference(unsafe)
        candidates = nuclei.loc[(nuclei.codigo_ine == code) & nuclei.nucleo_poblacion.notna()].sort_values(
            ["nucleo_poblacion", "nucleo_clave"], ascending=[False, True])
        selected, point, warning = None, None, ""
        for _, candidate in candidates.iterrows():
            geometry = candidate.geometry
            if geometry is None or geometry.is_empty:
                continue
            usable = geometry.intersection(safe)
            if usable.is_empty or usable.area <= 0:
                issues.append(make_issue(NUCLEI_FILE, "nucleo_fuera_area_exclusiva", candidate.nucleo_clave,
                                         "geometry", detalle="No ofrece superficie en su municipio sin solapes ni jurisdicción compartida."))
                continue
            selected, point = candidate, usable.representative_point()
            if geometry.difference(safe).area > 0.000001:
                warning = "nucleo_recortado_al_area_municipal_exclusiva"
                issues.append(make_issue(NUCLEI_FILE, warning, candidate.nucleo_clave, "geometry",
                                         detalle="Solo se utiliza la intersección para elegir un punto interior; no se cambia el núcleo fuente."))
            break
        if point is None:
            usable = terms[code].intersection(safe)
            if usable.is_empty or usable.area <= 0:
                raise IntegrationError(f"No se puede obtener un origen interior exclusivo para {code}")
            point = usable.representative_point()
            warning = "sin_nucleo_eiel_utilizable; referencia_del_termino_principal"
            issues.append(make_issue(NUCLEI_FILE, "origen_sin_nucleo", code, "geometry", detalle=warning))
        if not safe.contains(point):
            raise IntegrationError(f"Origen de {code} fuera de su superficie municipal exclusiva")
        lon, lat = to_lonlat.transform(point.x, point.y)
        rows.append({"origen_id": f"origen:{code}", "codigo_ine": code,
                     "municipio": master_row.municipio, "elegible_hasta_50000": True,
                     "metodo_origen": "nucleo_eiel_principal" if selected is not None else "interior_termino_principal",
                     "fuente_origen": NUCLEI_FILE if selected is not None else BOUNDARY_FILE,
                     "nucleo_clave": selected.nucleo_clave if selected is not None else "",
                     "nucleo_nombre": selected.nucleo_nombre if selected is not None else "",
                     "nucleo_poblacion": selected.nucleo_poblacion if selected is not None else None,
                     "nucleo_anio": 2023 if selected is not None else None,
                     "origen_advertencia": warning, "x_etrs89_utm30": point.x,
                     "y_etrs89_utm30": point.y, "longitud": lon, "latitud": lat,
                     "geometry": point})
    origin_columns = ["origen_id", "codigo_ine", "municipio", "elegible_hasta_50000",
                      "metodo_origen", "fuente_origen", "nucleo_clave", "nucleo_nombre",
                      "nucleo_poblacion", "nucleo_anio", "origen_advertencia", "x_etrs89_utm30",
                      "y_etrs89_utm30", "longitud", "latitud", "geometry"]
    result = gpd.GeoDataFrame(rows, columns=origin_columns, geometry="geometry", crs=METRIC_CRS)
    result["nucleo_poblacion"] = pd.array(result.nucleo_poblacion, dtype="Int64")
    result["nucleo_anio"] = pd.array(result.nucleo_anio, dtype="Int64")
    return result
