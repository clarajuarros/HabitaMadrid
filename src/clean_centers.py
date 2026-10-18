"""Limpieza trazable de directorios de educación y del registro sanitario.

No interpreta coordenadas, transforma CRS ni equipara ofertas sanitarias a
servicios disponibles hoy. Los centros de municipios grandes se conservan.
"""
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re

import pandas as pd

from src.integration_common import (
    IntegrationError, attach_municipal_context, make_issue, read_csv_strings,
)
from src.municipalities import normalized_name


EDUCATION_FILE = "centros_educativos.csv"
HEALTH_FILE = "centros_servicios_establecimientos_sanitarios.csv"
EDUCATION_FIELDS = {
    "CODIGO": "codigo_centro_original", "CENTRO": "nombre_centro",
    "COD_TIPO": "codigo_tipo_original", "TIPO_ABRV": "tipo_abreviado_original",
    "TIPO_EXT": "tipo_centro_original", "TITULARIDAD": "titularidad_original",
    "CDTPVIA": "direccion_tipo_via_original", "DOMICILIO": "direccion_via_original",
    "NMVIAL": "direccion_numero_original", "CDPOSTAL": "codigo_postal_original",
    "CDMUNI": "codigo_municipio_original", "MUNICIPIO": "municipio_original",
    "UTM_X": "coordenada_x_original", "UTM_Y": "coordenada_y_original",
    "SITUACIÓN": "situacion_original", "FECHA CONSTITUCIÓN": "fecha_constitucion_original",
}
HEALTH_FIELDS = {
    "centro_tipo": "tipo_centro_original",
    "dependecia_funcional": "dependencia_funcional_original",
    "dependecia_patrimonial": "dependencia_patrimonial_original",
    "municipio_nombre": "municipio_original",
    "direccion_vial_tipo": "direccion_tipo_via_original",
    "direccion_vial_nombre": "direccion_via_original",
    "direccion_vial_nro": "direccion_numero_original",
    "direccion_informacon_adicional": "direccion_adicional_original",
    "direccion_codigo_postal": "codigo_postal_original",
    "localizacion_coordenada_x": "coordenada_x_original",
    "localizacion_coordenada_y": "coordenada_y_original",
}


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _require(frame, columns, source):
    missing = set(columns) - set(frame.columns)
    if missing:
        raise IntegrationError(f"{source}: faltan columnas: {', '.join(sorted(missing))}")


def _coordinate_state(x, y):
    """Solo calidad sintáctica: ningún rango geográfico ni EPSG inferido."""
    x, y = str(x).strip(), str(y).strip()
    if not x and not y:
        return "ausentes"
    if not x or not y:
        return "incompletas"
    try:
        numbers = tuple(Decimal(value.replace(",", ".")) for value in (x, y))
    except InvalidOperation:
        return "no_numericas"
    if not all(value.is_finite() for value in numbers):
        return "no_numericas"
    if numbers in {(Decimal(0), Decimal(0)), (Decimal(-109), Decimal(-208))}:
        return "centinela"
    return "presentes_crs_pendiente"


def _add_coordinates(frame, source, issues):
    states = []
    for _, row in frame.iterrows():
        conflicts = json.loads(row.get("atributos_conflictivos_json", "{}"))
        if {"coordenada_x_original", "coordenada_y_original"} & set(conflicts):
            state = "conflictivas"
        else:
            state = _coordinate_state(row["coordenada_x_original"], row["coordenada_y_original"])
        states.append(state)
        if state not in {"presentes_crs_pendiente", "conflictivas"}:
            issues.append(make_issue(
                source, "coordenadas_" + state, registro=row["registro_id"],
                campo="coordenadas_originales",
                valor_original=_json([row["coordenada_x_original"], row["coordenada_y_original"]]),
                detalle="Se conserva el valor original; no se geolocaliza ni imputa.",
                fila_fuente=str(row.get("fila_fuente", row.get("filas_fuente", ""))),
            ))
    frame["estado_coordenadas"] = states
    frame["crs_coordenadas"] = ""
    frame["estado_crs"] = "pendiente_verificacion"
    return frame


def _add_address(frame):
    """Compone solo componentes publicados, conservándolos por separado."""
    addresses, states = [], []
    fields = ["direccion_tipo_via_original", "direccion_via_original", "direccion_numero_original"]
    for _, row in frame.iterrows():
        conflicts = json.loads(row.get("atributos_conflictivos_json", "{}"))
        if set(fields + ["direccion_adicional_original"]) & set(conflicts):
            addresses.append("")
            states.append("conflictiva")
            continue
        parts = [str(row.get(field, "")).strip() for field in fields]
        address = " ".join(part for part in parts if part)
        additional = str(row.get("direccion_adicional_original", "")).strip()
        if additional:
            address = ", ".join(part for part in (address, additional) if part)
        addresses.append(address)
        states.append("publicada" if address else "no_publicada")
    frame["direccion"] = addresses
    frame["estado_direccion"] = states
    return frame


def _education(raw, master, issues):
    _require(raw, EDUCATION_FIELDS, EDUCATION_FILE)
    frame = raw[list(EDUCATION_FIELDS) + ["fila_fuente"]].rename(columns=EDUCATION_FIELDS).copy()
    frame["fuente"] = EDUCATION_FILE
    frame["registro_id"] = [
        "educacion:" + code.strip() if code.strip() else "educacion:fila:" + str(line)
        for code, line in zip(frame["codigo_centro_original"], frame["fila_fuente"])
    ]
    known_codes = set(master["codigo_ine"])
    codes = []
    for _, row in frame.iterrows():
        raw_code = row["codigo_municipio_original"].strip()
        code = "28" + raw_code if re.fullmatch(r"[0-9]{3}", raw_code) else ""
        if code not in known_codes:
            issues.append(make_issue(
                EDUCATION_FILE, "municipio_no_mapeado", registro=row["registro_id"],
                campo="CDMUNI", valor_original=row["codigo_municipio_original"],
                detalle="Código municipal ausente, inválido o ajeno al maestro.",
                fila_fuente=str(row["fila_fuente"]),
            ))
            code = ""
        codes.append(code)
        if not row["codigo_centro_original"].strip():
            issues.append(make_issue(EDUCATION_FILE, "identificador_ausente", registro=row["registro_id"],
                                     campo="CODIGO", fila_fuente=str(row["fila_fuente"])))
    frame["codigo_ine"] = codes
    frame = attach_municipal_context(frame, master)
    for _, row in frame.iterrows():
        if row["municipio"] and normalized_name(row["municipio_original"]) != normalized_name(row["municipio"]):
            issues.append(make_issue(
                EDUCATION_FILE, "nombre_municipal_discrepante", registro=row["registro_id"],
                campo="MUNICIPIO", valor_original=row["municipio_original"],
                detalle="Se une por código; nombre oficial del maestro: " + row["municipio"],
                fila_fuente=str(row["fila_fuente"]),
            ))
    duplicates = frame[frame["codigo_centro_original"].str.strip().ne("") &
                       frame.duplicated("registro_id", keep=False)]
    for identifier, group in duplicates.groupby("registro_id", sort=True):
        fields = [field for field in EDUCATION_FIELDS.values() if group[field].nunique(dropna=False) > 1]
        issues.append(make_issue(
            EDUCATION_FILE, "identificador_duplicado", registro=identifier,
            campo=",".join(fields), valor_original=_json(sorted(set(group["codigo_centro_original"]))),
            detalle="Se conservan todas las filas y estados; no se selecciona una versión del centro.",
            fila_fuente=_json(group["fila_fuente"].tolist()),
        ))
    frame["situacion"] = frame["situacion_original"].str.strip().str.upper()
    frame["estado_nombre"] = frame["nombre_centro"].str.strip().map(lambda value: "publicado" if value else "no_publicado")
    frame = _add_coordinates(_add_address(frame), EDUCATION_FILE, issues)
    return frame.reset_index(drop=True)


def _health(raw, master, issues):
    _require(raw, set(HEALTH_FIELDS) | {"centro_nro_registro", "oferta_asistecial"}, HEALTH_FILE)
    raw = raw.copy()
    raw["registro_id"] = [
        "sanidad:" + code.strip() if code.strip() else "sanidad:fila:" + str(line)
        for code, line in zip(raw["centro_nro_registro"], raw["fila_fuente"])
    ]
    lookup = {}
    for _, row in master.iterrows():
        for field in ("municipio", "municipio_padron"):
            if field in row and str(row[field]).strip():
                lookup.setdefault(normalized_name(row[field]), set()).add(row["codigo_ine"])
    rows, offers = [], []
    for identifier, group in raw.groupby("registro_id", sort=True):
        source_lines = group["fila_fuente"].tolist()
        numbers = sorted(set(group["centro_nro_registro"]))
        conflicts, values = {}, {}
        for original, field in HEALTH_FIELDS.items():
            originals = sorted(set(group[original]))
            values[field] = originals[0] if len(originals) == 1 else ""
            if len(originals) > 1:
                conflicts[field] = originals
                issues.append(make_issue(
                    HEALTH_FILE, "atributo_contradictorio", registro=identifier,
                    campo=original, valor_original=_json(originals),
                    detalle="Escalar vacío; todas las alternativas se conservan en atributos_conflictivos_json.",
                    fila_fuente=_json(source_lines),
                ))
        number = numbers[0] if len(numbers) == 1 else ""
        if len(numbers) > 1:
            conflicts["numero_registro_original"] = numbers
            issues.append(make_issue(
                HEALTH_FILE, "identificador_original_discrepante", registro=identifier,
                campo="centro_nro_registro", valor_original=_json(numbers),
                detalle="Las formas del identificador solo coinciden tras eliminar espacios exteriores.",
                fila_fuente=_json(source_lines),
            ))
        elif not number.strip():
            issues.append(make_issue(HEALTH_FILE, "identificador_ausente", registro=identifier,
                                     campo="centro_nro_registro", fila_fuente=_json(source_lines)))
        candidates = lookup.get(normalized_name(values["municipio_original"]), set())
        code = next(iter(candidates)) if len(candidates) == 1 else ""
        if not code:
            issues.append(make_issue(
                HEALTH_FILE, "municipio_no_mapeado", registro=identifier,
                campo="municipio_nombre", valor_original=values["municipio_original"],
                detalle="Nombre ausente, contradictorio, no coincidente o ambiguo; sin búsqueda aproximada.",
                fila_fuente=_json(source_lines),
            ))
        if group.duplicated(list(HEALTH_FIELDS) + ["centro_nro_registro", "oferta_asistecial"]).any():
            issues.append(make_issue(
                HEALTH_FILE, "fila_duplicada", registro=identifier,
                detalle="Duplicado en atributos publicados; se consolida con todas sus filas de procedencia.",
                fila_fuente=_json(source_lines),
            ))
        offer_values = sorted(set(group["oferta_asistecial"]))
        for offer, offer_group in group.groupby("oferta_asistecial", sort=True, dropna=False):
            offers.append({
                "registro_id": identifier, "numero_registro_original": number,
                "oferta_asistencial_original": offer, "codigo_ine": code,
                "fuente": HEALTH_FILE, "filas_fuente": _json(offer_group["fila_fuente"].tolist()),
            })
            if not offer.strip():
                issues.append(make_issue(HEALTH_FILE, "oferta_asistencial_ausente", registro=identifier,
                                         campo="oferta_asistecial", fila_fuente=_json(offer_group["fila_fuente"].tolist())))
        rows.append({
            "registro_id": identifier, "numero_registro_original": number, **values,
            "nombre_centro": "", "estado_nombre": "no_publicado_en_fuente",
            "situacion": "", "estado_situacion": "no_publicada_en_fuente",
            "codigo_ine": code, "fuente": HEALTH_FILE, "filas_fuente": _json(source_lines),
            "numero_filas_fuente": len(group), "ofertas_asistenciales_json": _json(offer_values),
            "numero_ofertas_informadas": sum(bool(value.strip()) for value in offer_values),
            "atributos_conflictivos_json": _json(conflicts),
        })
    center_columns = ["registro_id", "numero_registro_original", *HEALTH_FIELDS.values(),
                      "nombre_centro", "estado_nombre", "situacion", "estado_situacion", "codigo_ine",
                      "fuente", "filas_fuente", "numero_filas_fuente", "ofertas_asistenciales_json",
                      "numero_ofertas_informadas", "atributos_conflictivos_json"]
    offer_columns = ["registro_id", "numero_registro_original", "oferta_asistencial_original",
                     "codigo_ine", "fuente", "filas_fuente"]
    centers = attach_municipal_context(pd.DataFrame(rows, columns=center_columns), master)
    offers_frame = attach_municipal_context(pd.DataFrame(offers, columns=offer_columns), master)
    centers = _add_coordinates(_add_address(centers), HEALTH_FILE, issues)
    return centers, offers_frame


def prepare_centers(master: pd.DataFrame, raw_dir: Path):
    """Devuelve tablas, incidencias y cobertura, sin escribir ni tocar fuentes."""
    issues = []
    education_raw = read_csv_strings(Path(raw_dir) / EDUCATION_FILE)
    health_raw = read_csv_strings(Path(raw_dir) / HEALTH_FILE)
    education = _education(education_raw, master, issues)
    health, offers = _health(health_raw, master, issues)
    active = education[education["situacion"].eq("ALTA")].copy().reset_index(drop=True)
    tables = {
        "centros_educativos.csv": education,
        "centros_educativos_alta.csv": active,
        "centros_sanitarios.csv": health,
        "ofertas_sanitarias.csv": offers,
    }
    summary = {
        "educacion": {
            "filas_fuente": len(education_raw), "registros_conservados": len(education),
            "situaciones": education["situacion"].value_counts(dropna=False).to_dict(),
            "registros_alta": len(active),
            "registros_alta_en_elegibles": int(active["elegible_hasta_50000"].fillna(False).sum()),
            "municipios_con_alta_en_elegibles": int(active.loc[active["elegible_hasta_50000"].fillna(False), "codigo_ine"].nunique()),
            "registros_no_mapeados": int(education["codigo_ine"].eq("").sum()),
            "estados_coordenadas": education["estado_coordenadas"].value_counts().to_dict(),
            "estados_coordenadas_alta": active["estado_coordenadas"].value_counts().to_dict(),
            "estados_coordenadas_alta_en_elegibles": active.loc[
                active["elegible_hasta_50000"].fillna(False), "estado_coordenadas"].value_counts().to_dict(),
        },
        "sanidad": {
            "filas_fuente": len(health_raw), "centros_unicos": len(health), "relaciones_oferta": len(offers),
            "centros_en_elegibles": int(health["elegible_hasta_50000"].fillna(False).sum()),
            "centros_no_mapeados": int(health["codigo_ine"].eq("").sum()),
            "centros_con_atributos_conflictivos": int(health["atributos_conflictivos_json"].ne("{}").sum()),
            "estados_coordenadas": health["estado_coordenadas"].value_counts().to_dict(),
            "nombres": "La fuente no publica denominaciones de centros; se conservan vacías.",
        },
    }
    return tables, issues, summary
