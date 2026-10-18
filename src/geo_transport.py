"""Localización de las paradas GTFS con municipios y ambigüedades explícitos."""
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

from src.geo_common import assign_points
from src.integration_common import IntegrationError, attach_municipal_context, make_issue


def prepare_stop_geography(master, integration_dir, municipalities, shared_areas):
    path = Path(integration_dir) / "transporte_paradas.csv"
    raw = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    required = {"stop_id", "nombre", "direccion_fuente", "latitud", "longitud",
                "crs_declarado", "tipo_parada", "fuente", "fila_fuente"}
    if not required.issubset(raw):
        raise IntegrationError("La tabla GTFS integrada no contiene los campos requeridos.")
    if not raw.stop_id.is_unique or raw.stop_id.eq("").any():
        raise IntegrationError("Las paradas requieren stop_id único y no vacío.")
    if not raw.crs_declarado.eq("EPSG:4326").all():
        raise IntegrationError("Las coordenadas GTFS deben declarar EPSG:4326.")
    raw["registro_id"] = "transporte:" + raw.stop_id
    lat = pd.to_numeric(raw.latitud, errors="coerce")
    lon = pd.to_numeric(raw.longitud, errors="coerce")
    valid = lat.between(-90, 90) & lon.between(-180, 180)
    points = [Point(x, y) if ok else None for x, y, ok in zip(lon, lat, valid)]
    spatial = gpd.GeoDataFrame(raw.copy(), geometry=points, crs=4326).to_crs(25830)
    projected_valid = (spatial.geometry.notna() & spatial.geometry.is_valid &
                       np.isfinite(spatial.geometry.x) & np.isfinite(spatial.geometry.y))
    projection_failed = valid.to_numpy() & ~projected_valid.to_numpy()
    spatial.loc[~projected_valid, "geometry"] = None
    assigned = assign_points(spatial, municipalities, shared_areas)
    # La fase 3 deja municipio vacío; se conserva la decisión espacial completa.
    spatial = spatial.drop(columns=[c for c in ["estado_asignacion_municipal"] if c in spatial])
    spatial = spatial.merge(assigned, on="registro_id", validate="one_to_one")
    spatial["codigo_ine"] = spatial.codigo_ine_espacial
    spatial["estado_asignacion_municipal"] = spatial.estado_asignacion_espacial
    spatial.loc[projection_failed, "estado_asignacion_municipal"] = "transformacion_fallida"
    spatial = attach_municipal_context(spatial, master)
    spatial = gpd.GeoDataFrame(spatial, geometry="geometry", crs=25830)
    spatial["metodo_localizacion"] = "Coordenadas GTFS WGS84, transformadas a ETRS89 UTM30"
    spatial["apta_proximidad"] = (spatial.estado_asignacion_espacial.eq("municipio_unico") &
                                  spatial.tipo_parada.eq("0") & valid.to_numpy())
    issues = []
    for row in spatial.loc[~spatial.estado_asignacion_espacial.eq("municipio_unico")].itertuples(index=False):
        issues.append(make_issue("crtm_interurbanos_gtfs", "parada_" + row.estado_asignacion_municipal,
                                 registro=row.registro_id, campo="codigo_ine_espacial",
                                 valor_original=row.codigos_candidatos_json, fila_fuente=row.fila_fuente,
                                 detalle="Se conserva el punto; no se asigna municipio por proximidad ni orden de códigos."))
    frame = pd.DataFrame(spatial.drop(columns="geometry"))
    usable = spatial.loc[spatial.apta_proximidad].copy()
    services = gpd.GeoDataFrame({
        "servicio_id": usable.registro_id, "categoria": "parada_bus",
        "codigo_ine": usable.codigo_ine, "nombre": usable.nombre,
        "direccion": usable.direccion_fuente, "fuente": usable.fuente,
        "metodo_localizacion": usable.metodo_localizacion,
    }, geometry=usable.geometry, crs=25830).reset_index(drop=True)
    return {"tables": {"paradas_geolocalizadas.csv": frame}, "points": spatial,
            "services": services, "issues": issues,
            "summary": {"paradas_y_estaciones": len(spatial), "crs_entrada": "EPSG:4326",
                        "crs_calculo": "EPSG:25830",
                        "asignaciones": spatial.estado_asignacion_espacial.value_counts().to_dict(),
                        "paradas_aptas_proximidad": len(services),
                        "estaciones_excluidas_proximidad": int(spatial.tipo_parada.ne("0").sum()),
                        "servicios_elegibles": int(usable.elegible_hasta_50000.fillna(False).sum()),
                        "alcance": "Feed parcial interurbano; no se evalúan horarios ni accesibilidad física."}}
