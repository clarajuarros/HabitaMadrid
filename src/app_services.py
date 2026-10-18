"""Listados offline de servicios publicados, sin geocodificar ni inferir horarios.

Los destinos conservan todos los municipios de la Comunidad de Madrid. El
origen de las distancias es el punto municipal preparado en la fase 4; los
metros representan una recta en EPSG:25830, nunca un itinerario.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd


SERVICE_COLUMNS = [
    "servicio_id", "nombre", "codigo_ine", "municipio", "categoria", "modalidad",
    "titularidad", "direccion", "url", "fuente", "estado_coordenadas",
    "x_25830", "y_25830", "longitud", "latitud", "lineas", "rutas_json",
    "url_fuente_geometria", "metodo_geometria", "reparacion_aplicada", "alertas_geometria_json",
]
KINDS = ("educacion", "sanidad", "hospitales", "transporte")
COVERAGE_NOTES = {
    "educacion": (
        "Centros educativos en ALTA de todas las titularidades y tipologías publicadas. "
        "La tipología no acredita una oferta o plaza disponible. Los centros locales sin "
        "coordenadas se conservan; no pueden ordenarse por proximidad."
    ),
    "sanidad": (
        "Centros de salud y consultorios del directorio oficial revisado en la fase 6. "
        "No incluye hospitales, urgencias ni el registro de autorizaciones. La ficha "
        "publicada permite consultar el servicio; no se infieren horarios, capacidad "
        "ni titularidad a partir de la categoría."
    ),
    "hospitales": (
        "Hospitales identificados en la capa oficial IDEM, una fuente parcial "
        "sin confirmación de actualidad. Puede incluir centros especializados; "
        "no acredita urgencias generales, capacidad, horarios, acceso o atención "
        "asignada. Cero registros locales no demuestra ausencia real de hospitales. "
        "Estos destinos se muestran como contexto y no intervienen en el IVP."
    ),
    "transporte": (
        "Fuente parcial: paradas operativas del GTFS de autobuses interurbanos CRTM. "
        "Las líneas son relaciones publicadas parada-ruta, sin afirmar servicio actual, "
        "frecuencia ni horario; no representa toda la red de transporte."
    ),
}
DISTANCE_NOTE = (
    "Las distancias se miden en línea recta desde el origen municipal de la fase 4 "
    "en EPSG:25830; no son tiempos ni distancias por carretera. Los destinos cercanos "
    "pertenecen a otros municipios, incluidos los de más de 50.000 habitantes."
)


def _read(path, columns):
    return pd.read_csv(path, usecols=columns, dtype=str, keep_default_na=False)


def _unique(frame, column, label):
    if frame[column].eq("").any() or frame[column].duplicated().any():
        raise ValueError(f"Identidades vacías o duplicadas en {label}.")


def _bool(series, label):
    if not series.isin(["True", "False"]).all():
        raise ValueError(f"Indicador operativo inválido en {label}.")
    return series.eq("True")


def _frame(index):
    result = pd.DataFrame("", index=index, columns=SERVICE_COLUMNS)
    for col in ["x_25830", "y_25830", "longitud", "latitud"]:
        result[col] = np.nan
    result["rutas_json"] = "[]"
    result["reparacion_aplicada"] = False
    result["alertas_geometria_json"] = "[]"
    return result


def _coordinates(frame, source, fields, operative):
    for target, original in zip(["x_25830", "y_25830", "longitud", "latitud"], fields):
        frame[target] = pd.to_numeric(source[original], errors="coerce")
    finite = np.isfinite(frame[["x_25830", "y_25830", "longitud", "latitud"]]).all(axis=1)
    if (operative & ~finite).any():
        raise ValueError("Servicio operativo sin coordenadas finitas verificadas.")
    frame.loc[~operative, ["x_25830", "y_25830", "longitud", "latitud"]] = np.nan
    frame["estado_coordenadas"] = np.where(operative, "verificadas", "sin_coordenadas_operativas")


def _municipalities(frame, names):
    if not frame["codigo_ine"].isin(names.index).all():
        raise ValueError("Servicio con código municipal desconocido.")
    frame["municipio"] = frame["codigo_ine"].map(names)
    _unique(frame, "servicio_id", "servicios")
    return frame.sort_values("servicio_id", kind="stable").reset_index(drop=True)


def load_service_tables(root):
    """Carga una vez tablas pequeñas ya elaboradas; no lee stop_times ni usa red.

    El consumidor puede guardar el resultado con ``st.cache_data``. Los cuatro
    catálogos de servicios comparten ``SERVICE_COLUMNS``; un nombre o enlace ausente permanece
    vacío. ``rutas_json`` conserva los IDs, nombres y URL realmente publicados.
    """
    root = Path(root)
    geo, integrated = root / "data/processed/geografia", root / "data/processed/integracion"
    master = _read(root / "data/processed/municipios/maestro_municipios.csv", ["codigo_ine", "municipio"])
    _unique(master, "codigo_ine", "maestro municipal")
    names = master.set_index("codigo_ine")["municipio"]
    origins = _read(geo / "origenes_municipales.csv", ["codigo_ine", "x_etrs89_utm30", "y_etrs89_utm30"])
    _unique(origins, "codigo_ine", "orígenes municipales")
    for col in ["x_etrs89_utm30", "y_etrs89_utm30"]:
        origins[col] = pd.to_numeric(origins[col], errors="raise")
    if not np.isfinite(origins[["x_etrs89_utm30", "y_etrs89_utm30"]]).all().all():
        raise ValueError("Origen municipal sin coordenadas finitas.")

    education = _read(geo / "centros_educativos_geografia.csv", [
        "registro_id", "nombre_centro", "codigo_ine", "tipo_centro_original",
        "titularidad_original", "direccion", "situacion", "fuente", "geografia_operativa",
        "x_25830", "y_25830", "longitud", "latitud",
    ])
    education = education.loc[education["situacion"].eq("ALTA")].copy()
    edu = _frame(education.index)
    for target, original in {
        "servicio_id": "registro_id", "nombre": "nombre_centro", "codigo_ine": "codigo_ine",
        "modalidad": "tipo_centro_original", "titularidad": "titularidad_original",
        "direccion": "direccion", "fuente": "fuente",
    }.items():
        edu[target] = education[original]
    edu["categoria"] = "educacion"
    _coordinates(edu, education, ["x_25830", "y_25830", "longitud", "latitud"],
                 _bool(education["geografia_operativa"], "educación"))

    health = _read(root / "data/processed/ivp/sanidad_catalogo_revisado.csv", [
        "registro_id", "nombre", "codigo_ine", "categoria", "fuente", "url_ficha",
        "apto_proximidad", "utm_x_25830", "utm_y_25830", "longitud_calculo", "latitud_calculo",
        "url_fuente_geometria", "metodo_geometria", "reparacion_aplicada", "alertas_geometria_json",
    ])
    health = health.loc[_bool(health["apto_proximidad"], "sanidad")].copy()
    if not health["categoria"].isin(["centro_salud", "consultorio"]).all():
        raise ValueError("El catálogo sanitario contiene una categoría no revisada para esta vista.")
    san = _frame(health.index)
    for target, original in {"servicio_id": "registro_id", "nombre": "nombre", "codigo_ine": "codigo_ine",
                             "categoria": "categoria", "fuente": "fuente", "url": "url_ficha",
                             "url_fuente_geometria": "url_fuente_geometria", "metodo_geometria": "metodo_geometria",
                             "alertas_geometria_json": "alertas_geometria_json"}.items():
        san[target] = health[original]
    san["reparacion_aplicada"] = _bool(health["reparacion_aplicada"], "reparaciones sanitarias")
    for alerts in san["alertas_geometria_json"]:
        try:
            value = json.loads(alerts)
        except (TypeError, ValueError) as exc:
            raise ValueError("Alertas de geometría sanitaria inválidas.") from exc
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError("Alertas de geometría sanitaria sin una lista de textos publicados.")
    san["modalidad"] = health["categoria"].map({"centro_salud": "Centro de salud", "consultorio": "Consultorio"})
    _coordinates(san, health, ["utm_x_25830", "utm_y_25830", "longitud_calculo", "latitud_calculo"],
                 pd.Series(True, index=health.index))

    stops = _read(geo / "paradas_geolocalizadas.csv", [
        "registro_id", "stop_id", "nombre", "direccion_fuente", "stop_url", "apta_proximidad", "location_type",
    ])
    _unique(stops, "registro_id", "paradas geolocalizadas")
    catalog = _read(geo / "catalogo_servicios_geograficos.csv", [
        "servicio_id", "categoria", "codigo_ine", "fuente", "x_25830", "y_25830", "longitud", "latitud",
        "nombre", "direccion", "url_ficha", "tipo_centro_original", "titularidad",
        "apto_proximidad", "url_fuente", "metodo_localizacion",
    ])
    hospitals = catalog.loc[catalog["categoria"].eq("hospital")].copy()
    _unique(hospitals, "servicio_id", "hospitales")
    if not hospitals["fuente"].eq("idem_hospitales").all():
        raise ValueError("Hospital sin procedencia de la capa oficial IDEM revisada.")
    hospital_coordinates = _bool(hospitals["apto_proximidad"], "hospitales")
    if not hospital_coordinates.all():
        raise ValueError("Hospital del catálogo sin geometría apta para proximidad.")
    hosp = _frame(hospitals.index)
    for target, original in {
        "servicio_id": "servicio_id", "nombre": "nombre", "codigo_ine": "codigo_ine",
        "categoria": "categoria", "fuente": "fuente", "direccion": "direccion",
        "url": "url_ficha", "modalidad": "tipo_centro_original", "titularidad": "titularidad",
        "url_fuente_geometria": "url_fuente", "metodo_geometria": "metodo_localizacion",
    }.items():
        hosp[target] = hospitals[original]
    _coordinates(hosp, hospitals, ["x_25830", "y_25830", "longitud", "latitud"], hospital_coordinates)
    catalog = catalog.loc[catalog["categoria"].eq("parada_bus"), [
        "servicio_id", "categoria", "codigo_ine", "fuente", "x_25830", "y_25830", "longitud", "latitud",
    ]].copy()
    _unique(catalog, "servicio_id", "paradas operativas")
    catalog = catalog.merge(stops, left_on="servicio_id", right_on="registro_id", how="left", validate="one_to_one")
    if catalog["registro_id"].isna().any() or not _bool(catalog["apta_proximidad"], "paradas").all():
        raise ValueError("Parada del catálogo sin registro operativo idéntico.")
    if not catalog["location_type"].isin(["", "0"]).all():
        raise ValueError("Una estación o instalación duplicada figura como parada operativa.")
    routes = _read(integrated / "transporte_rutas.csv", ["route_id", "route_short_name", "route_long_name", "route_url"])
    _unique(routes, "route_id", "rutas")
    relations = _read(integrated / "transporte_rutas_paradas.csv", ["route_id", "stop_id"])
    if relations.duplicated(["route_id", "stop_id"]).any() or not relations["route_id"].isin(routes["route_id"]).all():
        raise ValueError("Relaciones parada-ruta duplicadas o sin ruta publicada.")
    if not relations["stop_id"].isin(stops["stop_id"]).all():
        raise ValueError("Relación de ruta con parada desconocida.")
    joined = relations.merge(routes, on="route_id", validate="many_to_one").sort_values(["stop_id", "route_id"], kind="stable")
    grouped = joined.groupby("stop_id", sort=False)
    labels, route_json = {}, {}
    for stop_id, group in grouped:
        labels[stop_id] = ", ".join(sorted({name for name in group["route_short_name"] if name}))
        route_json[stop_id] = json.dumps(group.drop(columns="stop_id").to_dict("records"), ensure_ascii=False, allow_nan=False)
    bus = _frame(catalog.index)
    for target, original in {
        "servicio_id": "servicio_id", "nombre": "nombre", "codigo_ine": "codigo_ine",
        "categoria": "categoria", "fuente": "fuente", "direccion": "direccion_fuente", "url": "stop_url",
    }.items():
        bus[target] = catalog[original]
    bus["modalidad"] = "Autobús interurbano (fuente parcial)"
    bus["lineas"] = catalog["stop_id"].map(labels).fillna("")
    bus["rutas_json"] = catalog["stop_id"].map(route_json).fillna("[]")
    _coordinates(bus, catalog, ["x_25830", "y_25830", "longitud", "latitud"],
                 pd.Series(True, index=catalog.index))
    return {"educacion": _municipalities(edu, names), "sanidad": _municipalities(san, names),
            "hospitales": _municipalities(hosp, names),
            "transporte": _municipalities(bus, names), "origenes": origins,
            "rutas": routes, "relaciones": relations}


def service_listing(tables, codigo_ine, kind, *, nearby_limit=5):
    """Lista servicios locales y los N destinos de otros municipios más próximos.

    ``distancia_m`` es nula cuando no hay coordenadas operativas. Los empates se
    ordenan por identidad publicada; nunca se cambia un nombre ausente por el ID.
    Devuelve copias para que los filtros de interfaz no alteren la caché.
    """
    if kind not in KINDS:
        raise ValueError("Tipo de servicio desconocido: " + str(kind))
    if not isinstance(nearby_limit, int) or isinstance(nearby_limit, bool) or nearby_limit < 0:
        raise ValueError("nearby_limit debe ser un entero no negativo.")
    code = str(codigo_ine)
    origin = tables["origenes"].loc[tables["origenes"]["codigo_ine"].eq(code)]
    if len(origin) != 1:
        raise ValueError("El municipio no tiene un origen elegible único: " + code)
    xy = origin[["x_etrs89_utm30", "y_etrs89_utm30"]].to_numpy(dtype=float)[0]
    if not np.isfinite(xy).all():
        raise ValueError("El origen municipal no tiene coordenadas finitas.")
    data = tables[kind].copy()
    data["distancia_m"] = np.hypot(data["x_25830"] - xy[0], data["y_25830"] - xy[1])
    local = data.loc[data["codigo_ine"].eq(code)].sort_values(
        ["distancia_m", "servicio_id"], kind="stable", na_position="last")
    nearby = data.loc[~data["codigo_ine"].eq(code) & np.isfinite(data["distancia_m"])].sort_values(
        ["distancia_m", "servicio_id"], kind="stable").head(nearby_limit)
    return {"local": local.reset_index(drop=True), "nearby": nearby.reset_index(drop=True),
            "coverage_note": COVERAGE_NOTES[kind] + " " + DISTANCE_NOTE}
