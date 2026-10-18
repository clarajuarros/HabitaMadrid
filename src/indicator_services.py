"""Indicadores descriptivos de registros y proximidad, sin puntuaciones.

Los recuentos describen filas identificadas en las fuentes, no un censo de la
oferta real. Las distancias usan los puntos EPSG:25830 de la fase 4 y conservan
los destinos de municipios grandes. Ninguna distancia representa un trayecto.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.integration_common import IntegrationError, make_issue


DISTANCE_SPECS = (
    ("distancia_atencion_primaria_m", "sanidad", "Distancia al centro de salud o consultorio catalogado más próximo",
     ("centro_salud", "consultorio"), ("idem_centros_salud",), "candidato_ivp"),
    ("distancia_hospital_catalogado_m", "sanidad", "Distancia al hospital catalogado más próximo",
     ("hospital",), ("idem_hospitales",), "contexto"),
    ("distancia_centro_educativo_registrado_m", "educacion", "Distancia al centro educativo ALTA geovalidado más próximo",
     ("educacion",), ("centros_educativos",), "contexto"),
    ("distancia_centro_educativo_general_m", "educacion", "Distancia al centro educativo de tipología general seleccionada más próximo",
     ("educacion",), ("centros_educativos",), "candidato_ivp"),
    ("distancia_parada_bus_parcial_m", "transporte", "Distancia a la parada del GTFS parcial más próxima",
     ("parada_bus",), ("crtm_interurbanos_gtfs",), "candidato_ivp"),
)
GENERAL_EDUCATION_TYPES = (
    "COLEGIO DE EDUCACIÓN INFANTIL Y PRIMARIA",
    "COLEGIO DE EDUCACIÓN INFANTIL, PRIMARIA Y SECUNDARIA",
    "COLEGIO DE EDUCACIÓN PRIMARIA",
    "INSTITUTO DE EDUCACIÓN SECUNDARIA",
    "CENTRO PRIVADO DE EDUCACIÓN INFANTIL, PRIMARIA Y SECUNDARIA",
    "CENTRO PRIVADO DE EDUCACIÓN INFANTIL Y PRIMARIA",
    "CENTRO PRIVADO DE EDUCACIÓN PRIMARIA Y SECUNDARIA",
    "CENTRO PRIVADO DE EDUCACIÓN SECUNDARIA",
    "CENTRO PRIVADO DE EDUCACIÓN PRIMARIA",
    "CENTRO PRIVADO DE INFANTIL Y SECUNDARIA",
)
EDUCATION_OWNERS = (
    ("educacion_publicos_registrados", "PÚBLICO", "Centros educativos ALTA de titularidad pública registrados"),
    ("educacion_privados_registrados", "PRIVADO", "Centros educativos ALTA privados registrados"),
    ("educacion_concertados_registrados", "PRIVADO CONCERTADO", "Centros educativos ALTA privados concertados registrados"),
    ("educacion_publicos_titularidad_privada_registrados", "PÚBLICO-TITULARIDAD PRIVADA", "Centros educativos ALTA públicos de titularidad privada registrados"),
)
ORIGIN_SOURCES = ("idem_nucleos_eiel_2023", "idem_divisiones_administrativas")
DISTANCE_LIMIT = (
    "Línea recta desde un único punto representativo municipal EPSG:25830; no mide "
    "trayectos, tiempos, capacidad, horarios ni accesibilidad de toda la población. "
    "El origen puede proceder de un núcleo EIEL 2023 y las instalaciones no tienen "
    "una fecha de vigencia confirmada. Se conservan destinos de municipios no elegibles."
)
DISTANCE_EXTRA = {
    "sanidad": " La capa no confirma titularidad, disponibilidad ni derecho de acceso; el hospital puede ser especializado y no garantiza urgencias generales.",
    "educacion": " Agrupa todas las tipologías y titularidades del directorio. No identifica la etapa impartida, plazas ni admisión; un centro cercano puede no cubrir la necesidad educativa.",
    "transporte": " GTFS parcial de autobuses interurbanos; no representa Metro, Cercanías ni toda la red. No mide frecuencia, calendario, destinos ni servicio vigente.",
}
COUNT_LIMIT = (
    "Número de registros identificados de la fuente, sin verificar cobertura exhaustiva, "
    "capacidad ni funcionamiento actual. Cero significa cero registros en esta extracción "
    "y selección, nunca ausencia demostrada del servicio real."
)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _read(path, required, identifier=None):
    path = Path(path)
    if not path.is_file():
        raise IntegrationError(f"Falta la entrada de indicadores de servicios: {path}")
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = set(required) - set(frame.columns)
    if missing:
        raise IntegrationError(f"Faltan columnas en {path.name}: {', '.join(sorted(missing))}.")
    if identifier and (frame[identifier].eq("").any() or frame[identifier].duplicated().any()):
        raise IntegrationError(f"Identificadores vacíos o duplicados en {path.name}: {identifier}.")
    return frame


def _boolean(value):
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise IntegrationError("La elegibilidad debe ser booleana e informada.")


def _master(master):
    if not {"codigo_ine", "elegible_hasta_50000"}.issubset(master):
        raise IntegrationError("El maestro carece de código INE o elegibilidad.")
    result = master.copy()
    if (result.codigo_ine.isna().any() or result.codigo_ine.eq("").any()
            or result.codigo_ine.duplicated().any()):
        raise IntegrationError("El maestro contiene códigos vacíos o duplicados.")
    result["elegible_hasta_50000"] = result.elegible_hasta_50000.map(_boolean)
    return result


def _points(frame, x, y, label):
    points = frame[[x, y]].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    if not np.isfinite(points).all():
        raise IntegrationError(f"Coordenadas EPSG:25830 vacías, inválidas o no finitas en {label}.")
    return points


def _period(source_ids, metadata):
    dates = sorted({metadata.get(source, "")[:10] for source in source_ids} - {""})
    return "consulta_" + "+".join(dates) if dates else "consulta_sin_fecha"


def _definition(identifier, dimension, name, unit, direction, role, formula, sources, limitations, group):
    return dict(indicador_id=identifier, dimension=dimension, nombre=name, unidad=unit,
                sentido=direction, rol=role, formula=formula, fuentes_json=_json(list(sources)),
                limitaciones=limitations, grupo_redundancia=group,
                condicion_uso_ivp=(
                    "Candidatura provisional sujeta a la metodología de la fase 6. "
                    + {"educacion": "Solo proximidad a un directorio heterogéneo; no acredita adecuación a una etapa o perfil familiar.",
                       "transporte": "Solo proximidad a autobús interurbano de una fuente parcial; no acredita conectividad, frecuencia ni horarios.",
                       "sanidad": "Solo proximidad al conjunto CS/consultorio catalogado; no acredita cobertura, cartera, disponibilidad ni acceso público. Requiere revisar la cobertura global del catálogo para todos los municipios antes de cualquier puntuación."}[dimension]
                    if role == "candidato_ivp" else "Contexto; no seleccionado para el IVP en la fase 5."))


def prepare_service_indicators(master, integration_dir, geography_dir):
    """Devuelve observaciones y pruebas de cálculo, sin escribir ni imputar.

    Un origen perdido y un catálogo vacío conservan valores nulos con estados
    distintos. La combinación CS/consultorio se minimiza conjuntamente, no se
    promedian sus distancias ni se penalizan por separado.
    """
    integration_dir, geography_dir = Path(integration_dir), Path(geography_dir)
    master = _master(master)
    master_lookup = master.set_index("codigo_ine")
    known = set(master.codigo_ine)
    eligible = sorted(master.loc[master.elegible_hasta_50000, "codigo_ine"])
    origins = _read(geography_dir / "origenes_municipales.csv", {
        "origen_id", "codigo_ine", "metodo_origen", "fuente_origen", "x_etrs89_utm30", "y_etrs89_utm30",
    }, "origen_id")
    if origins.codigo_ine.duplicated().any():
        raise IntegrationError("Los indicadores requieren un único origen por municipio.")
    services = _read(geography_dir / "catalogo_servicios_geograficos.csv", {
        "servicio_id", "codigo_ine", "categoria", "nombre", "direccion", "fuente", "x_25830", "y_25830",
    }, "servicio_id")
    education = _read(integration_dir / "centros_educativos_alta.csv", {
        "registro_id", "codigo_ine", "situacion", "titularidad_original", "tipo_centro_original", "fuente",
    }, "registro_id")
    if not education.situacion.eq("ALTA").all():
        raise IntegrationError("El recuento educativo requiere exclusivamente registros ALTA.")
    allowed_categories = {"centro_salud", "consultorio", "hospital", "educacion", "parada_bus", "sanidad_otros"}
    if not services.categoria.isin(allowed_categories).all():
        raise IntegrationError("Categoría desconocida en el catálogo geográfico.")
    for frame, name in [(origins, "orígenes"), (services, "servicios geográficos")]:
        if not frame.codigo_ine.isin(known).all():
            raise IntegrationError(f"Hay {name} sin municipio confirmado en el maestro.")
    unknown_education = education.loc[~education.codigo_ine.isin(known)]
    issues = [make_issue("centros_educativos", "registro_sin_municipio_para_indicador", row.registro_id,
                         "codigo_ine", row.codigo_ine, "No se atribuye a ningún recuento municipal.")
              for row in unknown_education.itertuples()]
    # Una fuente debe conservar su identidad; mezclarla invalidaría la trazabilidad.
    if not education.fuente.eq("centros_educativos").all():
        raise IntegrationError("La tabla educativa mezcla fuentes distintas del directorio.")
    expected_sources = {category: set(sources) for _, _, _, categories, sources, _ in DISTANCE_SPECS
                        for category in categories}
    for row in services.itertuples():
        if row.categoria in expected_sources and row.fuente not in expected_sources[row.categoria]:
            raise IntegrationError(f"Fuente inesperada para {row.categoria}: {row.fuente}.")
    origin_xy = _points(origins, "x_etrs89_utm30", "y_etrs89_utm30", "orígenes")
    _points(services, "x_25830", "y_25830", "servicios")
    origins = origins.assign(_x=origin_xy[:, 0], _y=origin_xy[:, 1]).set_index("codigo_ine")
    services = services.sort_values("servicio_id", kind="stable").reset_index(drop=True)
    metadata = {}
    for folder, filename in [(integration_dir, "fuentes.csv"), (geography_dir, "fuentes_geograficas.csv")]:
        source_table = _read(folder / filename, {"fuente", "fecha_descarga_utc"})
        for source, date in source_table[["fuente", "fecha_descarga_utc"]].itertuples(index=False, name=None):
            if date:
                metadata[source] = date
    observations, definitions, distances, counts = [], [], [], []
    general_education_ids = set(education.loc[
        education.tipo_centro_original.isin(GENERAL_EDUCATION_TYPES), "registro_id"])

    def observe(code, identifier, value, state, sources, details, note):
        observations.append(dict(codigo_ine=code, indicador_id=identifier, valor=value,
                                 anio=None, periodo=_period(sources, metadata), estado=state,
                                 fuentes_json=_json(list(sources)), detalle_json=_json(details), nota=note))

    for identifier, dimension, name, categories, source_ids, role in DISTANCE_SPECS:
        sources = (*source_ids, *ORIGIN_SOURCES)
        general_selection = identifier == "distancia_centro_educativo_general_m"
        extra = (" Agrupa las tipologías seleccionadas y todas sus titularidades. "
                 "No identifica la oferta educativa real, plazas ni admisión; un centro cercano "
                 "puede no cubrir la necesidad educativa."
                 if general_selection else DISTANCE_EXTRA[dimension])
        limitations = DISTANCE_LIMIT + extra
        if general_selection:
            limitations += (" Selección por diez tipologías literales publicadas, no por oferta educativa "
                            "verificada; excluye otras tipologías, incluidos centros administrativos, "
                            "orientación, escuelas infantiles, educación especial y artística. "
                            "La selección no constituye un censo completo de centros que impartan una etapa.")
        definitions.append(_definition(identifier, dimension, name, "m", "menor_mejor", role,
            "min(sqrt((x_servicio-x_origen)^2+(y_servicio-y_origen)^2)), coordenadas EPSG:25830; "
            + "categorías: " + ", ".join(categories) + "; empate exacto por servicio_id ascendente."
            + (" Tipologías exactas seleccionadas: " + _json(list(GENERAL_EDUCATION_TYPES)) if general_selection else ""),
            sources, limitations, "proximidad_" + dimension))
        candidates = services.loc[services.categoria.isin(categories)].reset_index(drop=True)
        if general_selection:
            candidates = candidates.loc[candidates.servicio_id.isin(general_education_ids)].reset_index(drop=True)
        xy = _points(candidates, "x_25830", "y_25830", "candidatos")
        for code in eligible:
            details = dict(categorias=list(categories), candidatos_totales=len(candidates),
                           crs_calculo="EPSG:25830", tipo_distancia="euclidea_desde_origen_representativo",
                           cobertura_exhaustiva_verificada=False, vigencia_servicio_confirmada=False)
            if general_selection:
                details["tipologias_exactas_seleccionadas"] = list(GENERAL_EDUCATION_TYPES)
            trace = dict(codigo_ine=code, indicador_id=identifier, origen_id="", servicio_id="",
                         codigo_ine_destino="", categoria_servicio="", distancia_m=None,
                         metodo_origen="", fuente_origen="", fuente_servicio="")
            if code not in origins.index:
                state, value = "sin_origen", None
            elif not len(candidates):
                state, value = "sin_candidatos", None
                origin = origins.loc[code]
                details.update(origen_id=origin.origen_id, metodo_origen=origin.metodo_origen,
                               fuente_origen=origin.fuente_origen)
                trace.update(origen_id=origin.origen_id, metodo_origen=origin.metodo_origen,
                             fuente_origen=origin.fuente_origen)
            else:
                origin = origins.loc[code]
                delta = np.hypot(xy[:, 0] - origin._x, xy[:, 1] - origin._y)
                position = np.lexsort((candidates.servicio_id.to_numpy(dtype=str), delta))[0]
                target = candidates.iloc[position]
                value, state = float(delta[position]), "disponible"
                details.update(origen_id=origin.origen_id, codigo_ine_origen=code,
                    metodo_origen=origin.metodo_origen, fuente_origen=origin.fuente_origen,
                    origen_x_25830=float(origin._x), origen_y_25830=float(origin._y),
                    servicio_id=target.servicio_id, categoria_servicio=target.categoria,
                    codigo_ine_destino=target.codigo_ine, mismo_municipio=code == target.codigo_ine,
                    nombre_servicio=target.nombre, direccion_servicio=target.direccion,
                    fuente_servicio=target.fuente, servicio_x_25830=float(xy[position, 0]),
                    servicio_y_25830=float(xy[position, 1]),
                    destino_elegible_hasta_50000=bool(master_lookup.loc[target.codigo_ine, "elegible_hasta_50000"]))
                trace.update({key: details[key] for key in ["origen_id", "servicio_id", "codigo_ine_destino",
                    "categoria_servicio", "metodo_origen", "fuente_origen", "fuente_servicio"]})
                trace["distancia_m"] = value
            trace["estado"] = state
            distances.append(trace)
            observe(code, identifier, value, state, sources, details, limitations)

    count_specs = [
        ("educacion_alta_registrados", "educacion", "Centros educativos ALTA registrados", education,
         "registro_id", ("centros_educativos",), "Todos los tipos y titularidades; no equivale a centros de una etapa concreta.", "educacion_registros"),
    ]
    owner_labels = {label for _, label, _ in EDUCATION_OWNERS}
    for identifier, label, title in EDUCATION_OWNERS:
        count_specs.append((identifier, "educacion", title,
            education.loc[education.titularidad_original.eq(label)], "registro_id", ("centros_educativos",),
            "Titularidad literal del directorio. No acredita etapas, plazas ni oferta educativa actual.", "educacion_registros"))
    count_specs.extend([
        ("educacion_otra_titularidad_registrados", "educacion", "Centros educativos ALTA con otra titularidad o sin informar",
         education.loc[~education.titularidad_original.isin(owner_labels)], "registro_id", ("centros_educativos",),
         "Conserva etiquetas no clasificadas y vacías, sin recodificarlas como públicas o privadas.", "educacion_registros"),
        ("atencion_primaria_catalogada_local", "sanidad", "Centros de salud y consultorios geovalidados catalogados",
         services.loc[services.categoria.isin(["centro_salud", "consultorio"])], "servicio_id", ("idem_centros_salud",),
         "Solo puntos de la capa IDEM aptos para proximidad. No se utiliza el registro general de autorizaciones ni se garantiza atención pública.", "sanidad_registros"),
        ("hospitales_catalogados_locales", "sanidad", "Hospitales geovalidados catalogados",
         services.loc[services.categoria.eq("hospital")], "servicio_id", ("idem_hospitales",),
         "Incluye hospitales especializados; no acredita urgencias generales, capacidad ni titularidad.", "sanidad_registros"),
        ("paradas_bus_gtfs_parcial_locales", "transporte", "Paradas de autobús del GTFS parcial asignadas inequívocamente",
         services.loc[services.categoria.eq("parada_bus")], "servicio_id", ("crtm_interurbanos_gtfs",),
         "Solo location_type=0 geovalidadas. Excluye estaciones padre, puntos sin municipio y zonas compartidas; no equivale a toda la red ni a paradas con servicio vigente.", "transporte_registros"),
    ])
    for identifier, dimension, title, records, record_id, sources, extra, group in count_specs:
        limitations = COUNT_LIMIT + " " + extra
        definitions.append(_definition(identifier, dimension, title, "registros", "contexto", "contexto",
            "Recuento de identificadores únicos vinculados al código INE en la selección descrita; conjunto vacío=0 registros.",
            sources, limitations, group))
        grouped = records.groupby("codigo_ine")[record_id].agg(list).to_dict()
        for code in eligible:
            ids = sorted(grouped.get(code, []))
            details = dict(registros_ids=ids, total_registros=len(ids), alcance="registros_de_la_extraccion",
                           cero_significa="cero_registros_en_la_seleccion", seleccion=extra,
                           cobertura_exhaustiva_verificada=False,
                           registros_fuente_sin_municipio=len(unknown_education) if dimension == "educacion" else 0)
            observe(code, identifier, int(len(ids)), "disponible", sources, details, limitations)
            counts.append(dict(codigo_ine=code, indicador_id=identifier, registros=len(ids),
                               registros_ids_json=_json(ids), alcance="registros_de_la_extraccion"))
    return dict(observations=observations, definitions=definitions,
                tables={"detalle_distancias_indicadores.csv": pd.DataFrame(distances),
                        "recuentos_servicios_indicadores.csv": pd.DataFrame(counts)}, issues=issues,
                summary=dict(indicadores=len(definitions), observaciones=len(observations),
                             municipios_elegibles=len(eligible), servicios_geograficos=len(services),
                             centros_educativos_alta=len(education), centros_educativos_sin_municipio=len(unknown_education),
                             tipologias_educacion_general=list(GENERAL_EDUCATION_TYPES),
                             centros_educacion_general_alta=len(general_education_ids),
                             centros_educacion_general_geovalidados=int(services.servicio_id.isin(general_education_ids).sum()),
                             crs_distancias="EPSG:25830", tiempos_viaje_calculados=False,
                             anio_instalaciones="No confirmado; periodo identifica consulta de las fuentes, no vigencia."))
