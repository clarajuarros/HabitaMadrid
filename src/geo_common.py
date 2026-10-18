"""Asignación geográfica explícita, sin ajustar puntos al municipio más cercano."""
import json

import geopandas as gpd
import pandas as pd
from pyproj import CRS

from src.integration_common import IntegrationError

METRIC_CRS = "EPSG:25830"


def require_metric_crs(frame, label):
    if not isinstance(frame, gpd.GeoDataFrame) or frame.crs is None:
        raise IntegrationError(label + ": se requiere GeoDataFrame con CRS declarado.")
    if CRS(frame.crs) != CRS(METRIC_CRS):
        raise IntegrationError(label + ": se requiere EPSG:25830; no se miden grados como metros.")


def assign_points(points, municipalities, shared_areas):
    """Devuelve candidatos por intersección y deja sin clave toda ambigüedad.

    Un límite entre municipios o una jurisdicción compartida no recibe el primer
    código encontrado. Tampoco se aplica un buffer para atraer puntos exteriores.
    """
    for label, frame in [("puntos", points), ("municipios", municipalities),
                         ("jurisdicciones compartidas", shared_areas)]:
        require_metric_crs(frame, label)
    if "registro_id" not in points or points.registro_id.isna().any() or not points.registro_id.is_unique:
        raise IntegrationError("Los puntos requieren un registro_id único y no nulo.")
    if "codigo_ine" not in municipalities or not municipalities.codigo_ine.is_unique:
        raise IntegrationError("La cartografía municipal requiere códigos únicos.")
    if len(shared_areas) and not {"codigo_ine_1", "codigo_ine_2"}.issubset(shared_areas):
        raise IntegrationError("Faltan los participantes de las jurisdicciones compartidas.")
    for frame in (municipalities, shared_areas):
        if (frame.geometry.isna().any() or frame.geometry.is_empty.any() or
                not frame.geometry.is_valid.all()):
            raise IntegrationError("La asignación necesita polígonos válidos y no vacíos.")
        if not frame.geometry.geom_type.isin(["Polygon", "MultiPolygon"]).all():
            raise IntegrationError("La asignación requiere áreas poligonales.")
    known = set(municipalities.codigo_ine)
    if len(shared_areas) and (set(shared_areas.codigo_ine_1) | set(shared_areas.codigo_ine_2)) - known:
        raise IntegrationError("Una jurisdicción compartida referencia un municipio desconocido.")
    rows = []
    municipal_index, shared_index = municipalities.sindex, shared_areas.sindex
    for row in points.itertuples(index=False):
        geom = row.geometry
        if geom is None or geom.is_empty or not geom.is_valid:
            candidates, code, state = [], "", "sin_coordenadas"
        elif geom.geom_type != "Point":
            raise IntegrationError("La asignación solo admite geometrías Point.")
        else:
            positions = municipal_index.query(geom, predicate="intersects")
            shared_positions = shared_index.query(geom, predicate="intersects")
            candidate_set = set(municipalities.iloc[positions].codigo_ine)
            for position in shared_positions:
                area = shared_areas.iloc[position]
                candidate_set.update([area.codigo_ine_1, area.codigo_ine_2])
            candidates = sorted(candidate_set)
            if len(shared_positions):
                code, state = "", "jurisdiccion_compartida"
            elif len(candidates) == 1:
                code, state = candidates[0], "municipio_unico"
            elif len(candidates) > 1:
                code, state = "", "limite_entre_municipios"
            else:
                code, state = "", "sin_coincidencia"
        rows.append({"registro_id": row.registro_id, "codigo_ine_espacial": code,
                     "estado_asignacion_espacial": state,
                     "codigos_candidatos_json": json.dumps(candidates, ensure_ascii=False)})
    return pd.DataFrame(rows, columns=["registro_id", "codigo_ine_espacial",
                                      "estado_asignacion_espacial", "codigos_candidatos_json"])
