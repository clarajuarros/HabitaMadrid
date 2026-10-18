"""Sanidad geográfica oficial de IDEM, independiente del registro sanitario.

No se enlazan identificadores locales de ArcGIS con números de autorización.
El catálogo contiene instalaciones de ámbito regional; no demuestra por sí
solo exhaustividad, titularidad, capacidad o apertura en la fecha de consulta.
"""
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import shape

from src.geo_common import assign_points
from src.integration_common import IntegrationError, make_issue
from src.municipalities import normalized_name


SOURCES = {
    "centros_salud": {"path": "Hosted/Centros_de_Salud/FeatureServer/0", "prefix": "idem_salud", "source_id": "idem_centros_salud"},
    "hospitales": {"path": "Hosted/Hospitales/FeatureServer/3", "prefix": "idem_hospital", "source_id": "idem_hospitales"},
}


def _text(value):
    return "" if value is None or pd.isna(value) else str(value).strip()


def _json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise IntegrationError(f"No se pudo leer la evidencia sanitaria: {path.name}") from exc


def _verified_layer(source_dir, key, manifest):
    """Verifica integridad, consulta CRS explícita y ausencia de truncamiento."""
    records = {row["archivo"]: row for row in manifest.get("registros", [])}
    documents = {}
    for suffix in [".geojson", "_metadatos.json", "_conteo.json"]:
        filename = key + suffix
        if filename not in records:
            raise IntegrationError(f"Falta procedencia verificable para {filename}.")
        try:
            content = (source_dir / filename).read_bytes()
        except OSError as exc:
            raise IntegrationError(f"Falta el archivo sanitario {filename}.") from exc
        record = records[filename]
        if (record.get("sha256") != hashlib.sha256(content).hexdigest()
                or record.get("bytes") != len(content)):
            raise IntegrationError(f"Ha cambiado la evidencia sanitaria {filename}.")
        documents[suffix] = _json(source_dir / filename)
    data, metadata, count = (documents[suffix] for suffix in [".geojson", "_metadatos.json", "_conteo.json"])
    if metadata.get("geometryType") != "esriGeometryPoint":
        raise IntegrationError(f"{key} no es una capa puntual de instalaciones; no se utilizan centroides.")
    reference = metadata.get("extent", {}).get("spatialReference", {})
    if reference.get("latestWkid", reference.get("wkid")) != 4326:
        raise IntegrationError(f"{key}: CRS de origen no confirmado como EPSG:4326.")
    query = parse_qs(urlparse(records[key + ".geojson"].get("url", "")).query)
    if query.get("outSR") != ["4326"]:
        raise IntegrationError(f"{key}: no consta CRS explícito de la consulta de descarga.")
    if (data.get("type") != "FeatureCollection" or not isinstance(data.get("features"), list)
            or data.get("exceededTransferLimit") or len(data["features"]) != count.get("count")):
        raise IntegrationError(f"{key}: descarga incompleta o colección no válida.")
    return data, records[key + ".geojson"], metadata


def prepare_official_health(master, municipalities, shared_areas, source_dir):
    """Normaliza las dos capas descargadas y devuelve puntos aptos y auditoría.

    Se utiliza una única capa regional de centros de salud para evitar duplicar
    la réplica alojada dentro del servicio Hospitales. Los centros marcados
    CERRADO se conservan en la tabla y quedan fuera de proximidad. Un punto
    fuera del territorio, compartido, limítrofe o contradictorio con el municipio
    declarado no produce un candidato. No se ajustan coordenadas ni se geocodifica.
    """
    source_dir = Path(source_dir)
    if not {"codigo_ine", "municipio"}.issubset(master):
        raise IntegrationError("El maestro sanitario requiere código INE y municipio.")
    if master["codigo_ine"].duplicated().any():
        raise IntegrationError("El maestro sanitario contiene municipios duplicados.")
    master = master.copy()
    master["codigo_ine"] = master["codigo_ine"].map(_text)
    known_codes = set(master["codigo_ine"])
    names = {}
    for _, municipal in master.iterrows():
        for field in ["municipio", "municipio_padron"]:
            name = _text(municipal.get(field, ""))
            if not name:
                continue
            normalized = normalized_name(name)
            if normalized in names and names[normalized] != municipal["codigo_ine"]:
                raise IntegrationError("El maestro contiene nombres municipales ambiguos.")
            names[normalized] = municipal["codigo_ine"]
    manifest = _json(source_dir / "descargas.json")
    rows, issues, source_summary = [], [], {}
    for key, configuration in SOURCES.items():
        data, provenance, metadata = _verified_layer(source_dir, key, manifest)
        identifiers = set()
        source_summary[key] = {
            "registros": len(data["features"]), "url": provenance["url"],
            "sha256": provenance["sha256"], "descargado_utc": provenance.get("descargado_utc", ""),
            "crs": "EPSG:4326", "capa": configuration["path"],
            "nombre_capa": metadata.get("name", ""),
        }
        for feature in data["features"]:
            props = feature.get("properties", {})
            if not isinstance(props, dict):
                raise IntegrationError(f"{key}: registro sin propiedades válidas.")
            identifier = _text(props.get("fid"))
            if not identifier or identifier in identifiers:
                raise IntegrationError(f"{key}: identificador fid vacío o duplicado.")
            identifiers.add(identifier)
            service_id = configuration["prefix"] + ":" + identifier
            original_type = _text(props.get("tipocentro")) if key == "centros_salud" else "Hospital"
            category = {"CS": "centro_salud", "Consultorio": "consultorio"}.get(original_type, "sanidad_otros")
            if key == "hospitales":
                category = "hospital"
            closed = original_type.upper() == "CERRADO"
            name = _text(props.get("descr"))
            address = (_text(props.get("direccion")) if key == "hospitales" else
                       " ".join(value for field in ["tipovial", "nomvia", "numero"]
                                if (value := _text(props.get(field)))))
            original_municipality = _text(props.get("municipio"))
            declared = names.get(normalized_name(original_municipality), "")
            original_code = _text(props.get("cmun"))
            if original_code:
                official_code = "28" + original_code.zfill(3) if original_code.isdigit() and len(original_code) <= 3 else ""
                if official_code not in known_codes:
                    issues.append(make_issue(key, "codigo_municipal_desconocido", service_id,
                                             "cmun", original_code, "No se utiliza como correspondencia municipal."))
                elif declared and declared != official_code:
                    raise IntegrationError(f"{service_id}: código y nombre municipal de origen se contradicen.")
                else:
                    declared = official_code
            geometry = None
            try:
                geometry = shape(feature.get("geometry"))
                if (geometry.geom_type != "Point" or geometry.is_empty or not geometry.is_valid
                        or not np.isfinite([geometry.x, geometry.y]).all()
                        or not (-180 <= geometry.x <= 180 and -90 <= geometry.y <= 90)):
                    geometry = None
            except (TypeError, ValueError, KeyError, AttributeError):
                geometry = None
            if geometry is None:
                issues.append(make_issue(key, "coordenadas_no_utilizables", service_id,
                                         "geometry", "", "La instalación se conserva sin calcular proximidad."))
            if closed:
                issues.append(make_issue(key, "instalacion_marcada_cerrada", service_id,
                                         "tipocentro", original_type, "Se conserva y se excluye de proximidad."))
            rows.append({
                "registro_id": service_id, "servicio_id": service_id, "categoria": category,
                "fuente_registro_id": identifier, "codigo_hospital_original": _text(props.get("codigo")),
                "tipo_centro_original": original_type, "nombre": name,
                "estado_nombre": "publicado" if name else "no_publicado_en_fuente", "direccion": address,
                "municipio_original": original_municipality, "codigo_municipio_original": original_code,
                "codigo_ine_declarado": declared, "fuente": configuration["source_id"],
                "capa_fuente": configuration["path"],
                "url_fuente": provenance["url"], "url_ficha": _text(props.get("url")),
                "fecha_consulta_utc": provenance.get("descargado_utc", ""),
                "fecha_actualizacion_instalacion": "", "crs_original": "EPSG:4326",
                "longitud_original": geometry.x if geometry is not None else None,
                "latitud_original": geometry.y if geometry is not None else None,
                "metodo_localizacion": "geometria_puntual_oficial_epsg4326",
                "estado_operativo": "cerrado_segun_fuente" if closed else "sin_confirmacion_de_actualidad",
                "titularidad": "", "estado_titularidad": "no_publicada_en_capa",
                "atributos_originales_json": json.dumps(props, ensure_ascii=False, sort_keys=True),
                "geometry": geometry,
            })
    if not rows:
        raise IntegrationError("No hay instalaciones sanitarias en las capas oficiales.")
    points = gpd.GeoDataFrame(rows, geometry="geometry", crs=4326).to_crs(25830)
    points["estado_transformacion"] = np.where(points.geometry.notna(), "transformacion_completada", "sin_coordenadas")
    failed_transform = points.geometry.notna() & (
        ~np.isfinite(points.geometry.x) | ~np.isfinite(points.geometry.y)
        | points.geometry.is_empty | ~points.geometry.is_valid)
    for identifier in points.loc[failed_transform, "servicio_id"]:
        issues.append(make_issue("sanidad_oficial_geografica", "transformacion_fallida", identifier,
                                 "geometry", "", "La proyección produjo coordenadas no utilizables; "
                                 "se conservan los valores originales y se excluye de proximidad."))
    points.loc[failed_transform, "geometry"] = None
    points.loc[failed_transform, "estado_transformacion"] = "transformacion_fallida"
    assignment = assign_points(points, municipalities, shared_areas)
    points = points.merge(assignment, on="registro_id", validate="one_to_one")
    unique = points["estado_asignacion_espacial"].eq("municipio_unico")
    conflict = unique & points["codigo_ine_declarado"].ne("") & points["codigo_ine_declarado"].ne(points["codigo_ine_espacial"])
    points["codigo_ine"] = points["codigo_ine_espacial"].where(unique & ~conflict, "")
    points["municipio"] = points["codigo_ine"].map(master.set_index("codigo_ine")["municipio"]).fillna("")
    points["apto_proximidad"] = unique & ~conflict & points["estado_operativo"].ne("cerrado_segun_fuente")
    points["estado_validacion_municipal"] = np.where(conflict, "conflicto_municipio_declarado_espacial",
                                                     points["estado_asignacion_espacial"])
    points.loc[failed_transform, "estado_validacion_municipal"] = "transformacion_fallida"
    points["utm_x_25830"], points["utm_y_25830"] = points.geometry.x, points.geometry.y
    for _, point in points.loc[(~unique | conflict) & ~failed_transform].iterrows():
        issues.append(make_issue("sanidad_oficial_geografica", point["estado_validacion_municipal"],
                                 point["servicio_id"], "codigo_ine", point["codigo_ine_declarado"],
                                 "Se conserva, pero no participa como servicio próximo."))
    table = pd.DataFrame(points.drop(columns="geometry"))
    services = points.loc[points["apto_proximidad"]].copy().reset_index(drop=True)
    return {
        "tables": {"sanidad_oficial_geografica.csv": table}, "services": services,
        "issues": issues,
        "summary": {
            "fuentes": source_summary, "registros": len(points), "aptos_proximidad": len(services),
            "categorias_aptas": {str(category): int(count) for category, count in services["categoria"].value_counts().items()},
            "municipios_con_servicio_apto": int(services["codigo_ine"].nunique()),
            "cerrados_excluidos": int(points["estado_operativo"].eq("cerrado_segun_fuente").sum()),
            "conflictos_municipales": int(conflict.sum()), "crs_calculo": "EPSG:25830",
            "cobertura": "Capas oficiales de ámbito regional y cobertura parcial; incluyen destinos de municipios grandes. "
                "No equivalen al inventario exhaustivo de toda la sanidad pública y privada.",
            "identificadores": "Propios de cada capa IDEM; sin enlace inferido con el registro de autorizaciones sanitarias.",
            "duplicidad": "Se usa Centros_de_Salud/0 y Hospitales/3; se omite la réplica Centros de Salud de Hospitales/2.",
            "restricciones": "No consta fecha de actualización por instalación, titularidad, apertura verificada ni capacidad. "
                "No se reclasifican consultas privadas como atención primaria.",
        },
    }
