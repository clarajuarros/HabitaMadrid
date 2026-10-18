"""Vecinos por distancia euclídea desde puntos representativos municipales.

Estos resultados describen proximidad geométrica. No son rutas, tiempos de
viaje, accesibilidad de toda la población ni disponibilidad asistencial.
"""
import numbers

import geopandas as gpd
import numpy as np
import pandas as pd

from src.integration_common import IntegrationError, make_issue


CATEGORIES = (
    "educacion", "centro_salud", "consultorio", "hospital",
    "sanidad_otros", "parada_bus",
)
DISTANCE_KIND = "euclidea_desde_origen_representativo"
PROXIMITY_COLUMNS = [
    "origen_id", "codigo_ine_origen", "municipio_origen", "metodo_origen",
    "fuente_origen", "categoria", "puesto", "servicio_id",
    "codigo_ine_destino", "municipio_destino", "mismo_municipio",
    "destino_elegible_hasta_50000", "nombre_servicio", "estado_nombre_servicio",
    "direccion_servicio", "fuente_servicio", "metodo_localizacion_servicio",
    "distancia_m", "tipo_distancia",
]
COVERAGE_COLUMNS = [
    "origen_id", "codigo_ine_origen", "municipio_origen", "metodo_origen",
    "fuente_origen", "categoria", "estado", "candidatos_totales_categoria",
    "candidatos_mismo_municipio", "candidatos_otros_municipios",
    "resultados_globales", "resultados_otros_municipios",
]


def _text(value):
    return "" if value is None or pd.isna(value) else str(value).strip()


def _eligibility(value):
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, str) and value.lower().strip() in {"true", "false"}:
        return value.lower().strip() == "true"
    raise IntegrationError("La elegibilidad municipal debe ser booleana y estar informada.")


def _validate_points(frame, label, identifier, required):
    if not isinstance(frame, gpd.GeoDataFrame):
        raise IntegrationError(f"{label} debe ser un GeoDataFrame.")
    missing = set(required) - set(frame.columns)
    if missing:
        raise IntegrationError(f"Faltan columnas en {label}: {', '.join(sorted(missing))}.")
    if frame.crs is None or frame.crs.to_epsg() != 25830:
        raise IntegrationError(f"{label} debe utilizar EPSG:25830; no se calculan distancias en grados.")
    ids = frame[identifier].map(_text)
    if ids.eq("").any() or ids.duplicated().any():
        raise IntegrationError(f"{label} contiene identificadores vacíos o duplicados: {identifier}.")
    if len(frame):
        geometry = frame.geometry
        if (geometry.isna().any() or geometry.is_empty.any()
                or not geometry.is_valid.all() or not geometry.geom_type.eq("Point").all()):
            raise IntegrationError(f"{label} debe contener únicamente puntos válidos y completos.")
        if not np.isfinite(np.column_stack([geometry.x, geometry.y])).all():
            raise IntegrationError(f"{label} contiene coordenadas no finitas.")


def compute_proximity(origins, services, master, k=3):
    """Calcula los k servicios más cercanos, globales y de otros municipios.

    Entradas ya verificadas en EPSG:25830. Se conservan todos los destinos del
    maestro, incluidos los municipios de más de 50.000 habitantes. Solo los
    orígenes elegibles producen resultados. En cada empate exacto decide el
    identificador del servicio, de forma estable y explícita.

    Si falta el origen de un municipio elegible se publica su cobertura como
    ``sin_origen``. Si no hay candidatos de una categoría se publica
    ``sin_candidatos`` y ninguna distancia. No se generan servicios ficticios.
    Las coordenadas inválidas deben separarse antes de invocar esta función;
    detectarlas aquí es un incumplimiento del contrato y detiene el cálculo.
    """
    if isinstance(k, bool) or not isinstance(k, numbers.Integral) or k < 1:
        raise IntegrationError("k debe ser un entero positivo.")
    required_master = {"codigo_ine", "municipio", "elegible_hasta_50000"}
    if not required_master.issubset(master.columns):
        raise IntegrationError("El maestro carece de codigo_ine, municipio o elegibilidad.")
    master = master.copy()
    master["codigo_ine"] = master["codigo_ine"].map(_text)
    if master["codigo_ine"].eq("").any() or master["codigo_ine"].duplicated().any():
        raise IntegrationError("El maestro contiene códigos municipales vacíos o duplicados.")
    master["elegible_hasta_50000"] = master["elegible_hasta_50000"].map(_eligibility)
    lookup = master.set_index("codigo_ine")
    _validate_points(origins, "origenes", "origen_id",
                     {"origen_id", "codigo_ine", "metodo_origen", "fuente_origen"})
    _validate_points(services, "servicios", "servicio_id", {
        "servicio_id", "categoria", "codigo_ine", "nombre", "direccion",
        "fuente", "metodo_localizacion",
    })
    origins, services = origins.copy(), services.copy()
    for frame, identifier in [(origins, "origen_id"), (services, "servicio_id")]:
        frame[identifier] = frame[identifier].map(_text)
        frame["codigo_ine"] = frame["codigo_ine"].map(_text)
        if not frame["codigo_ine"].isin(lookup.index).all():
            raise IntegrationError("Hay orígenes o servicios sin municipio confirmado en el maestro.")
    if not services["categoria"].isin(CATEGORIES).all():
        raise IntegrationError("Los servicios contienen categorías desconocidas.")
    origins = origins.loc[origins["codigo_ine"].map(lookup["elegible_hasta_50000"])].copy()
    origins = origins.sort_values(["codigo_ine", "origen_id"], kind="stable")
    services = services.sort_values("servicio_id", kind="stable").reset_index(drop=True)
    candidates = {category: services.loc[services["categoria"].eq(category)].reset_index(drop=True)
                  for category in CATEGORIES}
    global_rows, neighboring_rows, coverage, issues = [], [], [], []

    def make_row(origin, service, distance, position):
        destination_code = service["codigo_ine"]
        name = _text(service["nombre"])
        name_state = _text(service.get("estado_nombre", ""))
        return {
            "origen_id": origin["origen_id"], "codigo_ine_origen": origin["codigo_ine"],
            "municipio_origen": lookup.loc[origin["codigo_ine"], "municipio"],
            "metodo_origen": _text(origin["metodo_origen"]),
            "fuente_origen": _text(origin["fuente_origen"]),
            "categoria": service["categoria"], "puesto": position,
            "servicio_id": service["servicio_id"], "codigo_ine_destino": destination_code,
            "municipio_destino": lookup.loc[destination_code, "municipio"],
            "mismo_municipio": destination_code == origin["codigo_ine"],
            "destino_elegible_hasta_50000": bool(lookup.loc[destination_code, "elegible_hasta_50000"]),
            "nombre_servicio": name,
            "estado_nombre_servicio": name_state or ("publicado" if name else "no_publicado_en_fuente"),
            "direccion_servicio": _text(service["direccion"]),
            "fuente_servicio": _text(service["fuente"]),
            "metodo_localizacion_servicio": _text(service["metodo_localizacion"]),
            "distancia_m": float(distance), "tipo_distancia": DISTANCE_KIND,
        }

    for _, origin in origins.iterrows():
        code = origin["codigo_ine"]
        for category in CATEGORIES:
            group = candidates[category]
            other = group["codigo_ine"].ne(code).to_numpy()
            if len(group):
                distances = np.hypot(group.geometry.x.to_numpy() - origin.geometry.x,
                                     group.geometry.y.to_numpy() - origin.geometry.y)
                # Toda la categoría se ordena: no se recorta antes de resolver empates.
                order = np.lexsort((group["servicio_id"].to_numpy(dtype=str), distances))
                global_order = order[:k]
                neighboring_order = order[other[order]][:k]
                for target, selection in [(global_rows, global_order),
                                          (neighboring_rows, neighboring_order)]:
                    for position, service_index in enumerate(selection, 1):
                        target.append(make_row(origin, group.iloc[service_index],
                                               distances[service_index], position))
            else:
                global_order, neighboring_order = [], []
            coverage.append({
                "origen_id": origin["origen_id"], "codigo_ine_origen": code,
                "municipio_origen": lookup.loc[code, "municipio"],
                "metodo_origen": _text(origin["metodo_origen"]),
                "fuente_origen": _text(origin["fuente_origen"]), "categoria": category,
                "estado": "candidatos_disponibles" if len(group) else "sin_candidatos",
                "candidatos_totales_categoria": len(group),
                "candidatos_mismo_municipio": int((~other).sum()),
                "candidatos_otros_municipios": int(other.sum()),
                "resultados_globales": len(global_order),
                "resultados_otros_municipios": len(neighboring_order),
            })

    eligible = master.loc[master["elegible_hasta_50000"], "codigo_ine"].sort_values()
    missing_origins = sorted(set(eligible) - set(origins["codigo_ine"]))
    for code in missing_origins:
        issues.append(make_issue("proximidad", "municipio_sin_origen", code, "geometry", "",
                                 "No se calculan distancias para este municipio elegible."))
        for category in CATEGORIES:
            group = candidates[category]
            others = group["codigo_ine"].ne(code)
            coverage.append({
                "origen_id": "", "codigo_ine_origen": code,
                "municipio_origen": lookup.loc[code, "municipio"],
                "metodo_origen": "", "fuente_origen": "", "categoria": category,
                "estado": "sin_origen", "candidatos_totales_categoria": len(group),
                "candidatos_mismo_municipio": int((~others).sum()),
                "candidatos_otros_municipios": int(others.sum()),
                "resultados_globales": 0, "resultados_otros_municipios": 0,
            })
    coverage_frame = pd.DataFrame(coverage, columns=COVERAGE_COLUMNS)
    coverage_frame = coverage_frame.sort_values(
        ["codigo_ine_origen", "origen_id", "categoria"], kind="stable").reset_index(drop=True)
    return {
        "tables": {
            "proximidad_servicios.csv": pd.DataFrame(global_rows, columns=PROXIMITY_COLUMNS),
            "proximidad_servicios_otros_municipios.csv": pd.DataFrame(neighboring_rows, columns=PROXIMITY_COLUMNS),
            "cobertura_proximidad.csv": coverage_frame,
        },
        "issues": issues,
        "summary": {
            "k": int(k), "crs_calculo": "EPSG:25830", "unidad_distancia": "metro",
            "tipo_distancia": DISTANCE_KIND,
            "origenes_elegibles": len(origins), "municipios_elegibles": len(eligible),
            "municipios_elegibles_con_origen": origins["codigo_ine"].nunique(),
            "municipios_elegibles_sin_origen": missing_origins,
            "servicios_considerados": len(services),
            "servicios_por_categoria": {category: len(group) for category, group in candidates.items()},
            "filas_proximidad_global": len(global_rows),
            "filas_proximidad_otros_municipios": len(neighboring_rows),
            "desempate": "distancia ascendente, servicio_id ascendente en empate exacto",
            "interpretacion": "Distancia en línea recta desde un punto representativo municipal; "
                "no mide recorridos, tiempos de viaje, capacidad ni accesibilidad de toda la población.",
            "destinos": "Todos los municipios del maestro, incluidos los no elegibles por población.",
        },
    }
